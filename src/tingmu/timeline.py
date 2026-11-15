"""Short-gap presentation and reviewed line breaks, separate from source timing."""

from bisect import bisect_right
from dataclasses import dataclass, replace
from itertools import accumulate
import unicodedata

from .models import Cue, Token

MAX_LINE_WIDTH = 42


def display_width(text: str) -> int:
    """Conservative text-column budget (not a pixel or audio measurement)."""
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in "WF" else 1
               for c in text)


def split_display_text(text: str) -> list[str]:
    """Deterministic fallback for retained model edits; never join source cues."""
    parts = []
    while display_width(text) > MAX_LINE_WIDTH:
        width, limit = 0, 0
        for index, char in enumerate(text):
            width += display_width(char)
            if width > MAX_LINE_WIDTH:
                break
            limit = index + 1
        boundaries = [i + 1 for i, char in enumerate(text[:limit]) if char in "，。！？；、,.!?;: \n"]
        cut = next((i for i in reversed(boundaries) if display_width(text[:i]) >= MAX_LINE_WIDTH // 2), limit)
        parts.append(text[:cut])
        text = text[cut:]
    if text:
        parts.append(text)
    return parts


@dataclass(frozen=True)
class DisplaySegment:
    cue: Cue
    start_ms: int
    end_ms: int
    source_ids: tuple[str, ...]
    timing_basis: str = "source"


def _slice_tokens(cue: Cue, start: int, end: int) -> list[Token]:
    # Teacher tokenization may omit spaces. Align its exact literal spans without
    # attaching a whole-word meaning/pronunciation to a fragment of that word.
    result, cursor = [], 0
    for token in cue.tokens:
        position = cue.text.find(token.text, cursor)
        if position < 0 or cue.text[cursor:position].strip():
            return []
        limit = position + len(token.text)
        left, right = max(start, position), min(end, limit)
        if left < right:
            result.append(replace(token) if left == position and right == limit
                          else Token(cue.text[left:right]))
        cursor = limit
    return result


def _display_parts(source: Cue):
    parts = source.segmented_texts
    valid = (isinstance(parts, list) and bool(parts)
             and all(isinstance(p, str) and p.strip() and display_width(p) <= MAX_LINE_WIDTH for p in parts)
             and "".join(parts) == source.text)
    if not valid or len(parts) == 1:
        yield replace(source, tokens=list(source.tokens)), "source"
        return
    total = sum(display_width(p) for p in parts)
    elapsed, offset = 0, 0
    for index, part in enumerate(parts):
        next_elapsed = elapsed + display_width(part)
        start = source.start + (source.end - source.start) * elapsed / total
        end = source.end if index == len(parts) - 1 else source.start + (source.end - source.start) * next_elapsed / total
        yield replace(source, id=f"{source.id}:part:{index + 1}", start=start, end=end, text=part,
                      tokens=_slice_tokens(source, offset, offset + len(part)), segmented_texts=[]), "text_proportional"
        elapsed, offset = next_elapsed, offset + len(part)


class SubtitleTimeline:
    """Keep every source separate and bridge only an actual short blank gap.

    min_duration_ms and companion_hold_ms are accepted for old saved settings but
    deliberately do not extend or delay a cue. Split display times are estimates
    within the untouched source interval, explicitly marked text_proportional.
    Both surfaces look up the current position directly, including backward seeks.
    """

    def __init__(self, cues: list[Cue], min_duration_ms: int = 1200, gap_fill_ms: int = 500,
                 companion_hold_ms: int = 2200):
        self.minimum = int(min_duration_ms)
        self.hold = int(companion_hold_ms)
        self.gap = max(0, min(1500, int(gap_fill_ms)))
        entries = [(part, source.id, basis) for source in cues for part, basis in _display_parts(source)]
        entries.sort(key=lambda entry: (entry[0].start, entry[0].end))
        self.cues = [entry[0] for entry in entries]
        self.segments: list[DisplaySegment] = []
        for cue, source_id, basis in entries:
            start, end = round(cue.start * 1000), round(cue.end * 1000)
            self.segments.append(DisplaySegment(cue, start, end, (source_id,), basis))
        # Sweep the union of source ranges: an overlapping sign is not a blank.
        covered_until, owner = -1, None
        for index, segment in enumerate(self.segments):
            if segment.end_ms <= segment.start_ms:
                continue
            if owner is not None and 0 < segment.start_ms - covered_until <= self.gap:
                self.segments[owner] = replace(self.segments[owner], end_ms=segment.start_ms)
            if segment.end_ms >= covered_until:
                covered_until, owner = segment.end_ms, index
        self._starts = [s.start_ms for s in self.segments]
        self._ends = [s.end_ms for s in self.segments]
        self._max_ends = list(accumulate(self._ends, max))
        self._source_ids = {s.cue.id: s.source_ids for s in self.segments}

    def _at(self, position_ms: int) -> Cue | None:
        index = bisect_right(self._starts, position_ms) - 1
        while index >= 0 and self._max_ends[index] > position_ms:
            if self._ends[index] > position_ms:
                return self.cues[index]
            index -= 1
        return None

    def subtitle_at(self, position_ms: int) -> Cue | None:
        return self._at(position_ms)

    def companion_at(self, position_ms: int) -> Cue | None:
        return self._at(position_ms)

    def source_ids(self, cue_id: str) -> tuple[str, ...]:
        return self._source_ids.get(cue_id, ())
