"""Fetch only explicitly named, hash-pinned data files from a pinned PyPI wheel."""

from dataclasses import dataclass
from functools import lru_cache
import hashlib
from pathlib import Path, PurePosixPath
import tempfile
from urllib.error import URLError
from urllib.request import urlopen
from zipfile import ZipFile

from filelock import FileLock, Timeout

from .runtime import cache_root, confined_path
from .task_progress import TransferMeter


@lru_cache(maxsize=32)
def _digest(path, mtime, ctime, inode):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _check_cancelled(cancelled):
    if cancelled():
        raise InterruptedError("模型支持文件下载已暂停")


@dataclass(frozen=True)
class WheelSupport:
    name: str
    cache_group: str
    wheel_url: str
    wheel_bytes: int
    wheel_sha256: str
    members: dict[str, tuple[int, str]]

    def _group_path(self, *parts):
        root = cache_root()
        target = root.joinpath(self.cache_group, *parts)
        if confined_path(root, target) != target:
            raise ValueError("模型支持文件必须位于当前应用的共享缓存目录内")
        return target

    def path(self, *parts):
        return self._group_path(self.name, *parts)

    def inventory(self):
        valid = 0
        for member, (size, digest) in self.members.items():
            path = self.path(PurePosixPath(member).name)
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            if path.is_file() and stat.st_size == size:
                if _digest(path, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino) == digest:
                    valid += size
        complete = valid == sum(size for size, _ in self.members.values())
        return {"repository": self.name, "complete": complete, "installed_bytes": valid,
                "estimated_bytes": self.wheel_bytes, "download_bytes": 0 if complete else self.wheel_bytes}

    def _fetch(self, stage, progress, cancelled):
        wheel = stage / "package.whl"
        digest = hashlib.sha256()
        count = 0
        meter = TransferMeter(self.name, progress)
        meter.total = self.wheel_bytes
        meter.stage("connecting")
        with urlopen(self.wheel_url, timeout=15) as response, wheel.open("xb") as output:
            meter.update(0, self.wheel_bytes, 0, force=True)
            read = getattr(response, "read1", response.read)
            while True:
                _check_cancelled(cancelled)
                chunk = read(128 * 1024)
                if not chunk:
                    break
                count += len(chunk)
                if count > self.wheel_bytes:
                    raise ValueError("模型支持文件下载大小异常")
                output.write(chunk)
                digest.update(chunk)
                meter.update(count, self.wheel_bytes, count, force=count == self.wheel_bytes)
        _check_cancelled(cancelled)
        meter.stage("verifying")
        if count != self.wheel_bytes or digest.hexdigest() != self.wheel_sha256:
            raise ValueError("模型支持文件下载校验失败，请重新下载")
        meter.stage("installing")
        with ZipFile(wheel) as archive:
            for member, (size, expected) in self.members.items():
                _check_cancelled(cancelled)
                info = archive.getinfo(member)
                if info.file_size != size:
                    raise ValueError("模型支持文件大小异常")
                asset = archive.read(info)
                if hashlib.sha256(asset).hexdigest() != expected:
                    raise ValueError("模型支持文件校验失败")
                (stage / PurePosixPath(member).name).write_bytes(asset)

    def install(self, progress, cancelled):
        parent = self._group_path()
        parent.mkdir(parents=True, exist_ok=True)
        lock = FileLock(self._group_path(self.name + ".lock"))
        while True:
            _check_cancelled(cancelled)
            try:
                with lock.acquire(timeout=.4):
                    if self.inventory()["complete"]:
                        return
                    with tempfile.TemporaryDirectory(dir=parent) as temporary:
                        stage = Path(temporary)
                        try:
                            self._fetch(stage, progress, cancelled)
                        except (URLError, TimeoutError) as exc:
                            raise RuntimeError(f"无法下载 {self.name}，请检查 PyPI 网络连接后重试") from exc
                        _check_cancelled(cancelled)
                        self.path().mkdir(exist_ok=True)
                        for member in self.members:
                            name = PurePosixPath(member).name
                            (stage / name).replace(self.path(name))
                    return
            except Timeout:
                progress(f"正在等候其他工作区下载 {self.name}")
