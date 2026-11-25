"""Install and inspect the two optional, checksum-pinned local speech models."""
from __future__ import annotations

import hashlib
from pathlib import Path
import shutil
import tarfile
import tempfile
import urllib.request
from filelock import FileLock, Timeout

from .runtime import confined_path
from .task_progress import TransferMeter

RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
MODELS = [
    ("spleeter", "source-separation-models", "sherpa-onnx-spleeter-2stems-fp16",
     "d54561979bd2e08a51e7dbd99ac36bb47564e089eefd403636dbca93e811bba2",
     {"vocals.fp16.onnx": 19681017, "accompaniment.fp16.onnx": 19681024}),
    ("pyannote", "speaker-segmentation-models", "sherpa-onnx-pyannote-segmentation-3-0",
     "24615ee884c897d9d2ba09bb4d30da6bb1b15e685065962db5b02e76e4996488",
     {"model.onnx": 5992913, "LICENSE": 1061}),
]
ARCHIVE_BYTES = {"spleeter": 35_271_738, "pyannote": 6_958_444}


def planned_download_bytes(cache):
    total = 0
    for family, _, _, digest, files in MODELS:
        archive = confined_path(cache, "speech-downloads/" + family + ".tar.bz2")
        if not _complete(confined_path(cache, "speech-models/" + family), files):
            if not archive.is_file() or _sha256(archive) != digest:
                total += ARCHIVE_BYTES[family]
    return total


def _complete(target, files):
    try:
        return confined_path(target, "LICENSE").is_file() and all(confined_path(target, f).stat().st_size == size
                                                      for f, size in files.items())
    except OSError:
        return False


def speech_model_status(cache: Path) -> dict:
    present = {name: _complete(confined_path(cache, "speech-models/" + name), files)
               for name, _, _, _, files in MODELS}
    return {"bgm": "spleeter-2stems-fp16" if present["spleeter"] else "spectral-background",
            "speaker": "pyannote-segmentation-3.0" if present["pyannote"] else "unavailable"}


def _sha256(path: Path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def install(cache: Path, progress=print, cancelled=None):
    """Publish each verified model atomically. Inference never invokes the installer."""
    def check():
        if cancelled and cancelled():
            raise InterruptedError("已取消语音模型下载")

    check()
    downloads = confined_path(cache, "speech-downloads")
    downloads.mkdir(parents=True, exist_ok=True)
    lock = FileLock(confined_path(downloads, "install.lock"))
    waiting = False
    while True:
        check()
        try:
            lock.acquire(timeout=0.25)
            break
        except Timeout:
            if not waiting:
                progress("另一个工作区正在安装共用语音模型，等待完成")
                waiting = True
    try:
        _install_locked(cache, downloads, progress, check)
    finally:
        lock.release()


def _install_locked(cache, downloads, progress, check):
    for index, (family, tag, name, digest, files) in enumerate(MODELS, 1):
        check()
        target = confined_path(cache, "speech-models/" + family)
        if _complete(target, files):
            progress(f"{family} 模型已安装")
            continue
        archive = confined_path(downloads, family + ".tar.bz2")
        meter = TransferMeter(family, progress)
        meter.stage("checking", package=index, packages=len(MODELS))
        if not archive.is_file() or _sha256(archive) != digest:
            with tempfile.TemporaryDirectory(dir=downloads) as folder:
                partial = Path(folder) / "download"
                total = ARCHIVE_BYTES[family]
                meter.total = total
                meter.stage("connecting")
                with urllib.request.urlopen(f"{RELEASE}{tag}/{name}.tar.bz2", timeout=30) as source:
                    meter.update(0, total, 0, force=True)
                    with partial.open("wb") as output:
                        received = 0
                        read = getattr(source, "read1", source.read)
                        while block := read(128 * 1024):
                            check()
                            output.write(block)
                            received += len(block)
                            meter.update(received, total, received, force=received == total)
                check()
                meter.stage("verifying")
                if _sha256(partial) != digest:
                    raise ValueError(f"{name} SHA256 校验失败")
                partial.replace(archive)
        check()
        meter.stage("installing")
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:bz2") as tar, tempfile.TemporaryDirectory(dir=target) as tmp:
            staged = Path(tmp)
            for asset, size in files.items():
                member = tar.getmember(f"{name}/{asset}")
                if not member.isfile() or member.size != size:
                    raise ValueError(f"模型归档无效：{asset}")
                with tar.extractfile(member) as source, (staged / asset).open("wb") as dest:
                    shutil.copyfileobj(source, dest)
                check()
            if family == "spleeter":
                shutil.copyfile(Path(__file__).with_name("Spleeter-LICENSE.txt"), staged / "LICENSE")
            for asset in staged.iterdir():
                asset.replace(target / asset.name)
        progress(f"{family} 模型已安装")
