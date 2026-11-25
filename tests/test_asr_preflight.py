import json

import pytest

from tingmu import batch, media, workspace_models


@pytest.mark.parametrize("installed,missing", [
    ((), "Qwen3-ASR-1.7B"),
    (("Qwen/Qwen3-ASR-1.7B",), "Qwen3-ForcedAligner-0.6B"),
])
def test_missing_qwen_weights_report_specific_component_before_audio_or_gpu(
    tmp_path, monkeypatch, installed, missing
):
    monkeypatch.setattr(workspace_models, "cache_root", lambda: tmp_path)
    monkeypatch.setattr(workspace_models, "_support", lambda _: None)
    for name in installed:
        repo = tmp_path / "qwen-models" / ("models--" + name.replace("/", "--"))
        revision = "a" * 40
        (repo / "refs").mkdir(parents=True)
        (repo / "refs/main").write_text(revision)
        snapshot = repo / "snapshots" / revision
        snapshot.mkdir(parents=True)
        for file in ("config.json", "tokenizer_config.json", "preprocessor_config.json", "tokenizer.json"):
            (snapshot / file).write_text("{}")
        (snapshot / "weights-01.safetensors").write_bytes(b"weights")
        (snapshot / "model.safetensors.index.json").write_text(json.dumps({
            "weight_map": {"test": "weights-01.safetensors"}
        }))
    monkeypatch.setattr(media, "prepared_audio", lambda *a, **k: pytest.fail("audio extraction started"))
    monkeypatch.setattr(media, "_qwen_python", lambda: pytest.fail("GPU probe started"))
    with pytest.raises(media.AsrSetupError, match=missing) as caught:
        media.transcribe(tmp_path / "video.mkv", model="qwen3-asr-1.7b", suppress_bgm=True)
    detail = batch._failure_details(caught.value, None)
    assert missing in detail["error_message"] and "工作区语音识别设置下载" in detail["error_message"]


def test_upstream_media_error_still_does_not_copy_untrusted_text_into_queue():
    details = batch._failure_details(media.MediaError("private SDK response document"), None)
    assert "private SDK" not in details["error_message"]
