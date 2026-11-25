from copy import deepcopy
from threading import Event

import pytest

from tingmu.models import Cue, LanguagePair, Token
from tingmu.providers import ProviderError, RequestCancelled
from tingmu.regroup import VERSION, regroup


def unit(text, start=0, end=1, ident="a", **fields):
    return dict(text=text, start=start, end=end, source_id=ident, **fields)


def group(a, b, text, *, lo=None, hi=None, reason="", uncertain=False):
    return {
        "start_index": a,
        "end_index": b,
        "start_char": lo,
        "end_char": hi,
        "text": text,
        "reason": reason,
        "uncertain": uncertain,
    }


def run(units, complete, *, target="en", **kwargs):
    return regroup(
        units, LanguagePair("en", target), provider="fixture", model="explicit", client=complete, **kwargs
    )


def singletons(system, payload, schema):
    return {"groups": [group(u["index"], u["index"], u["text"]) for u in payload["units"]]}


@pytest.mark.parametrize(
    "target,parts,text",
    [
        ("en", ["We stayed because", "the train was late."], "We stayed because the train was late."),
        ("fr", ["Nous sommes", "arrivés."], "Nous sommes arrivés."),
        ("ja", ["明日", "また。"], "明日また。"),
        ("yue", ["呢層", "樓？"], "呢層樓？"),
        ("ar", ["هذا", "جميل."], "هذا جميل."),
        ("ko", ["오늘은", "맑아요."], "오늘은 맑아요."),
        ("th", ["วัน", "นี้"], "วันนี้"),
    ],
)
def test_groups_unicode_without_changing_source_times(target, parts, text):
    units = [unit(parts[0], 300.125, 300.6), unit(parts[1], 300.68, 302.04, "b")]

    def complete(system, payload, schema):
        assert [u["index"] for u in payload["units"]] == [0, 1]
        assert [u["gap_ms"] for u in payload["units"]] == [0, 80]
        assert payload["units"][0]["start_ms"] == 300125
        return {"groups": [group(0, 1, text)]}

    cues, audit, lineage = run(units, complete, target=target)
    (cue,) = cues
    assert (cue.text, cue.original_text, cue.start, cue.end) == (text, text, 300.125, 302.04)
    assert audit["fallback_chunk_count"] == 0
    assert lineage["output_sources"][cue.id] == ["a", "b"]
    assert lineage["outputs"][cue.id]["unit_range"] == [0, 1]
    assert run(units, complete, target=target)[0] == cues


def test_acoustic_pieces_exact_across_source_cues_in_same_block():
    units = [
        unit("un", literal=True, block=2, raw_indices=[5]),
        unit("usual", 1, 2, "b", literal=True, block=2, raw_indices=[6]),
    ]
    assert run(units, lambda *_: {"groups": [group(0, 1, "unusual")]})[0][0].original_text == "unusual"


@pytest.mark.parametrize(
    "invalid",
    [
        [group(0, 0, "first")],
        [group(1, 1, "second"), group(0, 0, "first")],
        [group(0, 1, "first second"), group(1, 1, "second")],
        [{**group(0, 1, "first second"), "end": 40.2}],
        [group(False, 1, "first second")],
        [group(0, 1, "Invented submarine attack.", reason="guess")],
        [group(0, 1, "first 4th", reason="fix")],
        [group(0, 0, "first"), group(1, 1, "seco", hi=4)],
    ],
)
def test_bad_groups_are_not_partially_applied(invalid):
    units = [unit("first"), unit("second", 1.1, 2, "b")]

    def client(system, payload, schema):
        if len(payload["units"]) == 2:
            return {"groups": invalid}
        return {"groups": []}

    cues, audit, lineage = run(units, client)
    assert [(c.id, c.start, c.end, c.text) for c in cues] == [("a", 0, 1, "first"), ("b", 1.1, 2, "second")]
    assert audit["fallback_chunk_count"] == 1
    assert all(record["kind"] == "fallback" for record in lineage["outputs"].values())
    assert audit["request_count"] == 6


