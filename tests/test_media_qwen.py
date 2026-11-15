from types import SimpleNamespace

import pytest

from tingmu.media import _alignment_to_cues, _qwen_model_id, _qwen_snapshot, _audio_windows, MediaError


def item(text, start, end):
    return SimpleNamespace(text=text, start_time=start, end_time=end)


def test_qwen_aliases_are_explicit():
    assert _qwen_model_id("qwen3-asr-1.7b") == "Qwen/Qwen3-ASR-1.7B"
    assert _qwen_model_id("Qwen/Qwen3-ASR-0.6B") == "Qwen/Qwen3-ASR-0.6B"
    assert _qwen_model_id("large-v3-turbo") is None


def test_resampler_tail_does_not_become_invalid_three_sample_audio():
    assert _audio_windows(480003, 480000) == [(0, 480003)]
    assert _audio_windows(960003, 480000) == [(0, 480000), (480000, 960003)]
    assert _audio_windows(963200, 480000) == [(0, 480000), (480000, 960000), (960000, 963200)]


def test_alignment_preserves_particles_punctuation_and_absolute_time():
    raw = "係喎！真㗎？唔係啫，嚟咗啦。"
    letters = "係喎真㗎唔係啫嚟咗啦"
    units = [item(char, 1 + i * 0.3, 1.2 + i * 0.3) for i, char in enumerate(letters)]
    cues = _alignment_to_cues(raw, units, source="test", offset=180, duration=30, chunk_index=0)
    assert "".join(c.original_text for c in cues) == raw
    assert [c.text for c in cues] == ["係喎！", "真㗎？", "唔係啫，嚟咗啦。"]
    assert cues[0].start == 181 and cues[0].end == 181.5
    assert all(180 <= c.start < c.end <= 210 for c in cues)


def test_alignment_never_interpolates_missing_units():
    with pytest.raises(MediaError, match="没有返回时间"):
        _alignment_to_cues("係喎", [], source="test", offset=0, duration=30, chunk_index=0)
    with pytest.raises(MediaError, match="不一致"):
        _alignment_to_cues("係喎", [item("不是", 1, 2)], source="test", offset=0, duration=30, chunk_index=0)


def test_alignment_splits_at_acoustic_gap_and_retains_english():
    raw = "係，OK 啦。"
    cues = _alignment_to_cues(raw, [item("係", 1, 1.3), item("OK", 3, 3.3), item("啦", 3.4, 3.6)],
                              source="test", offset=90, duration=30, chunk_index=2)
    assert [c.text for c in cues] == ["係，", "OK 啦。"]
    assert "".join(c.original_text for c in cues) == raw
    assert (cues[1].start, cues[1].end) == (93, 93.6)


def test_alignment_preserves_punctuation_inside_cleaned_english_unit():
    raw = "Wi-Fi 好㗎。"
    cues = _alignment_to_cues(raw, [item("WiFi", 1, 1.3), item("好", 1.4, 1.6), item("㗎", 1.7, 1.9)],
                              source="test", offset=0, duration=30, chunk_index=0)
    assert "".join(c.original_text for c in cues) == raw
    with pytest.raises(MediaError, match="遗漏"):
        _alignment_to_cues("係喎", [item("係", 1, 2)], source="test", offset=0, duration=30, chunk_index=0)


def test_complete_qwen_cache_is_used_without_network(tmp_path, monkeypatch):
    import huggingface_hub
    for name in ["config.json", "tokenizer_config.json", "preprocessor_config.json", "vocab.json", "merges.txt", "model.safetensors"]:
        (tmp_path / name).write_text("test", encoding="utf-8")
    calls = []
    def download(repo, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("local_files_only") is True
        return str(tmp_path)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    assert _qwen_snapshot("Qwen/test", "cache") == str(tmp_path)
    assert len(calls) == 1


def test_incomplete_qwen_cache_does_not_count_as_offline_ready(tmp_path, monkeypatch):
    import huggingface_hub
    (tmp_path / "model.safetensors.index.json").write_text('{"weight_map":{"x":"missing.safetensors"}}')
    calls = []
    def download(repo, **kwargs):
        calls.append(kwargs)
        return str(tmp_path)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    with pytest.raises(MediaError, match="缓存不完整"):
        _qwen_snapshot("Qwen/test", "cache")
    assert len(calls) == 2 and "local_files_only" not in calls[1]
