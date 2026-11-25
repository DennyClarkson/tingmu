from types import SimpleNamespace

import numpy as np
import pytest

from tingmu.acoustic import frame_energy, repair_times
from tingmu.media import MediaError, _alignment_to_cues, _audio_windows, _whisper_turn_cues
from tingmu.models import Cue


def aligned(text, times, *, turns=(), pauses=()):
    chars = [c for c in text if c.isalnum()]
    units = [
        SimpleNamespace(text=c, start_time=a, end_time=b) for c, (a, b) in zip(chars, times, strict=True)
    ]
    trace = {}
    cues = _alignment_to_cues(
        text,
        units,
        source="track",
        offset=0,
        duration=30,
        chunk_index=0,
        speaker_changes=turns,
        pauses=pauses,
        trace=trace,
    )
    assert "".join(c.original_text for c in cues) == text
    assert all(a.end <= b.start for a, b in zip(cues, cues[1:]))
    return cues, trace


def test_quiet_window_edges_have_one_owner_and_no_decoder_tail():
    rate = 16000
    audio = np.full(60 * rate + 3, 0.3, dtype=np.float32)
    audio[26 * rate : 27 * rate] = 0
    intervals = _audio_windows(len(audio), 30 * rate, frame_energy(audio))
    assert 26 * rate < intervals[0][1] < 27 * rate
    assert intervals[0][0] == 0 and intervals[-1][1] == len(audio)
    assert all(left[1] == right[0] for left, right in zip(intervals, intervals[1:]))
    assert all(24 * rate <= end - start <= 30 * rate for start, end in intervals[:-1])
    short_tail_audio = audio[: 30 * rate + 3]
    assert (
        _audio_windows(len(short_tail_audio), 30 * rate, frame_energy(short_tail_audio))[-1][0]
        <= 26 * rate + 3
    )


def test_timestamp_plateau_uses_only_the_speech_between_real_anchors():
    # Multiple sentences were aligned at the exact same timestamp. The
    # punctuation remains their textual boundary but carries no independent time.
    raw = "之前。第一句。第二句。之後。"
    times = [(0.2, 0.4), (0.4, 0.6)] + [(2.0, 2.0)] * 6 + [(2.8, 2.95), (2.95, 3.2)]
    cues, trace = aligned(raw, times, pauses=[(0.62, 1.60), (2.65, 2.79)])
    assert [(c.start, c.end) for c in (cues[0], cues[-1])] == [(0.2, 0.6), (2.8, 3.2)]
    assert len(cues) == 4
    assert all(1.60 <= c.start < c.end <= 2.65 and c.end - c.start >= 0.25 and c.uncertain for c in cues[1:3])
    repairs = trace["timing_repairs"]
    assert len(repairs) == 1 and repairs[0]["method"] == "bounded_estimate"
    assert repairs[0]["unit_start"] == 2 and repairs[0]["unit_end"] == 8
    assert all(b - a >= 0.0849 for a, b in repairs[0]["unit_spans"])


def test_insufficient_anchor_room_is_reported_and_original_stays_verbatim():
    raw = "問。答覆。"
    cues, trace = aligned(raw, [(1.0, 1.08), (1.08, 1.08), (1.08, 1.11)])
    assert cues[-1].uncertain
    assert sum(r["unit_end"] - r["unit_start"] for r in trace["timing_repairs"]) >= 2
    assert trace["cue_timings"][-1]["timing_basis"] in {"bounded_estimate", "anchor_limited"}
    with pytest.raises(MediaError, match="零时长"):
        aligned("都冇。", [(2.0, 2.0), (2.0, 2.0)])
    with pytest.raises(ValueError):
        repair_times([(1, 1.2), (float("nan"), 2)], [1, 1], 3, [])


def test_speaker_event_inside_word_does_not_split_and_first_char_stays_in_clause():
    times = [(1, 1.2), (1.2, 1.3), (1.8, 1.9), (1.9, 2.0), (2.0, 2.2)]
    result, _ = aligned("睇電視節目。", times, turns=[1.55])
    assert [c.text for c in result] == ["睇電視節目。"]
    result, _ = aligned(
        "聽完，我出去。",
        [(1, 1.2), (1.2, 1.4), (1.45, 1.55), (2.45, 2.6), (2.6, 2.8)],
        turns=[2.1],
        pauses=[(1.6, 2.4)],
    )
    assert [c.text for c in result] == ["聽完，", "我出去。"]
    # Word-level Whisper timestamps use the same linguistic boundaries even
    # when the optional speaker model is switched off.
    cue = Cue("w", 0, 4, "你出發？我等你。")
    words = [
        SimpleNamespace(word=w, start=i * 0.3, end=i * 0.3 + 0.25)
        for i, w in enumerate(["你", "出發？", "我", "等你。"])
    ]
    assert [c.text for c in _whisper_turn_cues(cue, words, [], 0)] == ["你出發？", "我等你。"]
    cue = Cue("w", 0, 3, "你好")
    words = [SimpleNamespace(word="你", start=1.0, end=1.5), SimpleNamespace(word="好", start=2.0, end=2.5)]
    assert _whisper_turn_cues(cue, words, [1.7], 0) == [cue]


