"""English periods that need more evidence before they can end a subtitle."""

from __future__ import annotations

import re
import string


_ABBREVIATIONS = frozenset(
    "mr mrs ms miss mx dr prof sr jr st mt capt lt sgt col gen gov sen rep rev pres "
    "vs v etc fig dept inc co ltd".split()
)
_CLOSERS = """”’"'）)]}»」』】〉》"""
_PERIOD_SEAM = re.compile(r"\.[" + re.escape(_CLOSERS) + r"\s]*")
_NUMBER_LABEL = re.compile(r"(?:[A-Za-z][-–]?)?\d|[IVXLCDM]{2,}\b")
_BEFORE_PRONOUN_I = frozenset(
    "am was can could do did will would should shall must have had may might".split()
)


def _word_before(text: str, offset: int) -> tuple[int, str]:
    end = offset
    while end and text[end - 1] in " \t":
        end -= 1
    start = end
    while start and text[start - 1] in string.ascii_letters:
        start -= 1
    return start, text[start:end]


def nonterminal_period(text: str, index: int) -> bool:
    """Recognize a title, abbreviation, initial or decimal at this exact dot."""
    if not 0 <= index < len(text) or text[index] != ".":
        return False
    if index and text[index - 1].isdigit() and text[index + 1 : index + 2].isdigit():
        return True
    start, word = _word_before(text, index)
    if word.lower() == "no":
        # "No. 5" labels a number. "No. We left." starts another sentence.
        # An isolated token has no following word to resolve this ambiguity.
        following = index + 1
        while following < len(text) and (text[following].isspace() or text[following] in _CLOSERS):
            following += 1
        return following == len(text) or _NUMBER_LABEL.match(text, following) is not None
    if word.lower() in _ABBREVIATIONS:
        return True
    if len(word) != 1:
        return False
    if word == "I" and _word_before(text, start)[1].lower() in _BEFORE_PRONOUN_I:
        return False
    # Lowercase letters can also be part of a dotted form such as e.g. or i.e.
    previous = start >= 2 and text[start - 1] == "." and text[start - 2] in string.ascii_letters
    following = index + 2 < len(text) and text[index + 1] in string.ascii_letters and text[index + 2] == "."
    return previous or following or word.isupper()


def ends_with_abbreviation(text: str) -> bool:
    """Check the last period, allowing a trailing quote, bracket or whitespace."""
    end = len(text)
    while end and (text[end - 1].isspace() or text[end - 1] in _CLOSERS):
        end -= 1
    return nonterminal_period(text, end - 1)


def protected_period_boundaries(text: str) -> set[int]:
    """Raw offsets on either side of a protected period or its closing punctuation."""
    offsets = set()
    for match in _PERIOD_SEAM.finditer(text):
        dot = match.start()
        if nonterminal_period(text, dot):
            first = dot
            while first and text[first - 1] in " \t":
                first -= 1
            offsets.update(range(first, match.end() + 1))
    return offsets
