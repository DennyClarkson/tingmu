"""Download official HF ASR weights with bounded HTTP ranges and SHA-256 checks.

Useful when an HTTP proxy buffers multi-gigabyte responses before forwarding any
bytes. This downloads model files only; it never reads or uploads user media.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import hashlib
from pathlib import Path
import shutil
import threading
import time

import httpx
from huggingface_hub import HfApi, snapshot_download


def download(repo: str, cache: Path, workers: int = 8):
    info = HfApi().model_info(repo, files_metadata=True)
    snapshot = Path(snapshot_download(repo, revision=info.sha, cache_dir=cache,
                                     allow_patterns=["*.json", "*.txt", "*.tiktoken"]))
    local = threading.local()
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
        url = f"https://huggingface.co/{repo}/resolve/{info.sha}/{file.rfilename}"
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            response = client.head(url)
            response.raise_for_status()
            resolved_url = str(response.url)
        count = (file.size + chunk_size - 1) // chunk_size
        began = time.perf_counter()

        def fetch(index):
            first, last = index * chunk_size, min(file.size, (index + 1) * chunk_size) - 1
            path = parts / str(index)
            if path.is_file() and path.stat().st_size == last - first + 1:
                return
            if not hasattr(local, "client"):
                local.client = httpx.Client(timeout=90, follow_redirects=True)
            for attempt in range(4):
                try:
                    response = local.client.get(resolved_url, headers={"Range": f"bytes={first}-{last}"})
                    response.raise_for_status()
                    if response.headers.get("content-range") != f"bytes {first}-{last}/{file.size}":
                        raise ValueError("Server did not honor the exact requested HTTP range")
                    if len(response.content) != last - first + 1:
                        raise ValueError("Incomplete HTTP range")
                    path.write_bytes(response.content)
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
    parser.add_argument("--cache", type=Path, default=Path(".cache/qwen-models"))
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    for repo in args.repo:
        download(repo, args.cache.resolve(), args.workers)
