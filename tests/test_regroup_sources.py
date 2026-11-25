import json

import pytest

from tingmu.models import Cue
from tingmu.regroup_sources import build_units


def run(cues, kind, folder, audit=None, *, protected=(), metadata=None):
    meta = dict(metadata or {})
    if audit is not None:
        path = folder / "private.json"
        path.write_text(json.dumps(audit), encoding="utf-8")
        meta["asr_audit_path"] = str(path)
    return build_units(cues, source_kind=kind, metadata=meta, audit_dir=folder, protected_ids=protected)


def qwen_block():
    return {
        "complete": True,
        "chunks": [
            {
                "start": 10.0,
                "duration": 4.0,
                "text": " «Wi-Fi», 你好！ ",
                "alignment": [
                    {"text": "WiFi", "start_time": 0.5, "end_time": 1.1},
                    {"text": "你", "start_time": 1.2, "end_time": 1.2},
                    {"text": "好", "start_time": 1.2, "end_time": 1.55},
                ],
                "cue_timings": [
                    {"cue_id": "first", "unit_start": 0, "unit_end": 1},
                    {"cue_id": "last", "unit_start": 1, "unit_end": 3},
                ],
            }
        ],
    }


def test_qwen_source_spelling_virtual_ticks_and_immutable_authored_unit(tmp_path):
    cues = [Cue("first", 10.5, 11.1, "«Wi-Fi»,", " «Wi-Fi», "), Cue("last", 11.2, 11.55, "你好！", "你好！ ")]
    units, meta = run(cues, "local_qwen", tmp_path, qwen_block(), metadata={"speaker_changes": [11.15]})
    assert [u["text"] for u in units] == [" «Wi-Fi», ", "你好！ "]
    assert units[0]["raw_indices"] == [0] and units[1]["raw_indices"] == [1, 2]
    assert units[1]["timing_basis"] == "neighbor_anchor" and units[1]["uncertain"]
    assert (
        units[1]["speaker_change_hint"]
        and not units[1]["boundary_before"]
        and units[0]["literal"]
        and all(u["start"] < u["end"] for u in units)
    )
    assert meta["fallback_source_count"] == 0
    cues[1].text = "保存的修改。"
    units, _ = run(cues, "local_qwen", tmp_path, qwen_block(), protected={"last"})
    assert (
        units[1]["text"] == "保存的修改。"
        and units[1]["original_text"] == "你好！ "
        and units[1]["protected"]
    )


def test_legacy_qwen_without_cue_ids_binds_only_when_window_matches(tmp_path):
    data = qwen_block()
    data["chunks"][0].pop("cue_timings")
    cue = Cue("legacy", 10.5, 11.55, "unused", " «Wi-Fi», 你好！ ")
    units, meta = run([cue], "local_qwen", tmp_path, data)
    assert meta["fallback_source_count"] == 0 and len(units) == 2
    cue.original_text = "different"
    units, meta = run([cue], "local_qwen", tmp_path, data)
    assert meta["fallback_source_count"] == 1 and units[0]["text"] == "different"


def test_whisper_words_remain_exact_and_legacy_segments_allow_model_subdivision(tmp_path):
    raw = {
        "complete": True,
        "segments": [
            {
                "text": "  Hola, señor.  ",
                "cue_ids": ["a", "b"],
                "start": 9.0,
                "end": 10.0,
                "words": [
                    {"text": " Hola,", "start": 9.0, "end": 9.3},
                    {"text": " señor.", "start": 9.5, "end": 10.0},
                ],
            },
            {"text": "Hi again.", "cue_ids": ["c"], "start": 10.0, "end": 13.0, "words": []},
        ],
    }
    cues = [
        Cue("a", 9, 9.3, "Hola,", "  Hola,"),
        Cue("b", 9.5, 10, "señor.", " señor.  "),
        Cue("c", 10.0, 13.0, "Hi again."),
    ]
    units, report = run(cues, "local_whisper", tmp_path, raw)
    assert [u["text"] for u in units] == [c.original_text for c in cues]
    assert [u["timing_basis"] for u in units] == ["whisper_word", "whisper_word", "whisper_segment"]
    assert [u["literal"] for u in units] == [True, True, False] and report["fallback_source_count"] == 0


@pytest.mark.parametrize(
    "error", ["truncated", "incomplete", "invalid", "traversal", "outside", "regression"]
)
def test_untrusted_or_incomplete_audits_use_visible_source_fallback(tmp_path, error):
    folder = tmp_path / "asr-audit"
    folder.mkdir()
    p = folder / "a.json"
    data = qwen_block()
    if error == "incomplete":
        data["complete"] = False
    elif error == "invalid":
        data["chunks"][0]["alignment"][0]["start_time"] = float("nan")
    elif error == "regression":
        data["chunks"][0]["cue_timings"][1]["unit_start"] = 0
    p.write_text("{" if error == "truncated" else json.dumps(data), "utf8")
    if error == "outside":
        outside = tmp_path / "other.json"
        outside.write_text(json.dumps(data), "utf8")
        p = outside
    elif error == "traversal":
        p = folder / ".." / "asr-audit" / "a.json"
    cues = [Cue("first", 10.5, 11.1, "raw", " «Wi-Fi», ")]
    units, report = run(cues, "local_qwen", folder, metadata={"asr_audit_path": str(p)})
    assert [u["timing_basis"] for u in units] == ["source_span"] and units[0]["uncertain"]
    assert report["audit_status"] != "read" and report["fallback_source_count"] == 1


