"""Install user-selected, hash-locked Windows runtimes inside the portable folder."""

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import subprocess
import tempfile
import time
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen
from zipfile import ZipFile

from filelock import FileLock, Timeout

from . import runtime_install_qwen as qwen
from .runtime import confined_path, installation_root


_TARGETS = {"ffmpeg": "bin", "local-asr": ".cache/runtime-deps/local-asr",
            "whisper-cuda": ".cache/runtime-deps/whisper-cuda/bin", "qwen": "runtime/asr"}
_CHUNK = 256 * 1024


@lru_cache(maxsize=1)
def _locks():
    path = Path(__file__).parent / "assets/runtime-deps/windows-cp312-x64.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _local_uv(root):
    cached = _path(root, ".cache/tools/uv.exe")
    return str(cached) if cached.is_file() else shutil.which("uv")


def _plan(root, kind):
    if kind not in _TARGETS:
        raise ValueError(f"Unknown runtime: {kind}")
    items = [(kind, item) for item in _locks()[kind]]
    if kind == "qwen" and not _local_uv(root):
        items = [("uv", _locks()["uv"][0])] + items
    return items


def download_bytes(kind, root=None):
    return sum(item["size"] for _, item in _plan(Path(root or installation_root()).resolve(), kind))


def _path(root, *parts):
    candidate = root.joinpath(*parts)
    result = confined_path(root, candidate)
    if result != candidate or candidate.is_symlink() or candidate.is_junction():
        raise ValueError("安装路径不能使用链接")
    return result


def _check(cancelled):
    if cancelled():
        raise InterruptedError("运行环境安装已取消")


def _fetch(item, target, offset, total, progress, cancelled):
    digest = hashlib.sha256()
    size = 0
    last = 0
    with urlopen(item["url"], timeout=15) as response, target.open("xb") as output:
        while True:
            _check(cancelled)
            chunk = response.read(_CHUNK)
            if not chunk:
                break
            size += len(chunk)
            if size > item["size"]:
                raise ValueError("运行环境下载大小异常")
            output.write(chunk)
            digest.update(chunk)
            now = time.monotonic()
            if now - last >= 0.15:
                progress(offset + size, total)
                last = now
    if size != item["size"] or digest.hexdigest() != item["sha256"]:
        raise ValueError("运行环境下载校验失败")
    progress(offset + size, total)


def _member(info):
    name = info.filename.removesuffix("/")
    parts = name.split("/")
    if ("\\" in name or ":" in name or any(s in {"", ".", ".."} or s.endswith((" ", "."))
            or PureWindowsPath(s).is_reserved() for s in parts)
            or stat.S_ISLNK(info.external_attr >> 16)):
        raise ValueError("压缩文件包含不安全的路径")
    return PurePosixPath(name)


def _destination(kind, member, filename):
    name = member.name.lower()
    parent = member.parent
    if kind == "uv":
        return "uv.exe" if name == "uv.exe" else None
    if kind == "ffmpeg":
        if parent.name == "bin" and (name in {"ffmpeg.exe", "ffprobe.exe"} or name.endswith(".dll")):
            return member.name
        if name in {"copying", "copying.txt", "license", "license.txt"}:
            return "FFmpeg-LICENSE.txt"
    if kind == "whisper-cuda":
        if parent.name == "bin" and member.parts[0] == "nvidia" and name.endswith(".dll"):
            return member.name
        if ".dist-info" in str(parent) and name.startswith(("license", "nvidia_software_license")):
            return "licenses/" + filename.partition("-")[0] + "-" + member.name
    if kind == "local-asr":
        # Wheels can place additional importable files under *.data/{purelib,platlib}.
        parts = member.parts
        if parts[0].endswith(".data"):
            if len(parts) < 3 or parts[1] not in {"purelib", "platlib"}:
                return None
            member = PurePosixPath(*parts[2:])
        if (name.endswith((".onnx", ".lib", ".pyc", ".pdb"))
                or any(p in {"__pycache__", "tests", "test", "include"} for p in member.parts)):
            return None
        return str(member)
    return None


def _archive(kind, archive_path, payload, cancelled):
    with ZipFile(archive_path) as archive:
        for info in archive.infolist():
            _check(cancelled)
            member = _member(info)
            if info.is_dir() or kind == "qwen":
                continue
            relative = _destination(kind, member, archive_path.name)
            if relative is None:
                continue
            out = _path(payload, relative)
            out.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, out.open("xb") as dest:
                while chunk := source.read(_CHUNK):
                    _check(cancelled)
                    dest.write(chunk)


