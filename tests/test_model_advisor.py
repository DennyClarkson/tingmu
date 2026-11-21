import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from tingmu import model_advisor as advisor, workspace_models as weights
from tingmu.models import AppSettings


def hardware(*, ram=24, disk=100, cuda=False, runtime=True):
    return {"ram_available_bytes": ram * advisor.GIB if ram is not None else None,
            "disk_free_bytes": disk * advisor.GIB, "runtimes": {
                engine: {"cpu_usable": runtime, "cuda_usable": cuda,
                         "cuda_free_bytes": 12 * advisor.GIB if cuda else 0}
                for engine in ("whisper", "qwen")}}


@pytest.fixture
def empty_cache(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(weights, "cache_root", lambda: cache)
    return cache


def snapshot(cache, model, repo=None):
    folder, repositories = weights._assets(model)
    repo = repo or repositories[0]
    hub = folder / ("models--" + repo.replace("/", "--"))
    target = hub / "snapshots" / ("1" * 40)
    target.mkdir(parents=True)
    names = ["config.json", "model.bin", "tokenizer.json"] if model in weights.WHISPER_MODELS else [
        "config.json", "model.safetensors", "tokenizer.json", "tokenizer_config.json", "preprocessor_config.json"]
    for name in names:
        (target / name).write_text("{}")
    (hub / "refs").mkdir()
    (hub / "refs/main").write_text("1" * 40)
    return target


def candidate(report, model):
    return next(row for row in report["candidates"] if row["model"] == model)


def test_language_mapping_matches_backend_and_cantonese_excludes_small(empty_cache):
    report = advisor.recommend_models(language="zh-HK", hardware=hardware())
    assert report["language"] == "yue"
    assert not candidate(report, "small")["available"]
    assert report["recommendation"]["model"] == "large-v3-turbo"
    unsupported = advisor.recommend_models(language="ar", hardware=hardware())
    assert not candidate(unsupported, "qwen3-asr-1.7b")["available"]
    assert candidate(unsupported, "large-v3")["available"]
    unknown = advisor.recommend_models(language="not-a-language", hardware=hardware())
    assert unknown["recommendation"] is None


def test_speed_accuracy_resources_and_gpu_runtime_are_distinct(empty_cache):
    assert advisor.recommend_models(language="en", preference="speed", hardware=hardware())["recommendation"]["model"] == "small"
    assert advisor.recommend_models(language="en", preference="accuracy", hardware=hardware(cuda=True))["recommendation"]["model"] == "qwen3-asr-1.7b"
    no_cuda = advisor.recommend_models(language="en", hardware=hardware(ram=4))
    assert no_cuda["recommendation"]["model"] == "small"
    assert no_cuda["recommendation"]["device"] == "cpu"
    assert advisor.recommend_models(hardware=hardware(ram=1))["recommendation"] is None
    assert advisor.recommend_models(hardware=hardware(ram=None))["recommendation"] is None
    assert advisor.recommend_models(hardware=hardware(runtime=False))["recommendation"] is None


def test_offline_cache_and_disk_constraints(empty_cache):
    assert advisor.recommend_models(offline=True, hardware=hardware())["recommendation"] is None
    snapshot(empty_cache, "large-v3-turbo")
    report = advisor.recommend_models(offline=True, hardware=hardware(disk=0))
    assert report["recommendation"]["ready"]
    assert report["recommendation"]["download_bytes"] == 0
    assert not candidate(report, "large-v3")["available"]


def test_partial_cache_and_shared_aligner_estimates(empty_cache):
    snapshot(empty_cache, "qwen3-asr-1.7b", weights.ALIGNER)
    inventory = weights.model_inventory("qwen3-asr-0.6b")
    assert inventory["cache_state"] == "incomplete"
    assert inventory["download_bytes"] == weights.REPOSITORY_BYTES[weights.QWEN_MODELS["qwen3-asr-0.6b"]]
    assert inventory["repositories"][1]["download_bytes"] == 0
    target = snapshot(empty_cache, "qwen3-asr-0.6b")
    assert weights.model_inventory("qwen3-asr-0.6b")["installed"]
    (target / "model.safetensors").write_bytes(b"")
    assert not weights.model_inventory("qwen3-asr-0.6b")["installed"]


def test_broken_or_escaping_snapshot_is_not_ready(empty_cache, tmp_path):
    target = snapshot(empty_cache, "qwen3-asr-0.6b")
    (target / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"weight": "../../../../outside"}}))
    (tmp_path / "outside").write_text("not weights")
    assert not weights.model_inventory("qwen3-asr-0.6b")["installed"]
    (target / "model.safetensors.index.json").write_text('{"weight_map": []}')
    assert not weights.model_inventory("qwen3-asr-0.6b")["installed"]
    (target / "model.safetensors.index.json").write_text('{"weight_map": {}}')
    assert not weights.model_inventory("qwen3-asr-0.6b")["installed"]


