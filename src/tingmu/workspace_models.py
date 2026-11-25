"""Inspect and populate the same local model caches that the ASR worker reads."""

import json
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading

from filelock import FileLock, Timeout

from . import qwen_support, whisper_support
from .media import QWEN_MODELS, _qwen_python
from .runtime import cache_root, confined_path, is_clean_install, portable_root
from .task_progress import TransferMeter

WHISPER_MODELS = {
    "small": "Systran/faster-whisper-small",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
ALIGNER = "Qwen/Qwen3-ForcedAligner-0.6B"
# Planning estimates, not a remote manifest. The advisor never queries the Hub.
REPOSITORY_BYTES = {
    WHISPER_MODELS["small"]: 510_000_000,
    WHISPER_MODELS["large-v3"]: 3_100_000_000,
    WHISPER_MODELS["large-v3-turbo"]: 1_650_000_000,
    QWEN_MODELS["qwen3-asr-0.6b"]: 1_900_000_000,
    QWEN_MODELS["qwen3-asr-1.7b"]: 4_750_000_000,
    ALIGNER: 1_900_000_000,
}


def _assets(model):
    if model in QWEN_MODELS:
        return confined_path(cache_root(), "qwen-models"), [QWEN_MODELS[model], ALIGNER]
    if model in WHISPER_MODELS:
        return confined_path(cache_root(), "models"), [WHISPER_MODELS[model]]
    raise ValueError("请选择可下载的本地识别模型")


def _support(model):
    if not is_clean_install():
        return None
    if model in QWEN_MODELS:
        return qwen_support
    if model in WHISPER_MODELS:
        return whisper_support
    return None


def _cached(cache, repo, model):
    try:
        cache = cache.resolve()
        hub = confined_path(cache, "models--" + repo.replace("/", "--"))
        revision = confined_path(cache, hub / "refs/main").read_text().strip()
        if not re.fullmatch("[0-9a-fA-F]{40}", revision):
            return False
        snapshot = confined_path(cache, hub / "snapshots" / revision)
        if model in QWEN_MODELS:
            index = confined_path(cache, snapshot / "model.safetensors.index.json")
            weights = set(json.loads(index.read_bytes())["weight_map"].values()) if index.is_file() else {"model.safetensors"}
            if not weights or any(not isinstance(name, str) or not name for name in weights):
                return False
            tokenizer = {"tokenizer.json"} if (snapshot / "tokenizer.json").is_file() else {"vocab.json", "merges.txt"}
            files = weights | tokenizer | {"config.json", "tokenizer_config.json", "preprocessor_config.json"}
        else:
            files = {"config.json", "model.bin", "tokenizer.json"}
        return all((snapshot / file).resolve().is_relative_to(cache)
                   and (snapshot / file).is_file() and (snapshot / file).stat().st_size > 0 for file in files)
    except (OSError, KeyError, ValueError, TypeError, RuntimeError, AttributeError):
        return False


def _directory_bytes(folder, root):
    """Count physical files once, including partial blobs, without following escapes."""
    total, seen = 0, set()
    try:
        if not folder.resolve().is_relative_to(root.resolve()):
            return 0
        for entry in folder.rglob("*"):
            resolved = entry.resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                continue
            stat = resolved.stat()
            identity = (stat.st_dev, stat.st_ino) if stat.st_ino else str(resolved)
            if identity not in seen:
                total += stat.st_size
                seen.add(identity)
    except (OSError, RuntimeError):
        pass
    return total


def model_inventory(model):
    """Read shared installation cache only; no downloads or runtime imports.

    download_bytes is a conservative estimate for incomplete repositories, not
    an exact remaining transfer size. A complete shared aligner costs zero again.
    """
    if model.startswith("local:"):
        from .local_whisper import resolve_local_model
        try:
            folder = resolve_local_model(model)
            return {"model": model, "installed": True, "cache_state": "complete",
                    "installed_bytes": _directory_bytes(folder, cache_root()),
                    "download_bytes": 0, "estimated_download_bytes": 0, "repositories": []}
        except (ValueError, OSError, RuntimeError):
            return {"model": model, "installed": False, "cache_state": "incomplete",
                    "installed_bytes": 0, "download_bytes": 0, "estimated_download_bytes": 0,
                    "repositories": []}
    cache, repos = _assets(model)
    rows = []
    for repo in repos:
        folder = cache / ("models--" + repo.replace("/", "--"))
        complete = _cached(cache, repo, model)
        size = _directory_bytes(folder, cache)
        rows.append({"repository": repo, "complete": complete, "installed_bytes": size,
                     "estimated_bytes": REPOSITORY_BYTES[repo],
                     "download_bytes": 0 if complete else REPOSITORY_BYTES[repo]})
    support = _support(model)
    if support:
        rows.append(support.inventory())
    installed = all(row["complete"] for row in rows)
    return {"model": model, "installed": installed,
            "cache_state": "complete" if installed else "incomplete" if any(
                row["installed_bytes"] or row["complete"] for row in rows) else "missing",
            "installed_bytes": sum(row["installed_bytes"] for row in rows),
            "download_bytes": sum(row["download_bytes"] for row in rows),
            "estimated_download_bytes": sum(row["estimated_bytes"] for row in rows),
            "repositories": rows}


@dataclass(frozen=True)
class ModelReadiness:
    """Machine-readable readiness independent of the interface wording."""

    ready: bool
    code: str
    message: str


def _require_engine(model):
    if model in QWEN_MODELS:
        _qwen_python()
    elif portable := portable_root():
        from .runtime_deps import require
        require(portable, "local-asr")


def weight_readiness(model) -> ModelReadiness:
    """Check the installed files without importing or probing the inference runtime."""
    try:
        if model.startswith("local:"):
            from .local_whisper import resolve_local_model
            try:
                resolve_local_model(model)
            except ValueError as exc:
                return ModelReadiness(False, "model_missing", str(exc))
            return ModelReadiness(True, "ready", "已就绪 · 使用共享目录中的本地模型")
        cache, repositories = _assets(model)
        for repo in repositories:
            if not _cached(cache, repo, model):
                name = repo.split("/")[-1]
                return ModelReadiness(False, "model_missing", f"缺少完整的 {name} 模型权重，请到工作区语音识别设置下载。")
        support = _support(model)
        if support and not support.inventory()["complete"]:
            return ModelReadiness(False, "support_missing", "识别模型支持文件不完整，请到工作区语音识别设置补全下载。")
        return ModelReadiness(True, "ready", "已检测到本地模型文件")
    except (OSError, KeyError, ValueError):
        return ModelReadiness(False, "model_missing", "尚未检测到完整模型")


def model_readiness(model) -> ModelReadiness:
    try:
        _require_engine(model)
    except RuntimeError as exc:
        return ModelReadiness(False, "runtime_missing", str(exc))
    return weight_readiness(model)


def model_status(model):
    """Compatibility display adapter; decisions must use model_readiness()."""
    if portable := portable_root():
        from .runtime_deps import last_probe
        engine = "qwen" if model in QWEN_MODELS else "local-asr"
        state = last_probe(portable, engine)
        if state is None:
            return "请在「运行环境与依赖管理」中检测识别运行环境"
        if not state["ready"]:
            return state["detail"]
    return model_readiness(model).message


def _download_python():
    # The Windows venv redirector spawns a second process. Launch CPython with
    # the same venv identity so killing the monitored PID closes the Hub files.
    if os.name == "nt" and sys.prefix != sys.base_prefix:
        return [sys._base_executable, "-B"], {**os.environ, "__PYVENV_LAUNCHER__": sys.executable}
    return [sys.executable, "-B"], None


def _download_repo(model, repo, progress, cancelled):
    meter = TransferMeter(repo, progress)
    progress(f"正在核对 {repo} 的文件和本地缓存")
    with tempfile.TemporaryDirectory(dir=cache_root()) as temp:
        events = os.path.join(temp, "transfer.jsonl")
        ledger = Path(temp) / "partial-files.txt"
        python, environment = _download_python()
        argv = [*python, "-m", "tingmu.workspace_models", model, repo, str(ledger)]
        with open(events, "wb") as writer, open(events, "rb") as reader, subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=writer, stderr=subprocess.DEVNULL, env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as child:
            partial = b""
            sample = None
            try:
                while True:
                    finished = child.poll() is not None
                    lines = (partial + reader.read()).split(b"\n")
                    partial = lines.pop()
                    if lines:
                        sample = json.loads(lines[-1])
                    if sample:
                        meter.update(*sample, force=finished)
                    if cancelled():
                        raise InterruptedError("模型下载已暂停，可重新下载以继续")
                    if finished:
                        break
                    try:
                        child.wait(timeout=.4)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                if child.returncode and ledger.exists():
                    cache, _ = _assets(model)
                    blobs = confined_path(cache, "models--" + repo.replace("/", "--") + "/blobs")
                    for name in ledger.read_text("ascii").splitlines():
                        if re.fullmatch(r"[a-f0-9]{40,64}\.[a-f0-9]{8}\.incomplete", name):
                            path = blobs / name
                            if not _linked(path):
                                path.unlink(missing_ok=True)
            if child.returncode:
                raise RuntimeError(f"下载 {repo} 失败，请检查 Hugging Face 网络连接和缓存盘空间后重试")


def download_model(model, progress, cancelled):
    cache, repositories = _assets(model)
    _require_engine(model)
    support = _support(model)
    if support:
        support.install(progress, cancelled)
    locks = cache / ".download-locks"
    if _linked(locks):
        raise ValueError("模型缓存必须位于当前应用目录内")
    locks.mkdir(parents=True, exist_ok=True)
    if _linked(locks):
        raise ValueError("模型缓存必须位于当前应用目录内")
    for repo in repositories:
        repo_key = "models--" + repo.replace("/", "--")
        lock_path = locks / (repo_key + ".lock")
        hub = cache / repo_key
        if (_linked(lock_path) or _linked(hub) or _linked(cache / ".locks")
                or _linked(cache / ".locks" / repo_key)
                or any(not entry.resolve().is_relative_to(cache) for entry in hub.rglob("*"))):
            raise ValueError("模型缓存不能链接至应用目录外")
        lock = FileLock(lock_path)
        while not cancelled():
            try:
                with lock.acquire(timeout=.4):
                    if not _cached(cache, repo, model):
                        _download_repo(model, repo, progress, cancelled)
                        if not _cached(cache, repo, model):
                            raise RuntimeError(f"{repo} 模型文件不完整，请重新下载")
                    break
            except Timeout:
                progress(f"正在等候其他工作区下载 {repo}")
        else:
            raise InterruptedError("模型下载已暂停")
    return model


def _linked(path):
    # Windows resolve(strict=False) races with another process creating a missing
    # parent: fallback resolution can produce a different path. These paths have
    # a canonical cache parent and fixed single components, so inspect reparse
    # points directly before/after mkdir instead of comparing speculative paths.
    return path.is_symlink() or path.is_junction()


def _fetch_snapshot(selected, repository):
    from huggingface_hub import snapshot_download
    from tqdm import tqdm
    cache, allowed = _assets(selected)
    if repository not in allowed:
        raise ValueError("模型仓库无效")
    patterns = (["*.json", "*.safetensors", "*.txt", "*.tiktoken"] if selected in QWEN_MODELS else
                ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"])
    plan = snapshot_download(repository, cache_dir=cache, allow_patterns=patterns, dry_run=True)
    if not plan:
        raise ValueError("模型仓库未返回文件清单")
    total = sum(f.file_size for f in plan) if all(f.file_size is not None for f in plan) else 0
    cached = sum(f.file_size or 0 for f in plan if not f.will_download)
    completed, received = cached, 0
    lock = threading.Lock()

    class TransferBar(tqdm):
        def __init__(self, *args, **kwargs):
            desc = kwargs.get("desc", "")
            self.network = desc == "Downloading bytes"
            self.file_bytes = desc.startswith("Reconstructing")
            super().__init__(*args, **kwargs)

        def display(self, *args, **kwargs):
            pass

        def update(self, n=1):
            nonlocal completed, received
            with lock:
                super().update(n)
                if self.network:
                    received = max(0, received + n)
                if self.file_bytes:
                    completed = max(cached, completed + n)
    stop = threading.Event()

    def publish():
        while not stop.wait(.25):
            with lock:
                print(json.dumps([completed, total, received]), flush=True)

    print(json.dumps([cached, total, 0]), flush=True)
    publisher = threading.Thread(target=publish, daemon=True)
    publisher.start()
    try:
        snapshot_download(repository, revision=plan[0].commit_hash, cache_dir=cache,
                          allow_patterns=patterns, tqdm_class=TransferBar)
    finally:
        stop.set()
        publisher.join()
    print(json.dumps([total or completed, total, received]), flush=True)


if __name__ == "__main__":
    from huggingface_hub import constants
    # The default HTTP callback fires only every 10 MiB. Smaller reads expose the
    # first transfer on slower links; Xet continues to use its own native events.
    constants.DOWNLOAD_CHUNK_SIZE = min(constants.DOWNLOAD_CHUNK_SIZE, 128 * 1024)
    model, repo, ledger = sys.argv[1:]
    cache, _ = _assets(model)
    blob_dir = confined_path(cache, "models--" + repo.replace("/", "--") + "/blobs")
    with confined_path(cache_root(), ledger).open("x", encoding="ascii") as journal:
        def record_partial(event, args):
            # Hub uses process-unique temporary names. Record only files opened
            # by this worker so cancellation never removes another caller's file.
            if event == "open" and isinstance(args[0], str) and args[0].endswith(".incomplete") and args[1] == "w":
                path = Path(args[0])
                if path.parent.resolve() == blob_dir:
                    journal.write(path.name + "\n")
                    journal.flush()
        sys.addaudithook(record_partial)
        _fetch_snapshot(model, repo)
