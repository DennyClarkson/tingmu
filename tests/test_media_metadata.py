import json
from pathlib import Path
import sys

import pytest

from tingmu import media


@pytest.fixture
def isolated_transcribe(tmp_path, monkeypatch):
    from tingmu.workspace_models import ModelReadiness
    monkeypatch.setattr("tingmu.workspace_models.weight_readiness", lambda _: ModelReadiness(True, "ready", ""))
    monkeypatch.setattr("tingmu.runtime_deps.require", lambda *args: {"ready": True})
    monkeypatch.setattr(media, "_cache_root", lambda: tmp_path)
    monkeypatch.setattr(media, "_qwen_python", lambda: Path(sys.executable))
    monkeypatch.setattr(media, "extract_audio", lambda *args, **kwargs: tmp_path / "audio.wav")
    return tmp_path


def save_cues(request):
    Path(request["result"]).write_text(json.dumps([
        {"id": "original-id", "start": 180, "end": 181, "text": "係喎", "original_text": " 係喎", "uncertain": True}
    ]), encoding="utf-8")


def test_qwen_metadata_propagates_warning_and_runtime_without_raw_payload(isolated_transcribe, monkeypatch):
    def worker(request, *args):
        save_cues(request)
        Path(request["raw_output"]).write_text(json.dumps({
            "model": "Qwen/Qwen3-ASR-1.7B", "complete": True,
            "runtime": {"torch": "2.7.1+cu128", "device": "cuda", "gpu_capability": [12, 0],
                        "peak_cuda_allocated_bytes": 123456, "huge_unknown_field": "secret raw text"},
            "chunks": [{"start": 190, "duration": 20, "alignment_error": "no reliable alignment",
                        "text": "raw transcript must stay in audit", "alignment": [{"text": "raw unit"}]}],
        }), encoding="utf-8")
        return True, ""
    monkeypatch.setattr(media, "_run_worker", worker)
    metadata = {"caller_field": "keep"}
    cues = media.transcribe(isolated_transcribe / "video.mkv", model="qwen3-asr-1.7b", device="cuda",
                            start=180, duration=60, metadata_out=metadata)
    assert metadata["asr_device"] == "cuda"
    assert metadata["asr_model"] == "Qwen/Qwen3-ASR-1.7B"
    assert metadata["asr_backend"] == "qwen3-asr"
    assert Path(metadata["asr_audit_path"]).is_file()
    assert metadata["asr_runtime"]["torch"] == "2.7.1+cu128"
    assert metadata["asr_runtime"]["gpu_capability"] == [12, 0]
    assert metadata["alignment_warnings"] == [{"start": 190, "duration": 20, "error": "no reliable alignment"}]
    assert metadata["alignment_warning_count"] == 1
    assert metadata["asr_audit_metadata_available"] is True
    assert metadata["caller_field"] == "keep"
    assert "raw transcript" not in json.dumps(metadata) and "huge_unknown_field" not in metadata["asr_runtime"]
    assert (cues[0].id, cues[0].start, cues[0].end, cues[0].original_text, cues[0].uncertain) == (
        "original-id", 180, 181, " 係喎", True)


@pytest.mark.parametrize("audit_text", [None, "not json", "[]", '{"runtime":[],"chunks":"bad"}'])
def test_missing_or_malformed_optional_audit_does_not_fail_cues(isolated_transcribe, monkeypatch, audit_text):
    def worker(request, *args):
        save_cues(request)
        if audit_text is not None:
            Path(request["raw_output"]).write_text(audit_text, encoding="utf-8")
        return True, ""
    monkeypatch.setattr(media, "_run_worker", worker)
    metadata = {"alignment_warnings": [{"error": "stale"}]}
    cues = media.transcribe(isolated_transcribe / "video.mkv", model="qwen3-asr-1.7b", device="cuda",
                            metadata_out=metadata)
    assert len(cues) == 1 and cues[0].uncertain
    assert metadata["asr_device"] == "cuda"
    assert metadata["asr_audit_metadata_available"] is False
    assert "alignment_warnings" not in metadata


def test_successful_cpu_fallback_reports_actual_device(isolated_transcribe, monkeypatch):
    attempts = []
    def worker(request, *args):
        attempts.append(request["device"])
        if request["device"] == "cuda":
            return False, "GPU unavailable"
        save_cues(request)
        Path(request["raw_output"]).write_text('{"runtime":{"device":"cuda"},"chunks":[]}', encoding="utf-8")
        return True, ""
    monkeypatch.setattr(media, "_run_worker", worker)
    metadata = {}
    media.transcribe(isolated_transcribe / "video.mkv", model="qwen3-asr-1.7b", metadata_out=metadata)
    assert attempts == ["cuda", "cpu"]
    assert metadata["asr_device"] == "cpu" and metadata["asr_runtime"]["device"] == "cpu"


def test_whisper_metadata_clears_prior_qwen_fields(isolated_transcribe, monkeypatch):
    def worker(request, *args):
        save_cues(request)
        return True, ""
    monkeypatch.setattr(media, "_run_worker", worker)
    metadata = {"caller": True, "asr_audit_path": "old", "alignment_warnings": ["old"]}
    media.transcribe(isolated_transcribe / "video.mkv", model="large-v3-turbo", device="cpu", metadata_out=metadata)
    assert metadata == {"caller": True, "asr_device": "cpu", "asr_model": "large-v3-turbo",
                        "asr_backend": "faster-whisper", "asr_runtime": {"device": "cpu", "compute_type": "int8"}}


def test_metadata_is_untouched_when_transcription_fails(isolated_transcribe, monkeypatch):
    monkeypatch.setattr(media, "_run_worker", lambda *args: (False, "inference failure"))
    metadata = {"caller": "unchanged"}
    with pytest.raises(media.MediaError):
        media.transcribe(isolated_transcribe / "video.mkv", device="cpu", metadata_out=metadata)
    assert metadata == {"caller": "unchanged"}


def test_optional_metadata_bounds_fields_and_skips_invalid_entries(tmp_path):
    path = tmp_path / "audit.json"
    path.write_text(json.dumps({
        "runtime": {"context": "x" * 10000, "gpu_name": "y" * 10000, "gpu_capability": [1, 2, 3],
                    "peak_cuda_allocated_bytes": "bad"},
        "chunks": [None, {"alignment_error": False}, {"alignment_error": "bad", "start": "invalid", "duration": 1}]
                  + [{"start": i, "duration": 1, "alignment_error": "z" * 10000, "text": "raw"} for i in range(105)],
    }), encoding="utf-8")
    result = media._transcription_metadata({"model": "qwen3-asr-1.7b", "device": "cuda", "raw_output": str(path)})
    assert len(result["alignment_warnings"]) == 100 and result["alignment_warning_count"] == 105
    assert result["alignment_warnings_truncated"] is True
    assert len(result["asr_runtime"]["context"]) == 256
    assert len(result["alignment_warnings"][0]["error"]) == 256
    assert "gpu_capability" not in result["asr_runtime"]
