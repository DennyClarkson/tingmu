from dataclasses import asdict, replace
import subprocess
import sys

import pytest

from tingmu.models import Cue, LanguagePair, Token
from tingmu.pre_llm import normalize


def gate(cues, kind="local_qwen", lang="yue", **kwargs):
    return normalize(cues, source_kind=kind, language=lang, **kwargs)


@pytest.mark.parametrize("kind", ["sidecar", "embedded"])
def test_partial_vtt_roll_shares_suffix_then_grows_and_preserves_literal_evidence(kind):
    cues = [
        Cue("a", 1, 2, "We can hear", "<v A>We can hear"),
        Cue("b", 1.8, 2.8, "can hear\nthe sea"),
        Cue("c", 2.8, 4, "the sea today."),
    ]
    expected = [asdict(x) for x in cues]
    out, audit, lineage = gate(cues, kind, "en")
    assert [c.text for c in out] == ["We can hear\nthe sea today."]
    assert (out[0].start, out[0].end) == (1, 4)
    assert audit["rolling_fold_count"] == 2
    assert lineage["sources"] == expected == [asdict(c) for c in cues]
    assert lineage["output_sources"][out[0].id] == ["a", "b", "c"]
    assert "the sea" not in str(audit)
    assert gate(cues, kind, "en") == (out, audit, lineage)


def test_identical_content_can_be_a_spoken_repetition_and_separate_speakers_can_overlap():
    for kind in ("sidecar", "embedded", "local_qwen", "local_whisper", "local_api", "external_api", "legacy"):
        repeated = [Cue("a", 0, 0.6, "Yes!"), Cue("b", 0.6, 1.5, "Yes!")]
        assert gate(repeated, kind, "en")[0] == repeated
        spoken = [Cue("a", 0, 1.5, "我會返嚟", speaker="A"), Cue("b", 1.0, 2, "我會返嚟得切。", speaker="B")]
        assert gate(spoken, kind)[0] == spoken


@pytest.mark.parametrize(
    "left,right,expected",
    [
        ("入咗成個", "鐘。", "入咗成個鐘。"),
        ("呢層", "樓？", "呢層樓？"),
        ("冇冇手", "信。", "冇冇手信。"),
    ],
)
def test_vad_word_tails_survive_half_second_silence_and_weak_turn_signal(left, right, expected):
    cues = [Cue("phrase", 1, 2.1, left), Cue("orphan", 2.68, 2.76, right)]
    out, audit, lineage = gate(cues, "local_whisper", quiet_intervals=[(2.12, 2.66)], speaker_changes=[2.39])
    assert [c.text for c in out] == [expected]
    assert [c.original_text for c in out] == [expected]
    assert (out[0].start, out[0].end) == (1, 2.76)
    assert audit["coalesced_count"] == 1
    assert lineage["output_sources"][out[0].id] == ["phrase", "orphan"]
    assert [c.id for c in gate([replace(cues[0], speaker="A"), replace(cues[1], speaker="B")])[0]] == [
        "phrase",
        "orphan",
    ]
    assert [c.id for c in gate([cues[0], replace(cues[1], start=4.1, end=4.18)])[0]] == ["phrase", "orphan"]
    assert [c.id for c in gate(cues, quiet_intervals=[(1.8, 3.8)])[0]] == ["phrase", "orphan"]


def test_measured_pause_blocks_ambiguous_fragments_and_terminal_dialogue_survives_dense_turns():
    raw = [
        Cue("1", 377.6, 377.68, "屈！"),
        Cue("2", 377.68, 379.92, "你買咗呢層樓？"),
        Cue("3", 2427.52, 2427.707, "四萬到三千九啦。"),
        Cue("4", 2427.707, 2427.76, "玩咩？"),
        Cue("5", 2427.76, 2429.36, "係講真㗎，三千九咁嘅你翻嚟咩？"),
    ]
    out, _, _ = gate(raw)
    assert [c.id for c in out] == [c.id for c in raw]
    rows = [Cue("a", 1, 1.14, "天"), Cue("b", 1.5, 2.4, "開始落雨")]
    assert len(gate(rows, quiet_intervals=[(1.15, 1.48)])[0]) == 2
    for a, b in (
        (Cue("a", 304.22, 304.48, "死蠢"), Cue("b", 304.88, 306, "我怎料閒定淡，")),
        (Cue("a", 2075, 2075.15, "平"), Cue("b", 2075.15, 2077.63, "投")),
    ):
        assert [c.id for c in gate([a, b])[0]] == ["a", "b"]


