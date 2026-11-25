"""Guard the release artifact against cached and wheel-bundled model weights."""
from pathlib import Path

import pytest


def test_clean_copy_keeps_language_data_and_omits_wheel_weights(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "packaging"))
    from build_clean_install import ignore_runtime

    assert ignore_runtime(tmp_path / "pycantonese/data/hkcancor", ["sample.cha"]) == []
    assert ignore_runtime(tmp_path / "faster_whisper/assets", ["__init__.py", "silero_vad_v6.onnx"]) == [
        "silero_vad_v6.onnx"]
    assert ignore_runtime(tmp_path, ["tagger.pickle", "nagisa_v001.model", "model.safetensors"]) == [
        "tagger.pickle", "nagisa_v001.model", "model.safetensors"]


@pytest.mark.parametrize("name", ["unlisted.bin", "params.safetensors", "checkpoint.pth", "nagisa_v001.model"])
def test_final_model_audit_rejects_weights_including_disguised_checkpoints(tmp_path, monkeypatch, name):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "packaging"))
    from build_clean_install import validate_model_free_runtime

    libs = tmp_path / "runtime/asr/Lib/site-packages"
    stats = libs / "scipy/stats"
    stats.mkdir(parents=True)
    (stats / "_sobol_direction_numbers.npz").write_bytes(b"numerical constants")
    (libs / "distutils-precedence.pth").write_text("import os\n", encoding="utf-8")
    assert validate_model_free_runtime(tmp_path) == {"files_checked": 2, "model_payloads": 0}
    (libs / name).write_bytes(b"PK\x00\x01")
    with pytest.raises(ValueError, match="model-free"):
        validate_model_free_runtime(tmp_path)