def _offline_uv(uv, python, wheels, target, temporary, cancelled):
    # Never invoke a resolver, sdist build or package script. All arguments are
    # locally verified wheel paths. The child gets no host uv/pip configuration.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("UV_", "PIP_", "PYTHON"))}
    for key in ("TEMP", "TMP", "TMPDIR", "XDG_CACHE_HOME", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR"):
        env[key] = str(temporary)
    env.update(UV_PYTHON_DOWNLOADS="never", UV_OFFLINE="1", UV_NO_PROGRESS="1")
    command = [uv, "--no-config", "--no-cache", "pip", "install", "--offline", "--no-index", "--no-build",
               "--no-deps", "--link-mode", "copy", "--python", str(python), "--target", str(target),
               *map(str, wheels)]
    with _path(temporary, "uv.log").open("w+b") as log:
        with subprocess.Popen(command, cwd=temporary, env=env, stdin=subprocess.DEVNULL,
                              stdout=log, stderr=log,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as process:
            try:
                while process.poll() is None:
                    _check(cancelled)
                    time.sleep(.15)
                if process.returncode:
                    log.seek(max(0, log.tell() - 1400))
                    raise RuntimeError("uv 离线安装失败：" + log.read().decode("utf-8", "replace"))
            finally:
                if process.poll() is None:
                    process.kill()


def _verify(root, kind, stage, cancelled):
    from .runtime_deps import verify_candidate

    selected = stage / "python.exe" if kind == "qwen" else stage
    result = verify_candidate(root, kind, selected, cancelled=cancelled)
    if not result["ready"]:
        raise RuntimeError("运行环境验证失败：" + str(result.get("detail", kind)))


def _keep_other_tools(old, staged, cancelled):
    # bin/ can already hold tools supplied by the user. Keep existing siblings.
    for item in old.iterdir():
        _check(cancelled)
        original = _path(old, item.name)
        target = _path(staged, item.name)
        if original.is_dir():
            target.mkdir(exist_ok=True)
            _keep_other_tools(original, target, cancelled)
        elif not target.exists():
            shutil.copy2(original, target)


def _publish(kind, stage, target, backup, cancelled):
    if kind == "ffmpeg" and target.is_dir():
        _keep_other_tools(target, stage, cancelled)
    _check(cancelled)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.rename(backup)
    try:
        stage.rename(target)
    except BaseException:
        if backup.exists():
            backup.rename(target)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def install(root, kind, progress, cancelled):
    """Install one explicitly selected component. The callback receives byte counts."""
    root = Path(root).resolve()
    if kind not in _TARGETS:
        raise ValueError(f"Unknown runtime: {kind}")
    target = _path(root, _TARGETS[kind])
    work = _path(root, ".cache/runtime-deps")
    _check(cancelled)
    work.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(_path(work, kind + ".lock")))
    while True:
        _check(cancelled)
        try:
            lock.acquire(timeout=.25)
            break
        except Timeout:
            continue
    try:
        backup = _path(work, kind + ".previous")
        if backup.exists() and not target.exists():
            backup.rename(target)
        elif backup.exists():
            shutil.rmtree(backup)
        if target.is_dir():
            try:
                _verify(root, kind, target, cancelled)
            except InterruptedError:
                raise
            except (OSError, RuntimeError, ValueError):
                pass
            else:
                progress(0, 0)
                return target
        with tempfile.TemporaryDirectory(prefix="install-" + kind + "-", dir=work) as temp:
            stage = _path(work, Path(temp).name)
            payload = _path(stage, "payload")
            payload.mkdir()
            if kind == "qwen":
                packages = qwen.prepare(root, payload)
            plan = _plan(root, kind)
            total = sum(item["size"] for _, item in plan)
            progress(0, total)
            offset = 0
            wheels = []
            for category, item in plan:
                _check(cancelled)
                filename = unquote(urlsplit(item["url"]).path.rsplit("/", 1)[-1])
                artifact = _path(stage, filename)
                _fetch(item, artifact, offset, total, progress, cancelled)
                offset += item["size"]
                _archive(category, artifact, stage if category == "uv" else payload, cancelled)
                if category == "qwen":
                    wheels.append(artifact)
                else:
                    artifact.unlink()
                if category == "uv":
                    uv = _path(root, ".cache/tools/uv.exe")
                    uv.parent.mkdir(parents=True, exist_ok=True)
                    _check(cancelled)
                    _path(stage, "uv.exe").replace(uv)
            if kind == "qwen":
                _offline_uv(_local_uv(root), payload / "python.exe", wheels, packages, stage, cancelled)
                qwen.finish(packages)
            _check(cancelled)
            if kind == "ffmpeg" and not (payload / "FFmpeg-LICENSE.txt").is_file():
                raise ValueError("FFmpeg 安装包缺少许可证")
            _verify(root, kind, payload, cancelled)
            if kind == "qwen":
                qwen.configure(root, payload, target)
            _publish(kind, payload, target, backup, cancelled)
            return target
    finally:
        lock.release()