def test_well_timed_unpunctuated_fragments_remain_available_for_semantic_regrouping():
    cues = [Cue("a", 1, 2.1, "We stayed because"), Cue("b", 2.16, 3.56, "the train was late.")]
    assert gate(cues, "external_api", "en")[0] == cues
    assert len(gate(cues, "external_api", "en", speaker_changes=[2.13])[0]) == 2
    assert len(gate([replace(cues[0], text="We stayed home"), cues[1]], "external_api", "en")[0]) == 2
    assert len(gate(cues, "sidecar", "en")[0]) == 2


def test_measured_silence_and_fragment_flag_survive_document_save(tmp_path):
    import numpy as np

    from tingmu.acoustic import quiet_intervals
    from tingmu.models import StudyDocument
    from tingmu.storage import Store

    energy = np.ones(160, dtype=np.float32)
    energy[100:125] = 0
    pauses = quiet_intervals(energy)
    language = LanguagePair("zh", "en")
    source = [Cue("a", 0.1, 1.0, "board-"), Cue("b", 1.02, 1.5, "ing.")]
    cues, audit, lineage = gate(source, "external_api", language, quiet_intervals=pauses)
    assert len(cues) == 1 and cues[0].text == "board-ing."
    doc = StudyDocument(str(tmp_path / "video.mp4"), language, cues, metadata={
        "asr_quiet_intervals": pauses, "pre_llm_audit": audit, "pre_llm_lineage": lineage,
    })
    store = Store(tmp_path / "workspace")
    try:
        store.save_document(doc)
        restored = store.load_document(doc.media_path, language)
        assert restored.cues[0].uncertain is False
        assert restored.metadata["asr_quiet_intervals"] == [[2.0, 2.5]]
    finally:
        store.close()


def test_legacy_dictionary_tokens_are_rebuilt_only_for_changed_text():
    rows = [
        Cue("a", 0, 1, "They keep moving", tokens=[Token("They")]),
        Cue("b", 0.8, 2, "They keep moving forward.", tokens=[Token("forward")]),
        Cue("c", 4, 5, "Wait!", tokens=[Token("Wait")]),
    ]
    out, _, lineage = gate(rows, "sidecar", "en")
    assert [c.text for c in out] == ["They keep moving forward.", "Wait!"]
    assert out[0].tokens == [] and out[1].tokens == rows[2].tokens
    assert lineage["sources"] == [asdict(c) for c in rows]


def test_local_fragment_fallback_is_independent_of_language_or_dictionaries():
    # Same source evidence yields the same boundaries for an unknown language.
    rows = [Cue("a", 0, 1.1, "冇冇手"), Cue("b", 1.12, 1.2, "信。")]
    ordinary = gate(rows, "local_qwen", "yue")
    assert gate(rows, "local_qwen", "und") == ordinary
    assert len(ordinary[0]) == 1
    code = "import sys; import tingmu.pre_llm; assert 'pycantonese' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize(
    "lang,parts",
    [
        ("en", ["The evening train is finally here. ", "We should head home together."]),
        ("es", ["Las estrellas brillan en el cielo. ", "Podemos regresar por la mañana."]),
        ("ar", ["هل سنعود إلى المدينة اليوم؟ ", "تنتظرنا مجموعة من الأصدقاء هناك."]),
        ("hi", ["आज मौसम बहुत सुहाना है। ", "हम लोग कल सुबह घर लौटेंगे।"]),
        ("ja", ["今日はとてもよい天気でした。", "明日の朝みんなで家に帰ります。"]),
    ],
)
def test_explicit_sentence_split_is_literal_and_each_child_gets_reading_time(lang, parts):
    c = Cue("source", 11, 17, "".join(parts), "raw evidence")
    result, audit, lineage = gate([c], "external_api", LanguagePair("en", lang))
    assert [x.text for x in result] == parts
    assert audit["split_runon_count"] == 1
    assert all(x.uncertain and x.end - x.start >= 0.6 for x in result)
    assert result[0].start == 11 and result[-1].end == 17
    assert all(source_ids == ["source"] for source_ids in lineage["output_sources"].values())
    assert lineage["sources"][0]["original_text"] == "raw evidence"
    assert len(gate([replace(c, end=12)], "external_api", lang)[0]) == 1


