import re
from types import SimpleNamespace

import pytest

from tingmu.media import _alignment_to_cues, _whisper_turn_cues
from tingmu.models import Cue
from tingmu.sentence_punctuation import ends_with_abbreviation, nonterminal_period


@pytest.mark.parametrize(
    "word",
    "Mr Mrs Ms Miss Mx Dr Prof Sr Jr St Mt Capt Lt Sgt Col Gen Gov Sen Rep Rev Pres "
    "vs v etc e.g i.e No Fig Dept Inc Co Ltd U.S A.M W J".split(),
)
def test_known_abbreviations_protect_each_period(word):
    text = f"“{word}.”"
    assert ends_with_abbreviation(text + " ")
    assert all(nonterminal_period(text, i) for i, ch in enumerate(text) if ch == ".")


def recognize(backend, pieces, *, times=None, turns=(), pauses=()):
    raw = "".join(pieces)
    if backend == "qwen":
        pieces = re.findall(r"[\w']+", raw)
    if times is None:
        times = [(i / 4, i / 4 + 0.2) for i in range(len(pieces))]
    duration = times[-1][1]
    if backend == "whisper":
        words = [
            SimpleNamespace(word=word, start=a, end=b) for word, (a, b) in zip(pieces, times, strict=True)
        ]
        cues = _whisper_turn_cues(Cue("id", 0, duration, raw), words, turns, 0, pauses=pauses)
    else:
        units = [
            SimpleNamespace(text=word, start_time=a, end_time=b)
            for word, (a, b) in zip(pieces, times, strict=True)
        ]
        cues = _alignment_to_cues(
            raw,
            units,
            source="test",
            offset=0,
            duration=duration,
            chunk_index=0,
            speaker_changes=turns,
            pauses=pauses,
        )
    assert "".join(c.original_text for c in cues) == raw
    return [c.text for c in cues]


@pytest.mark.parametrize("backend", ["whisper", "qwen"])
@pytest.mark.parametrize(
    "pieces",
    [
        ("I'm", " Mr.", " Low", " Profile,", " thanks."),
        ("Mrs", ".", " White,", " come", " in."),
        ("So anyway, we talked about how I went to Mr.", " and", " Mrs.", " White's", " house."),
        ("Mr.", " Magorium's", " Wonder", " Emporium."),
        ("Mr", ".", " Lambert,", " come", " in."),
        ("Dr", " .", "”", " ", "Smith", " spoke."),
        ("At", " 3", ".", "14", " A.", "M.", " J", ".", " W.", " Brown", " left."),
    ],
)
def test_title_stays_with_its_name_and_real_sentence_end_survives(backend, pieces):
    assert recognize(backend, (*pieces, " Then", " we", " left!")) == ["".join(pieces), "Then we left!"]


@pytest.mark.parametrize("backend", ["whisper", "qwen"])
def test_no_reply_ends_but_number_labels_continue(backend):
    pieces = ("No.", " I", " have", " No.", " 5.", " Try", " No.", " B-7.")
    assert recognize(backend, pieces) == ["No.", "I have No. 5.", "Try No. B-7."]


@pytest.mark.parametrize("backend", ["whisper", "qwen"])
@pytest.mark.parametrize(
    ("pauses", "split"),
    [
        ([], False),
        ([(0.45, 2.24)], False),
        ([(0.45, 1.35), (1.50, 2.45)], False),
        ([(0.45, 2.25)], True),
    ],
)
def test_abbreviation_needs_a_continuous_measured_pause_even_with_speaker_turn(backend, pauses, split):
    result = recognize(
        backend,
        ("Hello", " Dr.", " Smith.", " Goodbye."),
        times=[(0, 0.2), (0.23, 0.44), (2.5, 2.8), (2.85, 3.1)],
        turns=[1.5],
        pauses=pauses,
    )
    assert result == (["Hello Dr.", "Smith.", "Goodbye."] if split else ["Hello Dr. Smith.", "Goodbye."])


@pytest.mark.parametrize("backend", ["whisper", "qwen"])
def test_independent_exclamation_after_an_abbreviation_remains_a_boundary(backend):
    assert recognize(backend, ("Ask", " Dr.!", " Then", " leave.")) == ["Ask Dr.!", "Then leave."]


@pytest.mark.parametrize("backend", ["whisper", "qwen"])
def test_pronoun_i_can_end_a_sentence_next_to_a_name_with_initials(backend):
    assert recognize(backend, ("So", " did", " I.", " Then", " Dr.", " I.", " M.", " Jones", " left.")) == [
        "So did I.",
        "Then Dr. I. M. Jones left.",
    ]


def test_ordinary_full_stops_and_ellipses():
    for text in ("He's gone.", "Noel.", "Go.” ", "Dr..."):
        assert not ends_with_abbreviation(text)
    assert not nonterminal_period("Mr", 2)