def test_partial_reply_can_be_recovered_by_recursive_smaller_requests():
    units = [unit("one"), unit("two", 1, 2, "b"), unit("three", 2, 3, "c"), unit("four", 3, 4, "d")]
    sizes = []

    def complete(system, payload, schema):
        batch = payload["units"]
        sizes.append(len(batch))
        if len(batch) > 2:
            return {"groups": [group(0, 0, batch[0]["text"])]}
        return {"groups": [group(0, 1, " ".join(r["text"] for r in batch))]}

    cues, audit, _ = run(units, complete)
    assert sizes == [4, 4, 2, 2]
    assert [c.text for c in cues] == ["one two", "three four"]
    assert not audit["fallback_chunk_count"]


def test_coarse_offsets_are_codepoints_but_binding_uses_display_width():
    text = "你好！ Hi e\u0301."
    split = text.index(" ") + 1
    units = [unit(text, 5, 15)]

    def complete(*_):
        return {"groups": [group(0, 0, text[:split], hi=split), group(0, 0, text[split:], lo=split)]}

    cues, _, lineage = run(units, complete)
    assert [c.start for c in cues] == pytest.approx([5, 5 + 10 * 7 / 12])
    assert [c.end for c in cues] == pytest.approx([5 + 10 * 7 / 12, 15])
    assert "".join(c.original_text for c in cues) == text
    assert len({c.id for c in cues}) == 2
    assert all(r["timing_basis"] == "text_proportional" for r in lineage["outputs"].values())


@pytest.mark.parametrize(
    "text,cut,duration,literal",
    [
        ("Hello there", 2, 5, False),
        ("A. Long phrase follows.", 2, 1.2, False),
        ("short words", 6, 4, True),
        ("cafe\u0301. End.", 4, 5, False),
    ],
)
def test_unsafe_coarse_or_fine_offsets_retain_exact_original(text, cut, duration, literal):
    units = [unit(text, 0, duration, literal=literal)]

    def complete(*_):
        return {"groups": [group(0, 0, text[:cut], hi=cut), group(0, 0, text[cut:], lo=cut)]}

    cues, audit, _ = run(units, complete)
    assert [(c.start, c.end, c.text) for c in cues] == [(0, duration, text)]
    assert audit["fallback_chunk_count"] == 1


def test_barriers_share_a_request_but_cannot_share_a_caption():
    units = [
        unit("Hi", 0, 1, speaker="s1"),
        unit("No.", 1.1, 2, "b", speaker="s2"),
        unit("Later", 5, 6, "c"),
        unit("yes", 6.2, 7, "d", boundary_before=True),
    ]
    wires = []

    def complete(system, payload, schema):
        wires.append(payload["units"])
        if len(wires) == 1:
            return {"groups": [group(0, 3, "Hi No. Later yes")]}
        return singletons(system, payload, schema)

    cues, audit, _ = run(units, complete)
    assert len(wires) == 2 and [u["boundary_before"] for u in wires[0]] == [False, True, True, True]
    assert [c.id for c in cues] == ["a", "b", "c", "d"]
    assert audit["mandatory_boundary_count"] == 3


def test_protected_cue_is_excluded_and_retained_in_full_and_lineage_includes_rolling_inputs():
    sources = [
        Cue("a", 0, 1, "Hi"),
        Cue("b", 1, 2, "A"),
        Cue("lock", 3, 4, "Fixed", "Raw", translation="原文", tokens=[Token("Fixed")]),
    ]
    units = [unit("Hi A", source_indices=[0, 1]), unit("Fixed", 3, 4, "lock", source_index=2)]
    sent = []

    def complete(system, payload, schema):
        sent.extend(r["text"] for r in payload["units"])
        return singletons(system, payload, schema)

    cues, _, lineage = run(units, complete, protected_ids={"lock"}, source_cues=sources)
    assert sent == ["Hi A"] and cues[1] == sources[2] and cues[1] is not sources[2]
    assert lineage["output_sources"][cues[0].id] == ["a", "b"]


