"""Local, isolated FFmpeg extraction of timed text and transparent subtitle images."""

from __future__ import annotations

import base64
from bisect import bisect_right
from contextlib import ExitStack
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
from itertools import groupby
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from tempfile import TemporaryDirectory

from PySide6.QtGui import QImage

from .runtime import cache_root, confined_path, installation_root, portable_root


PAGE_MS = 30000
MAX_BYTES = 16 * 1024 * 1024
BITMAP_CODECS = {"dvd_subtitle", "hdmv_pgs_subtitle", "dvb_subtitle", "xsub"}


class SubtitleError(ValueError):
    """A short, user-facing decoder error."""


@dataclass(frozen=True)
class BitmapFrame:
    url: str
    bounds: dict[str, float]


class BitmapPage:
    """Keep decoded geometry on the page and materialize URLs only around playback."""
    def __init__(self, scratch, times, files):
        self._scratch = scratch
        self.times = times
        self.files = files
        self._url = lru_cache(maxsize=4)(self._read_url)

    @staticmethod
    def _read_url(path):
        return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")

    def at(self, position):
        index = bisect_right(self.times, position) - 1
        if index < 0 or not self.files[index]:
            return ""
        path, bounds = self.files[index]
        return BitmapFrame(self._url(path), bounds)

    def close(self):
        self._url.cache_clear()
        self._scratch.cleanup()


@dataclass(frozen=True)
class Events:
    times: tuple[int, ...]
    values: tuple[str | BitmapFrame, ...]

    def at(self, position):
        index = bisect_right(self.times, position) - 1
        return self.values[index] if index >= 0 else ""


@dataclass(frozen=True)
class BitmapTrack:
    width: int
    height: int
    packets: tuple[int, ...]
    native_canvas: bool = True
    last_end: int | None = None


def _binary(name):
    suffix = ".exe" if os.name == "nt" else ""
    release = portable_root()
    folder = release or installation_root() / "dist/Tingmu"
    local = confined_path(folder, f"bin/{name}{suffix}")
    if local.is_file():
        return str(local)
    executable = None if release else shutil.which(name)
    if not executable:
        raise SubtitleError("缺少 FFmpeg 工具，请检查安装目录。")
    return executable