def test_caption_rolling_evidence_is_not_split_and_paired_events_are_protected(tmp_path):
    cues = [
        Cue("a", 0, 1.2, "We go", "<i>We go</i>"),
        Cue("b", 0.8, 1.8, "We go home."),
        Cue("c", 2, 3, "¡Sí!", translation="Yes!"),
        Cue("d", 4, 5, "♪"),
    ]
    metadata = {"subtitle_import": {"events": {c.id: {"event_index": i + 5} for i, c in enumerate(cues)}}}
    units, report = run(cues, "sidecar", tmp_path, metadata=metadata)
    assert [u["text"] for u in units] == ["We go home.", "¡Sí!"]
    assert units[0]["source_indices"] == [0, 1] and units[0]["event_indices"] == [5, 6]
    assert units[1]["protected"] and units[1]["boundary_before"] and report["dropped_indices"] == [3]


@pytest.mark.parametrize("kind", ["external_api", "local_api"])
def test_service_segments_keep_raw_clock_and_worker_detected_quiet_barriers(kind, tmp_path):
    cs = [Cue("a", 123, 124, "Start", "  Start"), Cue("b", 125, 126, "again", " again")]
    meta = {
        "asr_quiet_intervals": [[124.1, 124.8]],
        "asr_segment_sources": {"a": {"chunk": 4, "segment_index": 8}, "b": {"chunk": 4, "segment_index": 9}},
    }
    units, report = run(cs, kind, tmp_path, metadata=meta)
    assert [(u["text"], u["raw_index"], u["block"], u["timing_basis"]) for u in units] == [
        ("  Start", 8, 4, "api_segment"),
        (" again", 9, 4, "api_segment"),
    ]
    assert (
        units[1]["quiet_hint_ms"] == 700
        and not units[1]["boundary_before"]
        and report["rolling_fold_count"] == 0
    )


def test_midword_inferred_turn_and_internal_quiet_remain_soft(tmp_path):
    # Four contiguous 60ms raw ticks. A change inside the middle tick should
    # never force its numeral/name to become an independent subtitle.
    raw = {
        "complete": True,
        "chunks": [
            {
                "start": 100,
                "duration": 3,
                "text": "四千九百。",
                "alignment": [
                    {"text": ch, "start_time": i * 0.06, "end_time": (i + 1) * 0.06}
                    for i, ch in enumerate("四千九百")
                ],
                "cue_timings": [{"cue_id": "a", "unit_start": 0, "unit_end": 4}],
            }
        ],
    }
    c = Cue("a", 100.0, 100.24, "四千九百。")
    u, _ = run(
        [c],
        "local_qwen",
        tmp_path,
        raw,
        metadata={"speaker_changes": [100.08], "asr_quiet_intervals": [[99.6, 100.06]]},
    )
    assert "".join(a["text"] for a in u) == "四千九百。"
    assert sum(a["speaker_change_hint"] for a in u) == 1 and not any(a["boundary_before"] for a in u)


def test_measured_long_silence_and_explicit_speakers_still_partition(tmp_path):
    cues = [
        Cue("a", 0, 1, "甲", speaker="A"),
        Cue("b", 1.1, 2, "乙", speaker="B"),
        Cue("c", 3.5, 3.9, "丙", speaker="B"),
        Cue("d", 5.9, 6.1, "丁", speaker="B"),
    ]
    u, _ = run(cues, "external_api", tmp_path, metadata={"asr_quiet_intervals": [[2.1, 3.45]]})
    assert [a["boundary_before"] for a in u] == [False, True, True, True]
    assert [a["quiet_hint_ms"] for a in u] == [0, 0, 1350, 0]


def test_zero_extent_source_is_conserved_without_inventing_acoustic_time(tmp_path):
    raw = Cue("zero", 1.25, 1.25, "still here")
    [u], _ = run([raw], "sidecar", tmp_path)
    assert (u["text"], u["start"], u["end"], u["protected"], u["timing_basis"]) == (
        raw.text,
        raw.start,
        raw.end,
        True,
        "unplaced_source",
    )


@pytest.mark.parametrize("kind", ["sidecar", "embedded", "local_api", "external_api"])
def test_coarse_smart_units_apply_micro_overlap_hygiene_before_request(tmp_path, kind):
    original = [Cue("first", 0, 1.035, "One line."), Cue("next", 1, 3, "Another line.")]
    units, _ = run(original, kind, tmp_path)
    assert units[0]["end"] == units[1]["start"] == 1
    assert units[0]["uncertain"] and units[0]["timing_basis"] == "neighbor_bounded"
    assert original[0].end == 1.035
