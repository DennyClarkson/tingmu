from dataclasses import replace

import pytest

from tingmu.models import Cue, LanguagePair
from tingmu.pre_llm import _terminal, normalize
from tingmu.regroup import _boundary_quality, regroup
from tingmu.timeline import MAX_LINE_WIDTH, display_width, split_display_text


PHRASES = [
    ["I'm Mr.", "Low Profile,"],
    ["Mrs.", "White,"],
    ["So anyway, we talked about how I went to Mr.", "and Mrs.", "White's house."],
    ["Mr.", "Magorium's Wonder Emporium."],
    ["Mr.", "Lambert,"],
    ["We saw W.", "Wynne."],
    ["J.", "P.", "Wynne."],
    ["The U.S.", "Marshal arrived."],
]


@pytest.mark.parametrize("text,terminal", [
    ("I'm Mr.", False), ("Mrs.", False), ("J. P.", False), ("The U.S.", False),
    ("We can go.", True), ("So can I.", True), ("“White.”", True), ("走啦。", True),
])
def test_terminal_period(text, terminal):
    assert _terminal(text) is terminal


@pytest.mark.parametrize("parts", PHRASES)
def test_asr_title_seam_keeps_each_original_event_in_order(parts):
    rows = [Cue(str(i), i, i + 0.98, p) for i, p in enumerate(parts)]
    out, _, lineage = normalize(rows, source_kind="external_api", language="en")
    assert [c.text for c in out] == [" ".join(parts)]
    assert (out[0].start, out[0].end) == (rows[0].start, rows[-1].end)
    assert lineage["output_sources"][out[0].id] == [r.id for r in rows]
    assert [r["text"] for r in lineage["sources"]] == parts


@pytest.mark.parametrize("kind", ["sidecar", "embedded"])
def test_imported_caption_boundaries_survive(kind):
    rows = [Cue("a", 0, 1, "I'm Mr."), Cue("b", 1, 2, "White.")]
    assert normalize(rows, source_kind=kind, language="en")[0] == rows


@pytest.mark.parametrize("barrier", ["speaker", "turn", "quiet", "gap", "translation", "protected"])
def test_title_seam_respects_acoustic_barriers_and_curated_rows(barrier):
    rows = [Cue("a", 0, 1, "Mrs."), Cue("b", 1.05, 2, "White,")]
    kwargs = {}
    if barrier == "speaker":
        rows = [replace(row, speaker=row.id) for row in rows]
    elif barrier == "turn":
        kwargs["speaker_changes"] = [1.025]
    elif barrier == "quiet":
        kwargs["quiet_intervals"] = [(0.9, 1.3)]
    elif barrier == "gap":
        rows[1] = replace(rows[1], start=1.5)
    elif barrier == "translation":
        rows[1] = replace(rows[1], translation="怀特")
    else:
        kwargs["protected_ids"] = ["a"]
    out, _, _ = normalize(rows, source_kind="external_api", language="en", **kwargs)
    assert out == rows


def test_complete_replies_stay_separate_from_the_title_after_them():
    rows = [
        Cue("1", 0, 0.8, "Ready to go."),
        Cue("2", 0.8, 0.95, "Mr."),
        Cue("3", 0.95, 2, "White?"),
        Cue("4", 2, 3, "係呀。"),
    ]
    out, _, lineage = normalize(rows, source_kind="local_qwen", language="en")
    assert [r.text for r in out] == ["Ready to go.", "Mr. White?", "係呀。"]
    assert list(lineage["output_sources"].values()) == [["1"], ["2", "3"], ["4"]]


def test_no_reply_keeps_its_cue_and_number_prefix_keeps_its_number():
    rows = [Cue("a", 0, 0.4, "No."), Cue("b", 0.4, 0.55, "Mr."), Cue("c", 0.55, 1.7, "Lambert.")]
    out, _, _ = normalize(rows, source_kind="local_whisper", language="en")
    assert [r.text for r in out] == ["No.", "Mr. Lambert."]
    assert _terminal("No.", "Mr.") and not _terminal("No.", "5")
    regrouped, _, _ = _regroup(_units([r.text for r in rows], literal=True), _reply)
    assert [r.text for r in regrouped] == ["No.", "Mr. Lambert."]


@pytest.mark.parametrize("left,right", [('He wrote “Mr.”', "White?"), ("Mr.", "“Yes.”")])
def test_quoted_dialogue_remains_separate(left, right):
    rows = [Cue("a", 0, 1, left), Cue("b", 1, 1.18, right)]
    out, _, _ = normalize(rows, source_kind="external_api", language="en")
    assert [r.id for r in out] == ["a", "b"]


