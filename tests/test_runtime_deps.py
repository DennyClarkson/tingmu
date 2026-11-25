"""All discovery tests are offline; even the interpreter with synthetic modules is isolated."""

import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from tingmu import media, runtime_deps as deps, workspace_models


def marker(root, **paths):
    data = root / "data"
    data.mkdir(exist_ok=True)
    (data / "runtime-setup.json").write_text(json.dumps({"paths": {k: str(v) for k, v in paths.items()}}))


def pair(folder):
    folder.mkdir(parents=True)
    for name in ("ffmpeg", "ffprobe"):
        (folder / (name + deps._SUFFIX)).touch()


def test_versioned_ffmpeg_pair_priority_and_invalidated_external_path(tmp_path, monkeypatch):
    local = tmp_path / "bin"
    external = tmp_path / "已有 工具"
    system = tmp_path / "path tools"
    for folder in (local, external, system):
        pair(folder)
    marker(tmp_path, ffmpeg=external)
    monkeypatch.setenv("PATH", str(system))
    seen = []

    def version(command, **kwargs):
        path = Path(command[0])
        seen.append((path, command[1]))
        assert kwargs["timeout"] and kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        return SimpleNamespace(stdout=f"{path.stem} version v1\n".encode(), returncode=0)

    monkeypatch.setattr(subprocess, "run", version)
    assert deps.probe(tmp_path, "ffmpeg")["source"] == "portable"
    (local / ("ffprobe" + deps._SUFFIX)).unlink()
    assert deps.last_probe(tmp_path, "ffmpeg") is None
    selected = deps.cached_probe(tmp_path, "ffmpeg")
    assert selected["path"] == str(external) and selected["source"] == "configured"
    assert seen == [(folder / (name + deps._SUFFIX), "-version")
                    for folder in (local, external) for name in ("ffmpeg", "ffprobe")]
    external.rename(external.with_name("moved"))
    assert deps.last_probe(tmp_path, "ffmpeg") is None
    assert deps.cached_probe(tmp_path, "ffmpeg")["path"] == str(system)


def test_verify_exact_ffmpeg_rejects_wrong_program_and_timeout(tmp_path, monkeypatch):
    pair(tmp_path / "bin")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=b"invalid", returncode=0))
    assert not deps.verify_candidate(tmp_path, "ffmpeg", tmp_path / "bin")["ready"]

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired("ffmpeg", 5)

    monkeypatch.setattr(subprocess, "run", timed_out)
    assert not deps.verify_candidate(tmp_path, "ffmpeg", tmp_path / "bin")["ready"]


def synthetic_site(site):
    code = {"faster_whisper": "class WhisperModel: pass", "ctranslate2": "__version__='fixture'\n"
            "def get_supported_compute_types(device): return {'int8'}", "onnxruntime":
            "def get_available_providers(): return ['CPUExecutionProvider']"}
    for name, contents in code.items():
        package = site / name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(contents)


def test_selected_site_checked_with_actual_interpreter_no_external_writes_or_network(tmp_path):
    selected = tmp_path / "outside cache" / "site-packages"
    synthetic_site(selected)
    root = tmp_path / "portable"
    ready = deps.verify_candidate(root, "local-asr", selected)
    assert ready["ready"] and ready["source"] == "configured" and ready["network_attempts"] == 0
    assert not list(selected.rglob("__pycache__"))
    package = selected / "faster_whisper" / "__init__.py"
    package.write_text("import socket\nsocket.getaddrinfo('offline.invalid', 443)")
    assert not deps.verify_candidate(root, "local-asr", selected)["ready"]
    destination = selected / "should-not-exist"
    package.write_text(f"open({str(destination)!r}, 'w').close()\nclass WhisperModel: pass")
    assert not deps.verify_candidate(root, "local-asr", selected)["ready"]
    assert not destination.exists()


def test_missing_or_partial_local_asr_cannot_borrow_packages_to_validate_a_stage(tmp_path):
    selected = tmp_path / "staging"
    selected.mkdir()
    assert not deps.verify_candidate(tmp_path, "local-asr", selected)["ready"]
    synthetic_site(selected)
    (selected / "onnxruntime/__init__.py").unlink()
    assert not deps.verify_candidate(tmp_path, "local-asr", selected)["ready"]