def _run(args, folder, stop, *, images=False):
    """Keep the process and its bounded scratch files inside this extraction job."""
    if stop.is_set():
        raise InterruptedError()
    with (folder / "stdout").open("w+b") as output:
        process = subprocess.Popen(args, cwd=folder, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            seen = set()
            published_bytes = 0
            deadline = time.monotonic() + 90
            while process.poll() is None:
                if stop.wait(0.05):
                    raise InterruptedError()
                if time.monotonic() > deadline:
                    raise SubtitleError("内嵌字幕解码超时。")
                if os.fstat(output.fileno()).st_size > MAX_BYTES:
                    raise SubtitleError("内嵌字幕数据超过单次读取上限。")
                if images:
                    # image2 publishes complete PNGs by rename. Only the current
                    # temporary file can grow between consecutive polls.
                    in_progress = 0
                    for entry in folder.iterdir():
                        if entry.name in seen:
                            continue
                        if entry.suffix == ".png":
                            published_bytes += entry.stat().st_size
                            seen.add(entry.name)
                        elif entry.suffix == ".tmp":
                            try:
                                in_progress += entry.stat().st_size
                            except FileNotFoundError:
                                pass  # Renamed between the listing and the stat.
                    if published_bytes + in_progress > MAX_BYTES:
                        raise SubtitleError("此段图形字幕过于密集。")
            if process.returncode:
                raise SubtitleError("无法解码这条内嵌字幕。")
            output.seek(0)
            data = output.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise SubtitleError("内嵌字幕数据超过单次读取上限。")
            return data
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def _scratch():
    root = confined_path(cache_root(), "tmp")
    root.mkdir(exist_ok=True, parents=True)
    return TemporaryDirectory(prefix="subtitle-", dir=root)


def text_events(data):
    import pysubs2
    cues = pysubs2.SSAFile.from_string(data.decode("utf-8-sig"), format_="srt")
    if len(cues) > 100000:
        raise SubtitleError("字幕条目超出单轨读取上限。")
    changes = []
    for index, cue in enumerate(cues):
        if cue.end > cue.start and cue.plaintext:
            changes.extend([(cue.start, index, cue.plaintext), (cue.end, index, "")])
    active = {}
    times, values = [], []
    total_chars = 0
    for stamp, group in groupby(sorted(changes), key=lambda change: change[0]):
        for _, index, value in group:
            if value:
                active[index] = value
            else:
                active.pop(index, None)
        if len(active) > 128:
            raise SubtitleError("同时显示的字幕条目过多。")
        visible = "\n".join(active.values())
        total_chars += len(visible)
        if total_chars > MAX_BYTES:
            raise SubtitleError("内嵌字幕文本超过读取上限。")
        times.append(stamp)
        values.append(visible)
    return Events(tuple(times), tuple(values))


def load_track(media, ordinal, stop):
    with _scratch() as temp:
        folder = Path(temp)
        selector = f"s:{ordinal}"
        probe = [_binary("ffprobe"), "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", selector]
        data = _run([*probe, "-show_entries", "stream=codec_name,width,height:format=start_time",
                     "-of", "json", media], folder, stop)
        info = json.loads(data)
        if not info.get("streams"):
            raise SubtitleError("找不到所选内嵌字幕轨。")
        stream = info["streams"][0]
        if stream.get("codec_name") not in BITMAP_CODECS:
            data = _run([_binary("ffmpeg"), "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe", "-i", media,
                         "-map", f"0:{selector}", "-c:s", "srt", "-f", "srt", "pipe:1"], folder, stop)
            return text_events(data)
        # The subtitle plane can be smaller than the movie (e.g. 1080p VobSub
        # on 2160p video). Input canvas_size stops FFmpeg inflating that plane.
        width, height = int(stream.get("width") or 0), int(stream.get("height") or 0)
        native_canvas = width > 0 and height > 0
        if not native_canvas:
            video = _run([_binary("ffprobe"), "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "json", media], folder, stop)
            dimensions = next(iter(json.loads(video).get("streams", [])), {})
            width, height = dimensions.get("width", 720), dimensions.get("height", 576)
        if not (0 < width <= 8192 and 0 < height <= 8192 and width * height <= 16777216):
            raise SubtitleError("无法显示此图形字幕的画布尺寸。")
        # Knowing the preceding packet time preserves long captions on random seeks.
        packets = _run([*probe, "-show_entries", "packet=pts_time,duration_time", "-of", "csv=p=0", media], folder, stop)
        origin = float(info.get("format", {}).get("start_time") or 0)
        stamps = {}
        for line_no, line in enumerate(BytesIO(packets)):
            if line_no >= 250000:
                raise SubtitleError("图形字幕条目超出单轨读取上限。")
            try:
                pts, _, duration = line.strip().partition(b",")
                stamp = round((float(pts) - origin) * 1000)
                stamps[stamp] = None
                if stream.get("codec_name") == "dvd_subtitle" and duration not in (b"", b"N/A"):
                    length = round(float(duration) * 1000)
                    if length > 0:
                        stamps[stamp] = stamp + length
            except ValueError:
                continue
        times = tuple(sorted(stamps))
        return BitmapTrack(width, height, times, native_canvas, stamps[times[-1]] if times else None)


def load_page(media, ordinal, track, page, stop):
    start = page * PAGE_MS
    end = start + PAGE_MS
    if track.last_end is not None and start >= track.last_end:
        return Events((), ())
    before = bisect_right(track.packets, start)
    if not before and (not track.packets or track.packets[0] >= end):
        return Events((), ())
    # Begin before the previous display event to recover a caption already on
    # screen at the page boundary. No picture or audio track is decoded.
    seek = max(0, (track.packets[before - 1] if before else start) - 1000)
    duration = (end - seek) / 1000
    scale = min(1, 1280 / track.width, 1080 / track.height)
    width, height = max(1, round(track.width * scale)), max(1, round(track.height * scale))
    # DVB/PGS may announce the true source canvas in the first display event.
    # In that case FFmpeg must be free to resize its input plane dynamically.
    canvas_args = ["-canvas_size", f"{track.width}x{track.height}"] if track.native_canvas else []
    with ExitStack() as cleanup:
        scratch = _scratch()
        folder = Path(cleanup.enter_context(scratch))
        _run([_binary("ffmpeg"), "-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe",
              "-ss", f"{seek / 1000:.3f}",
              "-t", f"{duration + 1:.3f}", *canvas_args,
              "-i", media, "-filter_complex_threads", "1", "-filter_complex",
              f"[0:s:{ordinal}]scale={width}:{height},format=rgba[caption]", "-map", "[caption]",
              "-t", f"{duration:.3f}", "-fps_mode", "passthrough", "-enc_time_base", "1/1000",
              "-threads", "1", "-frames:v", "512", "-frame_pts", "1", "-atomic_writing", "1", "-y", "%012d.png"],
             folder, stop, images=True)
        files = sorted(folder.glob("*.png"))
        if len(files) >= 511 or sum(p.stat().st_size for p in files) > MAX_BYTES:
            raise SubtitleError("此段图形字幕过于密集。")
        import numpy as np
        times, values = [], []
        for path in files:
            if stop.is_set():
                raise InterruptedError()
            stamp = int(path.stem) + seek
            if stamp >= end:
                break
            alpha = QImage(str(path)).convertToFormat(QImage.Format.Format_Alpha8)
            if alpha.isNull():
                raise SubtitleError("无法读取内嵌字幕画面。")
            value = ""
            pixels = np.frombuffer(alpha.constBits(), dtype=np.uint8).reshape(height, alpha.bytesPerLine())[:, :width]
            rows = np.flatnonzero(pixels.any(axis=1))
            if rows.size:
                cols = np.flatnonzero(pixels.any(axis=0))
                bounds = {"x": int(cols[0]) / width, "y": int(rows[0]) / height,
                          "width": int(cols[-1] - cols[0] + 1) / width,
                          "height": int(rows[-1] - rows[0] + 1) / height}
                value = (path, bounds)
            times.append(stamp)
            values.append(value)
        page = BitmapPage(scratch, tuple(times), tuple(values))
        cleanup.pop_all()  # The page owns these files until eviction or track close.
        return page