def test_split_assigns_explicit_pause_or_abstains_when_timing_evidence_disagrees():
    c = Cue("x", 0, 8, "We waited here for a while. We waited here for a while.")
    result, _, lineage = gate([c], "local_whisper", "en", quiet_intervals=[(3.2, 4.8)])
    assert [(x.start, x.end) for x in result] == [(0, 3.2), (4.8, 8)]
    assert all(v["timing_basis"] == "pause_bounded_estimate" for v in lineage["outputs"].values())
    result, _, _ = gate([c], "local_whisper", "en", quiet_intervals=[(0.1, 1.5)])
    assert len(result) == 1 and result[0].uncertain


def test_fast_text_bounded_to_next_event_and_micro_overlap_does_not_hide_speaker_dialogue():
    x = Cue("fast", 1, 1.08, "原始對齊壓縮的時間", "保留原聲學稿")
    y = Cue("next", 1.55, 2.62, "下一句。")
    z = Cue("third", 2.56, 4, "最後一句。")
    result, audit, lineage = gate([x, y, z])
    assert result[0].uncertain and result[0].id == "fast" and 1.08 < result[0].end < y.start
    assert result[1].end == 2.56 and result[2].start == 2.56
    assert audit["repaired_timing_count"] == 2 and lineage["sources"][0] == asdict(x)
    assert gate([x, y], quiet_intervals=[(1.09, 1.48)])[0][0].end == 1.08
    assert gate([x, y], speaker_changes=[1.2])[0][0].end == 1.08
    slow = Cue("slow", 5, 14, "喂。")
    anchored, _, _ = gate([slow], quiet_intervals=[(4.9, 8), (8.8, 14.2)])
    assert (anchored[0].start, anchored[0].end) == (8, 8.8) and anchored[0].uncertain
    unresolved, _, _ = gate([slow])
    assert (unresolved[0].start, unresolved[0].end) == (5, 14) and unresolved[0].uncertain


def test_raw_ledger_restores_drops_and_protected_authored_units():
    rows = [
        Cue("empty", 1, 1, " "),
        Cue("symbol", 2, 2.01, "♪"),
        Cue("bracket", 2.01, 3, "[Music]"),
        Cue("voice", 3.2, 3.8, "Come"),
        Cue("manual", 3.8, 3.88, "in!"),
        Cue("paired", 4, 4.1, "Danke!", translation="Thanks!"),
        Cue("foreign", 5, 6, "[Nairobi]"),
    ]
    result, audit, lineage = gate(rows, "sidecar", "en", protected_ids={"manual"})
    assert lineage["dropped_ids"] == ["empty", "symbol", "bracket"] and audit["dropped_count"] == 3
    assert [c.id for c in result] == ["voice", "manual", "paired", "foreign"]
    assert result[1:3] == rows[4:6]
    assert lineage["sources"] == [asdict(c) for c in rows]
    assert sorted(
        lineage["dropped_indices"] + [i for row in lineage["outputs"].values() for i in row["source_indices"]]
    ) == list(range(len(rows)))


def test_severe_collapse_uses_only_preceding_bounded_interval_when_next_cue_is_contiguous():
    cues = [
        Cue("before", 1313.1, 1314.48, "上一句。"),
        Cue("long", 1315.28, 1315.44, "明明係成句對白但係全部擠埋一齊。"),
        Cue("next", 1315.44, 1316.6, "後面一句。"),
    ]
    out, audit, lineage = gate(cues)
    assert out[0] == cues[0] and out[2] == cues[2]
    assert out[1].start >= cues[0].end + 0.08 and out[1].end == cues[1].end
    assert out[1].end - out[1].start >= 0.6 and out[1].uncertain
    assert audit["repaired_timing_count"] == 1 and "unresolved_timing_count" not in audit
    assert lineage["sources"] == [asdict(c) for c in cues]
    after_barriers, _, _ = gate(cues, quiet_intervals=[(1314.4, 1314.77)], speaker_changes=[1314.85])
    assert 1314.85 <= after_barriers[1].start < cues[1].start
    protected, _, _ = gate(cues, protected_ids={"long"})
    assert protected == cues


