import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import shutil
import sys
import threading
from urllib.parse import unquote
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from tingmu import runtime_deps, runtime_install as installer


def archive(members):
    payload = BytesIO()
    with ZipFile(payload, "w", ZIP_DEFLATED) as dest:
        for name, data in members.items():
            dest.writestr(name, data)
    return payload.getvalue()


@pytest.fixture
def local_release(monkeypatch):
    assets = {}
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            payload = assets[self.path]
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    serve = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .02})
    serve.start()
    locks = {}
    monkeypatch.setattr(installer, "_locks", lambda: locks)

    def release(kind, members, name="release.zip", bad_hash=False):
        data = archive(members)
        assets["/" + name] = data
        item = {"url": f"http://127.0.0.1:{server.server_port}/{name}", "size": len(data),
                "sha256": "0" * 64 if bad_hash else hashlib.sha256(data).hexdigest()}
        locks.setdefault(kind, []).append(item)
        return len(data)

    try:
        yield release, calls
    finally:
        server.shutdown()
        serve.join()
        server.server_close()


MEDIA = {"ffmpeg-win/bin/ffmpeg.exe": b"media", "ffmpeg-win/bin/ffprobe.exe": b"probe",
         "ffmpeg-win/bin/ffplay.exe": b"player", "ffmpeg-win/bin/avcodec-63.dll": b"shared",
         "ffmpeg-win/LICENSE": b"license", "ffmpeg-win/include/ffmpeg.h": b"header"}


def test_loopback_media_install_preserves_siblings_and_reuses_verified_pair(tmp_path, monkeypatch, local_release):
    release, calls = local_release
    total = release("ffmpeg", MEDIA)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "other-tool.exe").write_bytes(b"mine")
    seen = []

    def probe(root, kind, folder, *, cancelled=None):
        assert root == tmp_path and kind == "ffmpeg"
        return {"ready": (folder / "ffmpeg.exe").read_bytes() == b"media"
                and (folder / "ffprobe.exe").read_bytes() == b"probe"}
    monkeypatch.setattr(runtime_deps, "verify_candidate", probe)

    def progress(done, all_bytes):
        seen.append((done, all_bytes))

    assert installer.download_bytes("ffmpeg", tmp_path) == total
    assert not calls
    assert installer.install(tmp_path, "ffmpeg", progress, lambda: False) == bin_dir
    assert seen[0] == (0, total) and seen[-1] == (total, total)
    assert set(p.name for p in bin_dir.iterdir()) == {"ffmpeg.exe", "ffprobe.exe", "avcodec-63.dll",
                                                       "FFmpeg-LICENSE.txt", "other-tool.exe"}
    assert (bin_dir / "other-tool.exe").read_bytes() == b"mine"
    installer.install(tmp_path, "ffmpeg", progress, lambda: False)
    assert seen[-1] == (0, 0)
    assert calls == ["/release.zip"]


@pytest.mark.parametrize("failure", ["hash", "cancel", "verify", "../outside", "bin/../../outside",
                                     "docs/file:stream", "docs/CON", r"docs\..\outside"])
def test_interrupted_and_invalid_download_preserve_old_install(failure, local_release, monkeypatch, tmp_path):
    release, _ = local_release
    payload = MEDIA.copy()
    if failure not in {"hash", "cancel", "verify"}:
        payload[failure] = b"bad path"
    release("ffmpeg", payload, bad_hash=failure == "hash")
    monkeypatch.setattr(runtime_deps, "verify_candidate", lambda *args, **kwargs: {"ready": False})
    old = tmp_path / "bin/other.exe"
    old.parent.mkdir()
    old.write_bytes(b"previous")
    cancel = False

    def progress(done, total):
        nonlocal cancel
        cancel = bool(done and failure == "cancel")

    with pytest.raises((ValueError, InterruptedError, RuntimeError)):
        installer.install(tmp_path, "ffmpeg", progress, lambda: cancel)
    assert list(old.parent.iterdir()) == [old]
    assert old.read_bytes() == b"previous"
    assert not (tmp_path / "outside").exists()
    assert not list((tmp_path / ".cache/runtime-deps").glob("install-*"))