def test_conservative_correction_is_traceable_and_numbers_and_particles_are_preserved():
    def propose(text):
        return lambda *_: {"groups": [group(0, 0, text, reason="Clear spelling error.")]}

    cues, audit, lineage = run([unit("He recieve a letter.")], propose("He receive a letter."))
    assert (cues[0].text, cues[0].original_text) == ("He receive a letter.", "He recieve a letter.")
    assert audit["corrected_count"] == 1 and lineage["outputs"]["a"]["reason"]
    for text, after, target in [
        ("There are 3 clocks.", "There are 4 clocks.", "en"),
        ("我而家要返屋企喇喎", "我而家要返屋企喇呀", "yue"),
    ]:
        assert run([unit(text)], propose(after), target=target)[0][0].text == text


def test_cancel_keeps_complete_checkpoint_only_then_resumes_without_resending():
    units = [unit("saved"), unit("locked", 1, 2, "x", protected=True), unit("next", 2, 3, "b")]
    stop = Event()
    saved = []

    def checkpoint(state):
        saved.append(deepcopy(state))
        stop.set()
        state["completed"].clear()

    with pytest.raises(InterruptedError):
        run(units, singletons, cancelled=stop.is_set, max_workers=1, checkpoint=checkpoint)
    assert saved[-1]["version"] == VERSION and list(saved[-1]["completed"]) == ["0"]
    sent = []

    def client(system, payload, schema):
        sent.extend(u["text"] for u in payload["units"])
        return singletons(system, payload, schema)

    cues, _, _ = run(units, client, resume=saved[-1])
    assert sent == ["next"] and [c.text for c in cues] == ["saved", "locked", "next"]
    units[0]["text"] = "changed"
    run(units, client, resume=saved[-1])
    assert "changed" in sent


def test_provider_failures_and_cancellation_are_not_content_retries():
    calls = []

    def client(*_):
        calls.append(1)
        raise ProviderError("configuration unavailable", category="credentials")

    with pytest.raises(ProviderError, match="configuration unavailable"):
        run([unit("hello")], client)
    assert len(calls) == 1

    def cancelled(*_):
        raise RequestCancelled("cancel")

    with pytest.raises(InterruptedError):
        run([unit("hello")], cancelled)


def test_failed_acoustic_subdivisions_restore_the_source_once_and_retain_its_exact_times():
    source = Cue("a", 10, 14, "One two three four.")
    units = [
        unit(t, 10 + i, 10.5 + i, literal=True) for i, t in enumerate(["One", " two", " three", " four."])
    ]
    cues, audit, _ = run(units, lambda *_: {"groups": []}, source_cues=[source])
    assert [(c.id, c.start, c.end, c.text) for c in cues] == [("a", 10, 14, source.text)]
    assert audit["fallback_chunk_count"] == 1


@pytest.mark.parametrize(
    "raw,bad",
    [
        ("I can go.", "I can't go."),
        ("I had a cat.", "I had cat."),
        ("There is one.", "There is one.🔑"),
        ("We all come.", "Weall come."),
    ],
)
def test_small_but_semantically_unsupported_edit_falls_back(raw, bad):
    cues, audit, _ = run([unit(raw)], lambda *_: {"groups": [group(0, 0, bad, reason="spelling")]})
    assert cues[0].text == raw and audit["fallback_chunk_count"] == 1


