"""Install the hash-locked media tools and synthesize a five-second, two-track MKV."""

import argparse
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
from urllib.request import urlopen
from zipfile import ZipFile


def extract_locked_tools(directory):
    lock = Path(__file__).resolve().parents[1] / "src/tingmu/assets/runtime-deps/windows-cp312-x64.json"
    (item,) = json.loads(lock.read_text(encoding="utf-8"))["ffmpeg"]
    archive = directory / "ffmpeg.zip"
    with urlopen(item["url"], timeout=60) as source, archive.open("xb") as dest:
        shutil.copyfileobj(source, dest)
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if archive.stat().st_size != item["size"] or digest != item["sha256"]:
            raise ValueError("FFmpeg download does not match the runtime lock")
    with ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            path = PurePosixPath(entry.filename)
            if path.parent.name == "bin" and (path.name in {"ffmpeg.exe", "ffprobe.exe"} or path.suffix == ".dll"):
                with bundle.open(entry) as source, (directory / path.name).open("xb") as dest:
                    shutil.copyfileobj(source, dest)
    archive.unlink()
    for name in ("ffmpeg.exe", "ffprobe.exe"):
        subprocess.run([directory / name, "-version"], check=True, stdout=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--keep-tools", action="store_true", help="Keep the verified binaries for local tests")
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    github_path = os.environ.get("GITHUB_PATH")
    # A new installation forces an archive hash check even if PATH has media tools.
    # Actions needs the binaries for the subsequent offline test step.
    options = {"prefix": "verified-tools-", "dir": output.parent}
    keep_tools = args.keep_tools or github_path
    work = nullcontext(tempfile.mkdtemp(**options)) if keep_tools else tempfile.TemporaryDirectory(**options)
    with work as directory:
        tools = Path(directory)
        extract_locked_tools(tools)
        subprocess.run([
            tools / "ffmpeg.exe", "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=24",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-f", "lavfi", "-i", "sine=frequency=880:sample_rate=48000",
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-t", "5",
            "-c:v", "mpeg4", "-pix_fmt", "yuv420p", "-c:a", "aac", output,
        ], check=True)
        if github_path:
            with Path(github_path).open("a", encoding="utf-8") as stream:
                print(tools, file=stream)
        if keep_tools:
            print(f"Verified media tools: {tools}")


if __name__ == "__main__":
    main()