def test_cuda_dll_selection_only(tmp_path, monkeypatch, local_release):
    release, _ = local_release
    release("whisper-cuda", {"nvidia/cublas/bin/cublas64_12.dll": b"cublas",
                            "nvidia/cublas/lib/import.lib": b"lib", "nvidia/cublas/include/x.h": b"h"},
            name="nvidia_cublas-cu12.whl")
    release("whisper-cuda", {"nvidia/cudnn/bin/cudnn64_9.dll": b"cudnn",
                            "nvidia_cudnn.dist-info/licenses/License.txt": b"License"},
            name="nvidia_cudnn-cu12.whl")
    monkeypatch.setattr(runtime_deps, "verify_candidate", lambda r, k, p, **kwargs: {
        "ready": all((p / n).is_file() for n in ("cublas64_12.dll", "cudnn64_9.dll"))})
    dest = installer.install(tmp_path, "whisper-cuda", lambda *_: None, lambda: False)
    assert dest == tmp_path / ".cache/runtime-deps/whisper-cuda/bin"
    assert {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()} == {
        "cublas64_12.dll", "cudnn64_9.dll", "licenses/nvidia_cudnn-License.txt"}


def test_local_asr_wheel_layout_filters_weight_and_development_files(tmp_path, monkeypatch, local_release):
    release, _ = local_release
    release("local-asr", {"faster_whisper/__init__.py": b"", "faster_whisper/assets/silero.onnx": b"weight",
                          "engine.data/platlib/native.pyd": b"PE", "engine/tests/test_model.py": b"test",
                          "engine.dist-info/LICENSE": b"notice", "engine.data/scripts/demo.py": b"demo"},
            name="engine-1-py3-none-any.whl")
    monkeypatch.setattr(runtime_deps, "verify_candidate", lambda r, k, p, **kwargs: {"ready": (p / "native.pyd").is_file()})
    dest = installer.install(tmp_path, "local-asr", lambda *_: None, lambda: False)
    assert {p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file()} == {
        "faster_whisper/__init__.py", "native.pyd", "engine.dist-info/LICENSE"}


def test_linked_destination_fails_before_network(tmp_path, local_release):
    release, calls = local_release
    release("ffmpeg", MEDIA)
    root = tmp_path / "portable"
    root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    try:
        (root / "bin").symlink_to(external, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi
        _winapi.CreateJunction(str(external), str(root / "bin"))
    with pytest.raises(ValueError):
        installer.install(root, "ffmpeg", lambda *_: None, lambda: False)
    assert not calls and not list(external.iterdir())


def test_release_locks_are_windows_wheels_and_exclude_demo_and_model_downloads():
    locks = installer._locks()
    names = {kind: {unquote(item["url"].split("/")[-1]).partition("-")[0] for item in items}
             for kind, items in locks.items()}
    assert set(locks) == {"ffmpeg", "local-asr", "whisper-cuda", "qwen", "uv"}
    assert len(locks["ffmpeg"]) == len(locks["uv"]) == 1
    assert {"faster_whisper", "onnxruntime", "ctranslate2", "av", "tokenizers"} <= names["local-asr"]
    assert names["whisper-cuda"] == {"nvidia_cublas_cu12", "nvidia_cuda_runtime_cu12",
                                     "nvidia_cuda_nvrtc_cu12", "nvidia_cudnn_cu12"}
    assert {"torch", "qwen_asr", "nagisa", "transformers", "dynet38"} <= names["qwen"]
    assert names["qwen"].isdisjoint({"gradio", "gradio_client", "fastapi", "flask", "uvicorn"})
    for kind, items in locks.items():
        for item in items:
            assert item["url"].startswith("https://")
            assert len(bytes.fromhex(item["sha256"])) == 32 and item["size"] > 0
            if kind not in {"ffmpeg", "uv"}:
                assert item["url"].endswith(".whl")


def test_offline_uv_installs_verified_local_wheel_without_resolving_dependency(tmp_path):
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("local uv unavailable")
    wheel = tmp_path / "sample_runtime-1.0-py3-none-any.whl"
    prefix = "sample_runtime-1.0.dist-info/"
    files = {"sample_runtime.py": b"READY = True\n",
             prefix + "METADATA": b"Metadata-Version: 2.1\nName: sample-runtime\nVersion: 1.0\n"
                                  b"Requires-Dist: deliberately-unavailable\n",
             prefix + "WHEEL": b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"}
    files[prefix + "RECORD"] = "".join(f"{path},,\n" for path in [*files, prefix + "RECORD"]).encode()
    wheel.write_bytes(archive(files))
    dest = tmp_path / "中文 空格"
    installer._offline_uv(uv, Path(sys.executable), [wheel], dest, tmp_path, lambda: False)
    assert (dest / "sample_runtime.py").read_bytes() == files["sample_runtime.py"]