@pytest.mark.parametrize("block", ["no_gap", "no_previous", "long_pause", "speaker", "quiet_at_onset"])
def test_severe_without_neighbor_evidence_stays_at_original_time_and_is_counted(block):
    before = Cue("before", 1, 3, "上一句。")
    c = Cue("long", 4, 4.133, "呢一大句唔能夠全部喺一瞬間講晒。")
    next_cue = Cue("after", 4.133, 6, "後面一句。")
    rows = [before, c, next_cue]
    kwargs = {}
    if block == "no_gap":
        rows[0] = replace(before, end=4)
    elif block == "no_previous":
        rows = rows[1:]
    elif block == "long_pause":
        rows[0] = replace(before, start=0, end=1)
    elif block == "speaker":
        rows[0], rows[1] = replace(before, speaker="A"), replace(c, speaker="B")
    else:
        kwargs["quiet_intervals"] = [(3.4, 4.1)]
    out, audit, _ = gate(rows, **kwargs)
    kept = next(row for row in out if row.id == c.id)
    assert (kept.start, kept.end) == (4, 4.133) and kept.uncertain
    assert audit["unresolved_timing_count"] == audit["counts"]["timing_unresolved"] == 1


def test_contiguous_ordinary_short_reply_with_leading_space_keeps_its_onset():
    rows = [
        Cue("before", 1, 2, "前面一句。"),
        Cue("reply", 3, 3.08, "屈！"),
        Cue("after", 3.08, 4, "你講咩話？"),
    ]
    out, audit, _ = gate(rows)
    assert out == rows and "unresolved_timing_count" not in audit


def test_duplicate_input_and_generated_id_collisions_remain_deterministic():
    data = [Cue("a", 0, 1, "入咗成個"), Cue("b", 1.01, 1.08, "鐘。")]
    generated = gate(data)[0][0].id
    rows = data + [
        Cue(generated, 3, 4, "另一句。"),
        Cue("duplicate", 6, 7, "係。"),
        Cue("duplicate", 9, 10, "係。"),
    ]
    result, audit, lineage = gate(rows)
    assert len(set(c.id for c in result)) == len(result) and result[1].id == generated
    assert result[0].id != generated
    assert gate(rows) == (result, audit, lineage)
    assert {tuple(r["source_indices"]) for r in lineage["outputs"].values()} == {(0, 1), (2,), (3,), (4,)}


@pytest.mark.parametrize("n,step,duration,width", [(1200, 0.1, 1, 4), (1052, 0.2, 0.2, 3)])
def test_sliding_caption_stream_preserves_every_new_word_once(n, step, duration, width):
    words = [f"w{k}" for k in range(n + width - 1)]
    rows = [Cue(str(i), i * step, i * step + duration, " ".join(words[i : i + width])) for i in range(n)]
    result, _, lineage = gate(rows, "sidecar", "en")
    assert " ".join(c.text for c in result).split() == words
    assert all(a.end <= b.start for a, b in zip(result, result[1:]))
    assert all(0.6 <= c.end - c.start <= 8 for c in result)
    assert set(i for r in lineage["outputs"].values() for i in r["source_indices"]) == set(range(n))


def test_overlapping_api_updates_that_retract_then_expand_do_not_duplicate_words():
    rows = [
        Cue("a", 0, 1.6, "We never knew"),
        Cue("b", 0.7, 1.9, "We never"),
        Cue("c", 1.1, 2.4, "We never knew why."),
    ]
    for source in ("sidecar", "external_api", "local_api", "legacy"):
        out, _, lineage = gate(rows, source, "en")
        assert [c.text for c in out] == ["We never knew why."]
        assert lineage["output_sources"][out[0].id] == ["a", "b", "c"]
