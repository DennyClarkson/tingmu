import json
from contextlib import nullcontext
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tingmu import media, whisper_support, workspace_models


@pytest.mark.parametrize("device", ["cpu", "cuda"])
@pytest.mark.parametrize("source", ["missing", "bundled", "downloaded"])
def test_whisper_worker_uses_installed_vad(tmp_path, monkeypatch, device, source):
    has_vad = source != "missing"
    if source == "bundled":
        (tmp_path / whisper_support.FILENAME).touch()
    monkeypatch.setattr(media, "is_clean_install", lambda: source != "bundled")
    monkeypatch.setattr(whisper_support, "inventory", lambda: {"complete": source == "downloaded"})
    shared = tmp_path / "whisper-support"
    monkeypatch.setattr(whisper_support, "SUPPORT", SimpleNamespace(path=lambda: shared))
    vad = SimpleNamespace(get_assets_path=lambda: str(tmp_path))
    monkeypatch.setattr(media, "distribution", lambda name: SimpleNamespace(locate_file=lambda path: tmp_path))
    monkeypatch.setattr(media, "_register_cuda_dlls", lambda: None)
    monkeypatch.setattr(media, "_read_pcm_audio", lambda path: [])
    events = []
    monkeypatch.setattr(media, "_worker_emit", events.append)

    class Engine:
        def __init__(self, *args, **kwargs):
            assert kwargs["device"] == device
            assert kwargs["local_files_only"]

        def transcribe(self, audio, **kwargs):
            assert kwargs["vad_filter"] is has_vad
            assert kwargs["vad_parameters"]["min_silence_duration_ms"] == 500
            assert vad.get_assets_path() == str(shared if source == "downloaded" else tmp_path)
            return [], SimpleNamespace(duration=1, language="en")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Engine, vad=vad))
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(get_cuda_device_count=lambda: 1))
    request = {"model": "small", "device": device, "model_cache": str(tmp_path), "language": None,
               "audio": "input.wav", "result": str(tmp_path / "result.json")}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    assert media._asr_worker(path) == 0
    assert events[-1]["done"] and events[-1]["vad_filter"] is has_vad
    assert any("Silero" in event.get("progress", "") for event in events) != has_vad


@pytest.mark.parametrize("model", ["small", "qwen/qwen3-asr-0.6b"])
@pytest.mark.parametrize("consented", [False, True])
def test_clean_transcription_downloads_only_with_consent(tmp_path, monkeypatch, model, consented):
    downloads = []
    monkeypatch.setattr(workspace_models, "weight_readiness",
                        lambda _: workspace_models.ModelReadiness(True, "ready", ""))
    monkeypatch.setattr("tingmu.runtime_deps.require", lambda *args: {"ready": True})
    monkeypatch.setattr(media, "is_clean_install", lambda: True)
    monkeypatch.setattr(media, "_qwen_python", lambda: sys.executable)
    monkeypatch.setattr(media, "_cache_root", lambda: tmp_path)
    monkeypatch.setattr(media, "prepared_audio", lambda *args, **kwargs: nullcontext(tmp_path / "input.wav"))
    monkeypatch.setattr(workspace_models, "download_model", lambda selected, *args: downloads.append(selected))

    def run_worker(request, *args):
        assert request["allow_model_downloads"] is False
        Path(request["result"]).write_text("[]", encoding="utf-8")
        return True, ""

    monkeypatch.setattr(media, "_run_worker", run_worker)
    assert media.transcribe(tmp_path / "video", model=model, allow_model_downloads=consented) == []
    assert downloads == ([model.removeprefix("qwen/")] if consented else [])