def test_already_joined_short_lead_in_and_initial_keep_each_fragment_once():
    rows = [Cue("a", 0, 0.9, "I met"), Cue("b", 0.9, 1.05, "Mr."), Cue("c", 1.05, 2, "Lambert,")]
    out, _, lineage = normalize(rows, source_kind="local_whisper", language="en")
    assert [r.text for r in out] == ["I met Mr. Lambert,"]
    assert lineage["output_sources"][out[0].id] == ["a", "b", "c"]
    rows = [Cue("a", 0, 1, "We heard"), Cue("b", 1.5, 1.6, "W."), Cue("c", 1.6, 2.4, "Wynne.")]
    out, _, _ = normalize(rows, source_kind="local_whisper", language="en", quiet_intervals=[(1.02, 1.48)])
    assert [r.text for r in out] == ["We heard", "W. Wynne."]


@pytest.mark.parametrize("sentence", [
    "We called on Mr. and Mrs. White yesterday.",
    "The U.S. Marshal greeted J. P. Wynne.",
    "We should see if the lights are on.",
])
def test_runon_splits_only_at_actual_sentence_end(sentence):
    rows = [Cue("a", 0, 8, sentence + " Then we went back to the station.")]
    out, _, _ = normalize(rows, source_kind="external_api", language="en")
    assert [r.text for r in out] == [sentence + " ", "Then we went back to the station."]


@pytest.mark.parametrize("name", ["Mr. White", "J. P. Wynne", "U.S. Marshal"])
def test_display_break_keeps_the_whole_name_with_its_title(name):
    prefix = "Our next visitor will be the esteemed "
    text = prefix + name + " and his colleagues."
    assert split_display_text(text) == [prefix, text[len(prefix):]]


def test_display_keeps_an_exact_width_word_whole_after_a_title():
    text = "Mrs. " + "A" * MAX_LINE_WIDTH + " arrived."
    parts = split_display_text(text)
    assert parts == ["Mrs. ", "A" * MAX_LINE_WIDTH, " arrived."]
    assert all(display_width(p) <= MAX_LINE_WIDTH for p in parts)


def _units(parts, **fields):
    return [dict(source_id=str(i), text=p, start=i, end=i + 1, **fields) for i, p in enumerate(parts)]


def _reply(payload):
    return {"groups": [
        dict(start_index=u["index"], end_index=u["index"], text=u["text"], reason="", uncertain=False)
        for u in payload["units"]
    ]}


def test_regroup_gap_at_separately_timed_initial_dot_does_not_promote_a_sentence_cut():
    units = _units(["The", " U.S", ".", " Marshal"], literal=True, block=0)
    for i, u in enumerate(units):
        u["raw_indices"] = [i]
    units[-1]["start"] += 0.4
    assert _boundary_quality(units, 3) == 0
    units[-1]["boundary_before"] = True
    assert _boundary_quality(units, 3) == 5


def _regroup(units, answer):
    return regroup(units, LanguagePair("en", "en"), provider="fixture", model="fixture",
                   client=lambda _, payload, __: answer(payload))


@pytest.mark.parametrize("fine", [True, False])
@pytest.mark.parametrize("fallback", [True, False])
def test_regroup_preserves_title_chain_and_preceding_sentence(fine, fallback):
    parts = ["We can go.", "We called on Mr.", "and Mrs.", "White's house."]
    out, _, ledger = _regroup(_units(parts, literal=fine), (lambda _: {"groups": []}) if fallback else _reply)
    assert [r.text for r in out] == [parts[0], " ".join(parts[1:])]
    assert ledger["output_sources"][out[-1].id] == ["1", "2", "3"]


def test_regroup_keeps_explicit_turn_at_title_and_rejects_coarse_cut_after_initial():
    units = _units(["I'm Mr.", "White."], literal=True)
    units[1]["boundary_before"] = True
    assert len(_regroup(units, _reply)[0]) == 2
    text = "We spoke to J. P. Wynne after the interview."
    cut = text.index("Wynne")
    units = _units([text])
    units[0]["end"] = 8

    def answer(_):
        return {"groups": [
            dict(start_index=0, end_index=0, start_char=lo, end_char=hi, text=text[lo:hi], reason="", uncertain=False)
            for lo, hi in ((0, cut), (cut, len(text)))
        ]}

    out, audit, _ = _regroup(units, answer)
    assert [r.text for r in out] == [text]
    assert audit["fallback_chunk_count"] == 1