def test_many_turns_are_bounded_by_duration_without_per_turn_calls():
    units = [unit("Hi.", i, i + 0.95, str(i), speaker=f"speaker-{i % 2}") for i in range(215)]
    sizes = []

    class Client:
        last_response_metadata = {"returned_model": "provider/actual-2", "credential": "do not store"}

        def complete(self, system, payload, schema):
            batch = payload["units"]
            assert batch[-1]["end_ms"] - batch[0]["start_ms"] <= 90000
            sizes.append(len(batch))
            return singletons(system, payload, schema)

    cues, audit, _ = run(units, Client(), max_workers=1)
    assert len(cues) == 215 and len(sizes) == 3 and min(sizes[:2]) >= 60
    assert audit["responses"] == [{"returned_model": "provider/actual-2"}] * 3


def test_unplaced_legacy_source_is_exact_and_never_sent():
    source = Cue("old", 3, 3, "Hello")
    row = unit(source.text, 3, 3, "old", protected=True, timing_basis="unplaced_source")
    cues, audit, lineage = run([row], None, source_cues=[source])
    assert cues == [source] and not audit["request_count"]
    assert lineage["outputs"]["old"]["timing_basis"] == "unplaced_source"
    with pytest.raises(ValueError):
        run([row], singletons)


def test_oversized_single_coarse_source_is_preserved_without_resolving_model_credentials():
    large = unit("Large " * 900)
    cues, audit, _ = regroup([large], LanguagePair(), provider="", model=None)
    assert cues[0].text == large["text"] and not audit["request_count"] and audit["fallback_chunk_count"] == 1


@pytest.mark.parametrize(
    "provider,model,profile",
    [
        ("", "", None),
        ("deepseek", "", None),
        ("deepseek", "explicit", {}),
        ("deepseek", "explicit", {"model": "other", "provider": "deepseek", "base_url": "mock"}),
    ],
)
def test_invalid_explicit_assignment_fails_before_model_resolution(provider, model, profile):
    with pytest.raises(ProviderError) as error:
        regroup([unit("text")], LanguagePair(), provider=provider, model=model, profile=profile)
    assert error.value.category == "configuration"


def test_soft_turn_and_quiet_cannot_force_apart_compressed_number_or_compound():
    pieces = ["一", "千", "九", "百", "萬，", "手", "快", "有", "手", "慢", "冇。"]
    units = [
        unit(
            s,
            i * 0.11,
            (i + 1) * 0.11,
            str(i // 3),
            literal=True,
            quiet_hint_ms=80 if i == 3 else 0,
            speaker_change_hint=i == 3,
        )
        for i, s in enumerate(pieces)
    ]
    calls = []

    def bad(system, payload, schema):
        calls.append(payload)
        return {"groups": []}

    cues, audit, _ = run(units, bad, target="yue")
    assert len(cues) == 1 and cues[0].text == "".join(pieces)
    assert calls[0]["mandatory_starts"] == []
    assert calls[0]["units"][3]["speaker_change_hint"] and calls[0]["units"][3]["quiet_hint_ms"] == 80
    assert audit["fallback_chunk_count"] == 1


def test_valid_model_flash_tail_stays_with_unfinished_parent_and_original_boundary_is_audited():
    parts = ["三千九咁嘅", "你翻嚟咩？"]
    units = [unit(parts[0], 0, 0.85, literal=True), unit(parts[1], 0.85, 1.25, literal=True)]
    cues, audit, lineage = run(units, singletons, target="yue")
    assert len(cues) == 1 and (cues[0].text, cues[0].start, cues[0].end) == ("".join(parts), 0, 1.25)
    assert (
        lineage["outputs"][cues[0].id]["model_ranges"] == [[0, 0], [1, 1]]
        and audit["readability_join_count"] == 1
    )
    units[1]["boundary_before"] = True
    assert len(run(units, singletons, target="yue")[0]) == 2


def test_repeated_spoken_character_cannot_be_added_in_a_long_valid_group():
    text = "依阿姨帶兩個朋友翻嚟鬥地主，"

    def model(*_):
        return {"groups": [group(0, 0, text.replace("阿姨", "阿阿姨"), reason="transcription")]}

    cues, audit, _ = run([unit(text)], model, target="yue")
    assert cues[0].text == text and audit["fallback_chunk_count"] == 1