def test_cuda_requires_successful_native_handles_in_exact_directories(tmp_path, monkeypatch):
    folder = tmp_path / "shared"
    folder.mkdir()
    files = [folder / name for name in deps._CUDA_LIBS]
    files[0].touch()
    calls = []

    def failed(root, executable, kind, **options):
        calls.append((kind, options))
        return {"cuda_usable": False, "error": "RuntimeError"}

    monkeypatch.setattr(deps, "_python_check", failed)
    assert not deps.verify_candidate(tmp_path, "whisper-cuda", folder)["ready"] and not calls
    files[1].touch()
    assert not deps.verify_candidate(tmp_path, "whisper-cuda", folder)["ready"]
    assert calls == [("whisper-cuda", {"directories": [str(folder)], "libraries": list(map(str, files))})]


def test_cuda_declines_unusable_bundled_binaries_and_keeps_cpu_available(tmp_path, monkeypatch):
    portable = tmp_path / ".cache/runtime-deps/whisper-cuda/bin"
    current = tmp_path / "external"
    for folder in (portable, current):
        folder.mkdir(parents=True)
        for name in deps._CUDA_LIBS:
            (folder / name).touch()
    monkeypatch.setattr(deps, "_cuda_candidates", lambda *_: [("portable", [portable]), ("system", [current])])
    monkeypatch.setattr(deps, "_python_check", lambda root, exe, kind, directories, libraries:
                        {"cuda_usable": directories == [str(current)]})
    state = deps.probe(tmp_path, "whisper-cuda")
    assert state["nvidia_present"] and state["path"] == str(current)
    assert state["source"] == "system" and state["directories"] == [str(current)]


def test_isolated_qwen_candidate_and_runtime_marker_only_invalidation(tmp_path, monkeypatch):
    external = tmp_path / "external python"
    external.mkdir()
    exe = external / ("python" + deps._SUFFIX)
    exe.touch()
    marker(tmp_path, qwen=exe)
    monkeypatch.setattr(deps, "_python_check", lambda root, path, kind:
                        {"cpu_usable": Path(path) == exe, "cuda_usable": False, "files": []})
    assert deps.probe(tmp_path, "qwen")["source"] == "configured"
    file = tmp_path / "data/runtime-setup.json"
    values = json.loads(file.read_text())
    values["status"] = "skipped"
    file.write_text(json.dumps(values))
    assert deps.last_probe(tmp_path, "qwen")["ready"]
    exe.unlink()
    assert deps.last_probe(tmp_path, "qwen") is None


def test_portable_missing_local_engine_prevents_weight_download_preparation_and_ui_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(workspace_models, "portable_root", lambda: tmp_path)
    monkeypatch.setattr(deps, "require", lambda *a: (_ for _ in ()).throw(RuntimeError(deps.HINTS["local-asr"])))
    monkeypatch.setattr(workspace_models, "_download_repo", lambda *a: pytest.fail("unexpected weight download"))
    monkeypatch.setattr(workspace_models, "_support", lambda *a: pytest.fail("unexpected support download"))
    with pytest.raises(RuntimeError, match="运行环境"):
        workspace_models.download_model("small", lambda x: None, lambda: False)
    assert workspace_models.model_readiness("small").code == "runtime_missing"
    assert "检测识别运行环境" in workspace_models.model_status("small")
    monkeypatch.setattr(media, "portable_root", lambda: tmp_path)
    monkeypatch.setattr(media, "prepared_audio", lambda *a, **k: pytest.fail("unexpected audio extraction"))
    with pytest.raises(media.MediaError, match="运行环境"):
        media.transcribe(tmp_path / "input", model="small", allow_model_downloads=True)

def test_skip_terminates_active_probe_instead_of_waiting_for_its_timeout():
    import sys
    import time

    start = time.monotonic()
    with pytest.raises(InterruptedError):
        deps._run([sys.executable, "-I", "-B", "-c", "import time; time.sleep(30)"],
                  timeout=30, cancelled=lambda: time.monotonic() - start > .25)
    assert time.monotonic() - start < 5