def test_true_long_pause_separates_unpunctuated_sentences():
    result, _ = aligned(
        "已經完成大家休息",
        [(1 + i * 0.15, 1.12 + i * 0.15) for i in range(4)]
        + [(3.5 + i * 0.15, 3.62 + i * 0.15) for i in range(4)],
        pauses=[(1.60, 3.43)],
    )
    assert [c.text for c in result] == ["已經完成", "大家休息"]


def test_unanchored_chunk_start_does_not_hold_one_word_over_music():
    units = [
        SimpleNamespace(text="Joey", start_time=0.0, end_time=23.04),
        SimpleNamespace(text="hi", start_time=23.52, end_time=23.75),
    ]
    trace = {}
    cues = _alignment_to_cues(
        "Joey, hi!", units, source="test", offset=50, duration=30, chunk_index=2, trace=trace
    )
    assert 72 < cues[0].start < 73 and cues[0].uncertain
    assert trace["timing_repairs"][0]["method"] == "trimmed_unanchored_tail"
    assert units[0].start_time == 0


def test_chunk_origin_zero_follows_first_measured_onset_without_filling_introduction():
    cues, trace = aligned("大家好。", [(0, 0), (14.1, 14.25), (14.25, 14.6)], pauses=[(0.2, 13.8)])
    assert len(cues) == 1 and 13.8 <= cues[0].start < 14.1
    assert cues[0].uncertain and trace["timing_repairs"][0]["unit_start"] == 0


def test_plateau_with_one_stretched_unit_preserves_outer_anchors():
    times = [(0.8, 1.0), (1.0, 2.92)] + [(2.92, 2.92)] * 8 + [(2.92, 3.08), (3.12, 3.32)]
    fixed, affected, trace = repair_times(times, [1] * len(times), 6, [])
    assert fixed[0] == times[0] and fixed[-1] == times[-1]
    assert len(affected) == len(times) - 2
    assert min(b - a for a, b in fixed[1:-1]) >= 0.08
    assert len(trace) == 1


@pytest.mark.parametrize("filtered", [False, True])
def test_recognition_and_alignment_keep_same_selected_clock(filtered, tmp_path, monkeypatch):
    from contextlib import contextmanager
    from pathlib import Path

    from tingmu import media

    monkeypatch.setattr(media, "_cache_root", lambda: tmp_path)
    monkeypatch.setattr(media, "_qwen_python", lambda: Path("qwen-python"))
    monkeypatch.setattr(media, "portable_root", lambda: None)
    monkeypatch.setattr(media, "is_clean_install", lambda: False)
    expected_mix = tmp_path / "mix.wav"
    expected_vocals = tmp_path / "vocals.wav"
    extractions = []

    @contextmanager
    def prepare(source, **kwargs):
        assert kwargs["audio_track"] == 2 and kwargs["start"] == 70 and kwargs["duration"] == 12
        kwargs["metadata_out"]["bgm_method"] = "spleeter-2stems-fp16" if filtered else "off"
        yield expected_vocals if filtered else expected_mix

    def extract(source, output, track, start, duration, **kwargs):
        extractions.append((track, start, duration))
        return expected_mix

    def worker(request, *args):
        assert request["start"] == 70 and request["duration"] == 12
        assert Path(request["audio"]) == (expected_vocals if filtered else expected_mix)
        assert Path(request["alignment_audio"]) == expected_mix
        assert request["alignment_input"] == "original_track"
        Path(request["result"]).write_text("[]", encoding="utf8")
        return True, ""

    monkeypatch.setattr(media, "prepared_audio", prepare)
    monkeypatch.setattr(media, "extract_audio", extract)
    monkeypatch.setattr(media, "_run_worker", worker)
    media.transcribe(
        tmp_path / "media",
        model="qwen3-asr-1.7b",
        audio_track=2,
        start=70,
        duration=12,
        suppress_bgm=filtered,
        allow_model_downloads=True,
    )
    assert extractions == ([(2, 70, 12)] if filtered else [])
