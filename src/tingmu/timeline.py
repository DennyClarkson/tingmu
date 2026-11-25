"""Short-gap presentation and reviewed line breaks, separate from source timing."""

from bisect import bisect_right
from dataclasses import dataclass, replace
from itertools import accumulate
import unicodedata

from .models import AppSettings, Cue, Token

MAX_LINE_WIDTH = 42
MIN_SPLIT_MS = 600
# Preserve a visible pause when borrowing time outside the short-gap bridge.
BREATHING_GAP_MS = 100
# Treat a pause longer than six seconds as a break in dialogue.
MAX_DIALOGUE_PAUSE_MS = 6000


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
        # Prefer a shorter natural word boundary over cutting the next Latin
        # word merely to fill half a line. Unbroken words wider than the limit
        # still need a bounded split. Source whitespace remains in its slice.
        natural = [i for i in boundaries if text[:i].strip()]
        cut = next((i for i in reversed(natural) if display_width(text[:i]) >= MAX_LINE_WIDTH // 2),
                   natural[-1] if natural else limit)
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


def readable_split_ends(source: Cue, parts: list[str]) -> list[float]:
    """Return the proportional boundaries only if every displayed part is readable."""
    if len(parts) < 2:
        return []
    widths = [display_width(part) for part in parts]
    total = sum(widths)
    if not total:
        return []
    ends = [source.start + (source.end - source.start) * columns / total
            for columns in accumulate(widths[:-1])]
    ends.append(source.end)
    boundaries_ms = [round(t * 1000) for t in (source.start, *ends)]
    if any(right - left < MIN_SPLIT_MS for left, right in zip(boundaries_ms, boundaries_ms[1:])):
        return []
    return ends


def _display_parts(source: Cue, translated_segments=()):
    parts = source.segmented_texts
    valid = (isinstance(parts, list) and bool(parts)
             and all(isinstance(p, str) and p.strip() and display_width(p) <= MAX_LINE_WIDTH for p in parts)
             and "".join(parts) == source.text)
    ends = readable_split_ends(source, parts) if valid else []
    if not ends:
        yield replace(source, tokens=list(source.tokens)), "source"
        return
    start, offset = source.start, 0
    for index, (part, end) in enumerate(zip(parts, ends)):
        translation = translated_segments[index] if len(translated_segments) == len(parts) else source.translation
        yield replace(source, id=f"{source.id}:part:{index + 1}", start=start, end=end, text=part, translation=translation,
                      tokens=_slice_tokens(source, offset, offset + len(part)), segmented_texts=[]), "text_proportional"
        start, offset = end, offset + len(part)


class SubtitleTimeline:
    """Derive readable intervals without changing replay times or delaying the next cue."""

    @classmethod
    def for_document(cls, document, settings=None):
        settings = settings or AppSettings()
        return cls(document.cues if document else [], min_duration_ms=settings.subtitle_min_ms,
                   gap_fill_ms=settings.subtitle_gap_ms,
                   translation_review=document.metadata.get("translation_review") if document else None)

    def __init__(self, cues: list[Cue], min_duration_ms: int = 1200, gap_fill_ms: int = 500,
                 companion_hold_ms: int = 2200, *, translation_review=None):
        self.minimum = max(0, int(min_duration_ms))
        self.hold = int(companion_hold_ms)
        self.gap = max(0, min(1500, int(gap_fill_ms)))
        from .translation_review import translation_verified
        review = translation_review or {}
        entries = []
        for source in cues:
            segments = review.get("segments", {}).get(source.id, ()) if review and translation_verified(source, review) else ()
            entries.extend((part, source.id, basis) for part, basis in _display_parts(source, segments))
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
            gap_ms = segment.start_ms - covered_until
            if owner is not None and 0 < gap_ms <= MAX_DIALOGUE_PAUSE_MS:
                previous = self.segments[owner]
                if gap_ms <= self.gap:
                    self.segments[owner] = replace(previous, end_ms=segment.start_ms)
                else:
                    end_ms = min(previous.start_ms + self.minimum, segment.start_ms - BREATHING_GAP_MS)
                    if end_ms > covered_until:
                        self.segments[owner] = replace(previous, end_ms=end_ms)
            if segment.end_ms >= covered_until:
                covered_until, owner = segment.end_ms, index
        self._starts = [s.start_ms for s in self.segments]
        self._ends = [s.end_ms for s in self.segments]
        self._max_ends = list(accumulate(self._ends, max))
        self._source_ids = {s.cue.id: s.source_ids for s in self.segments}

    def export_cues(self, *, translated=False) -> list[Cue]:
        """Export the same intervals and split decisions that the player shows."""
        return [replace(s.cue, start=s.start_ms / 1000, end=s.end_ms / 1000,
                        text=s.cue.translation if translated else s.cue.text)
                for s in self.segments if s.end_ms > s.start_ms]

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
