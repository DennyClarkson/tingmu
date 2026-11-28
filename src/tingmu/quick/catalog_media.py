"""Download verified catalog media and install its matching captions."""
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from tingmu.media import _executable, _run
from tingmu.runtime import confined_path

ASSETS = Path(__file__).with_name("catalog_assets")


def install_bundled_lesson(source, item, media_path, store, language):
    from tingmu.models import StudyDocument
    from tingmu.storage import media_fingerprint

    lesson = confined_path(source, item["id"] + ".lesson.json")
    if not lesson.is_file() or store.material_paths(str(media_path), language)[0].exists():
        return
    document = StudyDocument.from_dict(json.loads(lesson.read_bytes()))
    if document.language != language:
        return
    if (not document.cues or document.metadata.get("stage") != "ready"
            or document.metadata.get("media_fingerprint") != {"size": item["bytes"], "mtime_ns": item["mtime_ns"]}):
        raise ValueError("随包学习资料与所选视频不匹配。")
    # The download verified the video's digest. Copies and ZIP extraction can change its mtime.
    document.media_path = str(media_path)
    document.metadata["media_fingerprint"] = media_fingerprint(media_path)
    store.save_document(document, only_if_missing=True)


def catalog_path(root, item):
    return confined_path(root, Path("downloads") / item["id"] / (item["title"] + item.get("suffix", ".mp4")))


def fetch_catalog_video(root, item, cancelled):
    dest = catalog_path(root, item)
    dest.parent.mkdir(parents=True, exist_ok=True)
    caption = confined_path(root, dest.with_suffix("." + item["language"] + ".vtt"))
    receipt = confined_path(root, dest.with_suffix(".sha256"))
    local = urlsplit(item["url"]).hostname == "127.0.0.1" and urlsplit(item["url"]).scheme == "http"
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        staging = Path(tmp)
        cached = False
        if dest.is_file() and receipt.is_file():
            with dest.open("rb") as stream:
                cached = receipt.read_text("ascii").strip() == item["sha256"] + " " + hashlib.file_digest(stream, "sha256").hexdigest()
        if not cached:
            part = staging / "payload"
            digest = hashlib.sha256()
            from .demo_source import open_local_url
            request = Request(item["url"], headers={"User-Agent": "Tingmu/1.0"})
            with (open_local_url(request) if local else urlopen(request, timeout=30)) as response:
                if not local and not response.geturl().startswith("https://"):
                    raise ValueError("片源返回了不安全的下载地址。")
                with part.open("wb") as out:
                    while block := response.read(256 * 1024):
                        if cancelled():
                            raise InterruptedError("下载已取消。")
                        digest.update(block)
                        out.write(block)
                        if out.tell() > item["bytes"]:
                            raise ValueError("片源文件大小已变化。")
            if digest.hexdigest() != item["sha256"] or part.stat().st_size != item["bytes"]:
                raise ValueError("下载文件校验失败，请重试。")
            output = part
            if item.get("card"):
                output = staging / "clip.mp4"
                _run([_executable("ffmpeg"), "-nostdin", "-loglevel", "error", "-loop", "1",
                      "-framerate", "8", "-i", str(confined_path(ASSETS, item["card"])), "-i", str(part),
                      "-t", "8", "-c:v", "mpeg4", "-q:v", "3", "-pix_fmt", "yuv420p",
                      "-c:a", "aac", "-af", "apad", "-movflags", "+faststart", str(output)], cancelled, 60)
            with output.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            if local and dest.exists():
                # An already installed lesson is keyed by the video's mtime as well as its size.
                with dest.open("rb") as src:
                    if hashlib.file_digest(src, "sha256").hexdigest() != checksum:
                        raise ValueError("同名本地视频内容不同，请先移走或重命名该文件。")
            else:
                if local and item.get("mtime_ns"):
                    os.utime(output, ns=(output.stat().st_atime_ns, item["mtime_ns"]))
                output.replace(dest)
            stamp = staging / "receipt"
            stamp.write_text(item["sha256"] + " " + checksum, "ascii")
            stamp.replace(receipt)
        if not local and not caption.exists():
            temp_caption = staging / "captions"
            shutil.copyfile(confined_path(ASSETS, item["id"] + ".webvtt"), temp_caption)
            temp_caption.replace(caption)
    return str(dest)
