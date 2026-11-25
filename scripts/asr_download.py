"""Download official HF ASR weights with bounded HTTP ranges and SHA-256 checks.

Useful when an HTTP proxy buffers multi-gigabyte responses before forwarding any
bytes. This downloads model files only; it never reads or uploads user media.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import hashlib
from pathlib import Path
import shutil
import sys
import time
from urllib.parse import quote
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tingmu.runtime import cache_root, confined_path  # noqa: E402

from huggingface_hub import HfApi, snapshot_download  # noqa: E402


def fetch_range(url, first, last, total):
    request = Request(url, headers={"Range": f"bytes={first}-{last}"})
    with urlopen(request, timeout=90) as response:
        if response.status != 206 or response.headers.get("Content-Range") != f"bytes {first}-{last}/{total}":
            raise ValueError("Server did not honor the exact requested HTTP range")
        expected = last - first + 1
        data = response.read(expected + 1)
        if len(data) != expected:
            raise ValueError("Incomplete HTTP range")
        return data


def download(repo: str, cache: Path, workers: int = 8):
    cache = confined_path(cache_root(), cache)
    info = HfApi().model_info(repo, files_metadata=True)
    snapshot = Path(snapshot_download(repo, revision=info.sha, cache_dir=cache,
                                     allow_patterns=["*.json", "*.txt", "*.tiktoken"]))
    chunk_size = 16 * 1024 * 1024
    for file in sorted((f for f in info.siblings if f.rfilename.endswith(".safetensors")), key=lambda f: f.size):
        expected = file.lfs.sha256
        output = snapshot / file.rfilename
        if output.is_file() and output.stat().st_size == file.size:
            with output.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() == expected:
                    print(f"Verified cached {repo}/{file.rfilename}", flush=True)
                    continue
        blob = snapshot.parent.parent / "blobs" / expected
        parts = cache / "ranges" / expected
        parts.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{quote(repo)}/resolve/{quote(info.sha)}/{quote(file.rfilename)}"
        count = (file.size + chunk_size - 1) // chunk_size
        began = time.perf_counter()

        def fetch(index):
            first, last = index * chunk_size, min(file.size, (index + 1) * chunk_size) - 1
            path = parts / str(index)
            if path.is_file() and path.stat().st_size == last - first + 1:
                return
            for attempt in range(4):
                try:
                    path.write_bytes(fetch_range(url, first, last, file.size))
                    return
                except Exception:
                    if attempt == 3:
                        raise
                    time.sleep(1 + attempt)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = [pool.submit(fetch, i) for i in range(count)]
            for n, future in enumerate(as_completed(pending), 1):
                future.result()
                if n % 10 == 0 or n == count:
                    print(f"{repo}/{file.rfilename}: {n}/{count} ranges, {time.perf_counter()-began:.1f}s", flush=True)
        digest = hashlib.sha256()
        temporary = blob.with_suffix(".assembled")
        with temporary.open("wb") as stream:
            for i in range(count):
                data = (parts / str(i)).read_bytes()
                digest.update(data)
                stream.write(data)
        if digest.hexdigest() != expected:
            raise ValueError(f"SHA256 mismatch for {file.rfilename}; partial data retained for investigation")
        temporary.replace(blob)
        if not output.exists():
            try:
                output.symlink_to(blob)
            except OSError:
                shutil.copyfile(blob, output)
        print(f"Verified {repo}/{file.rfilename}: {expected}", flush=True)
    print(f"READY {snapshot}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", nargs="+", help="Official Hugging Face repository IDs")
    parser.add_argument("--cache", type=Path, default=cache_root() / "qwen-models")
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    for repo in args.repo:
        download(repo, args.cache.resolve(), args.workers)
