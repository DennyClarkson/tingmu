"""Inspect and populate the same local model caches that the ASR worker reads."""

import json
import re
import subprocess
import sys
import time

from filelock import FileLock, Timeout

from .media import QWEN_MODELS, _qwen_python
from .runtime import cache_root, confined_path

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
    installed = all(row["complete"] for row in rows)
    return {"model": model, "installed": installed,
            "cache_state": "complete" if installed else "incomplete" if any(
                row["installed_bytes"] or row["complete"] for row in rows) else "missing",
            "installed_bytes": sum(row["installed_bytes"] for row in rows),
            "download_bytes": sum(row["download_bytes"] for row in rows),
            "estimated_download_bytes": sum(row["estimated_bytes"] for row in rows),
            "repositories": rows}


def model_status(model):
    try:
        if model.startswith("local:"):
            from .local_whisper import resolve_local_model
            try:
                resolve_local_model(model)
            except ValueError as exc:
                return str(exc)
            return "已就绪 · 使用共享目录中的本地模型"
        cache, repositories = _assets(model)
        if model in QWEN_MODELS:
            _qwen_python()
        if not all(_cached(cache, repo, model) for repo in repositories):
            return "尚未检测到完整模型"
        return "已检测到本地模型文件"
    except (OSError, KeyError, ValueError):
        return "尚未检测到完整模型"
    except RuntimeError as exc:
        return str(exc)


def _download_repo(model, repo, progress, cancelled):
    argv = [sys.executable, "-m", "tingmu.workspace_models", model, repo]
    started = time.monotonic()
    with subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
        try:
            while child.poll() is None:
                if cancelled():
                    raise InterruptedError("模型下载已暂停，可重新下载以继续")
                progress(f"正在下载 {repo} · {int(time.monotonic() - started)} 秒")
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
        if child.returncode:
            raise RuntimeError(f"下载 {repo} 失败，请检查 Hugging Face 网络连接和缓存盘空间后重试")


def download_model(model, progress, cancelled):
    cache, repositories = _assets(model)
    if model in QWEN_MODELS:
        _qwen_python()
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


if __name__ == "__main__":
    from huggingface_hub import snapshot_download
    selected, repository = sys.argv[1:]
    cache, allowed = _assets(selected)
    if repository not in allowed:
        raise ValueError("模型仓库无效")
    if selected in QWEN_MODELS:
        from .media import _qwen_snapshot
        _qwen_snapshot(repository, str(cache))
    else:
        snapshot_download(repository, cache_dir=cache, allow_patterns=[
            "config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"])