def test_size_deduplicates_hardlinked_snapshot_and_counts_partial_cache(empty_cache):
    folder = empty_cache / "folder"
    folder.mkdir()
    original = folder / "weights.bin"
    original.write_bytes(b"x" * 123)
    (folder / "alias.bin").hardlink_to(original)
    (folder / "partial.incomplete").write_bytes(b"x" * 17)
    assert weights._directory_bytes(folder, empty_cache) == 140


def test_api_options_are_explicit_unverified_and_never_called(empty_cache, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("network or download")
    monkeypatch.setattr(weights, "download_model", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    settings = AppSettings(asr_backend="local_api")
    report = advisor.recommend_models(settings, offline=True, hardware=hardware(ram=1))
    assert report["alternatives"][1]["available"]
    assert not report["alternatives"][1]["verified"]
    settings.local_asr_endpoint = "https://example.com/v1/audio/transcriptions"
    assert not advisor.recommend_models(settings, hardware=hardware())["alternatives"][1]["available"]
    settings.asr_backend = "external"
    assert not advisor.recommend_models(settings, hardware=hardware())["alternatives"][1]["available"]
    settings.allow_audio_upload = True
    assert advisor.recommend_models(settings, hardware=hardware())["alternatives"][1]["available"]
    assert not advisor.recommend_models(settings, offline=True, hardware=hardware())["alternatives"][1]["available"]
    assert len(advisor.recommend_models(AppSettings(), hardware=hardware())["alternatives"]) == 1


def test_probe_uses_given_worker_interpreter_and_treats_failure_as_unusable(tmp_path, monkeypatch):
    executable = tmp_path / "portable-python.exe"
    executable.touch()
    observed = []
    def run(argv, **kwargs):
        observed.append(argv)
        return SimpleNamespace(returncode=0, stdout='warning\n{"cpu_usable": true, "cuda_usable": false}')
    monkeypatch.setattr(subprocess, "run", run)
    result = advisor._probe_runtime(executable, "whisper")
    assert observed[0][0] == str(executable)
    assert observed[0][-1] == "whisper"
    assert result["cpu_usable"] and not result["cuda_usable"]
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("python", 35)
    monkeypatch.setattr(subprocess, "run", timeout)
    assert not advisor._probe_runtime(executable, "qwen")["cpu_usable"]


def test_custom_local_language_tokens_are_checked(empty_cache, monkeypatch):
    from tingmu import local_whisper
    monkeypatch.setattr(local_whisper, "cache_root", lambda: empty_cache)
    folder = empty_cache / "models" / "custom"
    folder.mkdir(parents=True)
    for name in ("config.json", "model.bin"):
        (folder / name).write_text("{}")
    (folder / "tokenizer.json").write_text(json.dumps({"added_tokens": [{"content": "<|en|>"}]}))
    settings = AppSettings(asr_model="local:custom", target_language="yue")
    assert not candidate(advisor.recommend_models(settings, hardware=hardware()), "local:custom")["available"]
    settings.target_language = "en"
    assert candidate(advisor.recommend_models(settings, hardware=hardware()), "local:custom")["ready"]


@pytest.mark.parametrize("location", [".download-locks", ".locks", ".locks/models--Systran--faster-whisper-small",
                                       "models--Systran--faster-whisper-small"])
def test_download_rejects_redirected_cache_directories(empty_cache, tmp_path, location, monkeypatch):
    cache = empty_cache / "models"
    redirected = cache / location
    redirected.parent.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    if os.name == "nt":
        from _winapi import CreateJunction
        CreateJunction(str(outside), str(redirected))
    else:
        redirected.symlink_to(outside, target_is_directory=True)
    def forbidden(*args):
        raise AssertionError("must reject before download")
    monkeypatch.setattr(weights, "_download_repo", forbidden)
    with pytest.raises(ValueError):
        weights.download_model("small", lambda _: None, lambda: False)
    assert not list(outside.iterdir())
