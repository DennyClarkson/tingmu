"""Normalize timed caption structure; retain the complete input separately."""

from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
import json
import math
import re
import unicodedata

from .models import Cue
from .sentence_punctuation import nonterminal_period
from .timeline import display_width


VERSION = "pre-llm-v2"
CAPTION_KINDS = {"sidecar", "embedded", "subtitle"}
ASR_KINDS = {"local_qwen", "local_whisper", "local_api", "external_api", "asr"}
_STOP = "。！？!?…؟۔।॥።"
_CLOSERS = "\"'”’»」』）)]"
_ENDS = re.compile(r"[。！？!?؟۔।॥።]+[”’\"'»」』）)\]]*\s*|(?<!\.)\.[”’\"'»]*(?=\s|$)\s*")
_NOISE = re.compile(
    r"(?:\[|【|\()\s*(?:music|applause|laughter|silence|inaudible|音[乐樂]|掌[声聲]|笑[声聲])\s*(?:\]|】|\))",
    re.I,
)


def _unspaced(c):
    n = ord(c)
    return (
        0x3400 <= n <= 0x9FFF
        or 0x20000 <= n <= 0x323AF
        or 0x3040 <= n <= 0x30FF
        or 0xE00 <= n <= 0xEFF
        or 0x1780 <= n <= 0x17FF
        or 0x1000 <= n <= 0x109F
    )


def _words(text):
    """Comparable lexical tokens with offsets into the untouched source spelling."""
    found = []
    i = 0
    while i < len(text):
        if not text[i].isalnum():
            i += 1
            continue
        start = i
        i += 1
        if not _unspaced(text[start]):
            while i < len(text) and not _unspaced(text[i]):
                if text[i].isalnum() or unicodedata.category(text[i]).startswith("M"):
                    i += 1
                elif text[i] in "'’" and i + 1 < len(text) and text[i + 1].isalnum():
                    i += 1
                else:
                    break
        while i < len(text) and unicodedata.category(text[i]).startswith("M"):
            i += 1
        found.append((unicodedata.normalize("NFC", text[start:i]).casefold(), start, i))
    return found


def _terminal(text, following=""):
    ending = text.rstrip().rstrip(_CLOSERS)
    if ending.endswith("."):
        return not nonterminal_period(ending + " " + following, len(ending) - 1)
    return ending.endswith(tuple(_STOP))


def _letters(text):
    return sum(c.isalnum() for c in text)


class _Barriers:
    def __init__(self, changes, quiet):
        self.turns = sorted(changes)
        self.quiet = []
        for a, b in sorted(quiet):
            if b - a < 0.30:
                continue
            if self.quiet and a <= self.quiet[-1][1]:
                self.quiet[-1] = (self.quiet[-1][0], max(b, self.quiet[-1][1]))
            else:
                self.quiet.append((a, b))
        self.ends = [b for a, b in self.quiet]

    def quiet_between(self, lo, hi):
        i = bisect_right(self.ends, lo)
        return i < len(self.quiet) and self.quiet[i][0] < hi

    def strong_pause_at(self, lo, hi):
        i = bisect_right(self.ends, lo)
        while i < len(self.quiet) and self.quiet[i][0] < hi:
            a, b = self.quiet[i]
            if b - a >= 1.8:
                return True
            i += 1
        return False

    def pauses_in(self, lo, hi):
        i = bisect_right(self.ends, lo)
        while i < len(self.quiet) and self.quiet[i][0] < hi:
            a, b = self.quiet[i]
            yield max(a, lo), min(b, hi)
            i += 1

    def turn_between(self, lo, hi):
        i = bisect_left(self.turns, lo)
        return i < len(self.turns) and self.turns[i] <= hi


@dataclass
class _Unit:
    cue: Cue
    indices: list[int]
    locked: bool = False
    changes: set[str] = field(default_factory=set)
    basis: str = "source"
    part: int = 0


def _same_voice(a, b):
    return not (a.speaker and b.speaker and a.speaker != b.speaker)


def _overlap_words(previous, current):
    """Longest suffix/prefix overlap using the prefix function, linear in input text."""
    n = min(len(previous), len(current))
    if not n:
        return 0
    tokens = [w[0] for w in current[:n]] + [None] + [w[0] for w in previous[-n:]]
    prefix = [0] * len(tokens)
    for i in range(1, len(tokens)):
        k = prefix[i - 1]
        while k and tokens[i] != tokens[k]:
            k = prefix[k - 1]
        prefix[i] = k + (tokens[i] == tokens[k])
    return prefix[-1]


def _roll_size(a, b, previous, current, barriers):
    gap = b.start - a.end
    if (
        not previous
        or not current
        or gap > 0.12
        or b.end < a.end - 0.12
        or not _same_voice(a, b)
        or barriers.quiet_between(min(a.end, b.start) - 0.05, max(a.end, b.start) + 0.05)
        or barriers.turn_between(min(a.end, b.start) - 0.05, max(a.end, b.start) + 0.05)
    ):
        return 0
    k = _overlap_words(previous, current)
    if not k:
        # A live caption renderer can briefly retract the tail of a growing
        # line. Keep the fullest literal version for the next update.
        if (
            gap <= -0.16
            and 2 <= len(current) < len(previous)
            and all(a[0] == b[0] for a, b in zip(previous, current))
        ):
            return -1
        return 0
    same = len(previous) == len(current) == k
    # Consecutive identical spoken lines can be intentional, especially replies.
    if same and (gap >= -0.08 or _letters(b.text) < 3):
        return 0
    letters = sum(len(t[0]) for t in current[:k])
    if k == 1 and not same:
        prefix = len(previous) == 1 and k < len(current)
        shared_word = letters >= 4
        if not (prefix and (gap < 0 or letters >= 2) or shared_word and gap < -0.08):
            return 0
    if k < min(len(previous), len(current)) and letters < 3:
        return 0
    return k


def _rolling(rows, barriers, *, require_overlap=False):
    """Use consecutive input layouts as evidence. Accumulate only the new suffix."""
    out = []
    i = 0
    while i < len(rows):
        first = rows[i]
        i += 1
        if first.locked or i == len(rows):
            out.append(first)
            continue
        previous = first.cue
        words = _words(previous.text)
        parts = [previous.text[: words[-1][2]]] if words else [previous.text]
        ending = previous.text[words[-1][2] :] if words else ""
        indices = list(first.indices)
        start, end, speaker = previous.start, previous.end, previous.speaker
        uncertain = previous.uncertain
        changed = False
        first_output = len(out)
        separator = ""
        while i < len(rows) and not rows[i].locked:
            following = rows[i].cue
            # Remote ASR and old material can contain cumulative updates too,
            # but adjacent spoken repetitions alone do not establish that origin.
            if require_overlap and following.start > previous.end - 0.16:
                break
            current = _words(following.text)
            k = _roll_size(previous, following, words, current, barriers)
            if not k or (speaker and following.speaker and speaker != following.speaker):
                break
            changed = True
            if k == -1:
                indices.extend(rows[i].indices)
                speaker = speaker or following.speaker
                uncertain = uncertain or following.uncertain
                end = max(end, following.end)
                previous = replace(previous, start=following.start, end=end, speaker=speaker)
                i += 1
                continue
            novel_start = current[k - 1][2]
            # Commit a long scroll at a measured update onset. The next unit
            # contains only newly appeared words, even for a moving two-line window.
            boundary = end if following.start <= start and following.end > end else following.start
            if k < len(current) and boundary - start >= 7:
                separator = following.text[novel_start : current[k][1]]
                text = "".join(parts) + ending
                out.append(
                    _Unit(
                        replace(
                            first.cue,
                            start=start,
                            end=min(end, boundary),
                            text=text,
                            original_text=text,
                            speaker=speaker,
                            uncertain=uncertain,
                        ),
                        indices,
                        changes={"rolling_fold"},
                        basis="caption_updates",
                    )
                )
                start = boundary
                parts = [following.text[current[k][1] : current[-1][2]]]
                indices = []
            elif k < len(current):
                parts.append(following.text[novel_start : current[-1][2]])
            ending = following.text[current[-1][2] :] or (ending if k == len(current) else "")
            indices.extend(rows[i].indices)
            speaker = speaker or following.speaker
            uncertain = uncertain or following.uncertain
            end = max(end, following.end)
            previous, words = following, current
            i += 1
        if changed:
            text = "".join(parts) + ending
            # A stream of 100–300ms VTT updates can end immediately after a
            # cut. Keep just that final tail with the last readable card.
            if len(out) > first_output and round((end - start) * 1000) < 600:
                last = out.pop()
                join = "" if separator and last.cue.text.endswith(separator) else separator
                text = last.cue.text + join + text
                start = last.cue.start
                indices = last.indices + indices
            out.append(
                _Unit(
                    replace(
                        first.cue,
                        start=start,
                        end=end,
                        text=text,
                        original_text=text,
                        speaker=speaker,
                        uncertain=uncertain,
                    ),
                    indices,
                    changes={"rolling_fold"},
                    basis="caption_updates",
                )
            )
        else:
            out.append(first)
    return out


def _fragment_join(left, right, barriers, asr):
    """Join only a short adjacent fragment of an unfinished, measured phrase.

    Only an exceptionally brief trailing syllable can survive a short VAD seam.
    Explicit speakers and long silence always remain independent.
    """
    a, b = left.cue, right.cue
    gap = b.start - a.end
    x, y = a.text.strip(), b.text.strip()
    if (
        left.locked
        or right.locked
        or not _same_voice(a, b)
        or not x
        or not y
        or _terminal(x, y)
        or gap < -0.12
        or gap > (0.80 if asr else 0.10)
        or b.end - a.start > 8
        or display_width(x) + display_width(y) > 100
    ):
        return None
    title_seam = x.rstrip(_CLOSERS).endswith(".")
    if title_seam and not (asr and x.endswith(".") and y[0].isalnum()):
        return None
    lo, hi = sorted((a.end, b.start))
    nl, nr = _letters(x), _letters(y)
    single_tail = asr and not title_seam and nl >= 2 and nr == 1 and b.end - b.start <= 0.12 and _terminal(y)
    if barriers.strong_pause_at(lo - 0.02, hi + 0.02):
        return None
    if not single_tail and (
        gap > 0.18
        or barriers.turn_between(lo - 0.02, hi + 0.02)
        or barriers.quiet_between(lo - 0.02, hi + 0.02)
    ):
        return None
    # Very short trailing tokens are frequently isolated by the aligner.
    # Complete replies and similarly short neighboring rows stay independent.
    suffix = nl >= 2 and 1 <= nr <= 3 and b.end - b.start <= 0.22 and (nl >= 3 or nr == 1)
    prefix = nl == 1 and nr >= 4 and a.end - a.start <= 0.22
    word_seam = x.endswith(("-", "‐", "‑")) and y[0].isalnum()
    if not (title_seam or suffix or prefix or word_seam):
        return None
    if a.text[-1].isspace() or b.text[0].isspace() or word_seam or _unspaced(x[-1]) or _unspaced(y[0]):
        return ""
    return " "


def _fragments(rows, barriers, asr):
    out = []
    for row in rows:
        join = _fragment_join(out[-1], row, barriers, asr) if out else None
        if join is None:
            out.append(row)
            continue
        prev = out[-1]
        text = prev.cue.text + join + row.cue.text
        lo, hi = sorted((prev.cue.end, row.cue.start))
        weak_seam = hi - lo > 0.18 or barriers.turn_between(lo, hi) or barriers.quiet_between(lo, hi)
        prev.cue = replace(
            prev.cue,
            text=text,
            original_text=text,
            end=max(prev.cue.end, row.cue.end),
            speaker=prev.cue.speaker or row.cue.speaker,
            uncertain=prev.cue.uncertain or row.cue.uncertain or weak_seam,
        )
        prev.indices.extend(row.indices)
        prev.changes.update(row.changes | {"fragment_join"})
        prev.basis = "source_union"
    return out


def _split(row, barriers):
    c = row.cue
    if row.locked or c.end - c.start < 1.2 or display_width(c.text) <= (28 if c.end - c.start >= 4 else 42):
        return [row]
    cuts = []
    counted = left_letters = 0
    remaining = _letters(c.text)
    for m in _ENDS.finditer(c.text):
        p = m.end()
        added = _letters(c.text[counted:p])
        counted, left_letters, remaining = p, left_letters + added, remaining - added
        if m.group().startswith(".") and nonterminal_period(c.text, m.start()):
            continue
        if min(left_letters, remaining) >= 4:
            cuts.append(p)
            left_letters = 0
    if not cuts:
        return [row]
    edges = [0, *cuts, len(c.text)]
    parts = [c.text[a:b] for a, b in zip(edges, edges[1:])]
    widths = [display_width(p) for p in parts]
    total = sum(widths)
    times, elapsed = [c.start], 0
    for w in widths[:-1]:
        elapsed += w
        times.append(round(c.start + (c.end - c.start) * elapsed / total, 3))
    times.append(c.end)
    starts, ends = times[:-1], times[1:]
    assigned = set()
    for a, b in barriers.pauses_in(c.start, c.end):
        if b - a < 0.45:
            continue
        near = bisect_left(times, (a + b) / 2)
        candidates = [
            k
            for k in (near - 1, near)
            if 0 < k < len(times) - 1 and k not in assigned and a - 0.4 <= times[k] <= b + 0.4
        ]
        if not candidates:
            row.cue = replace(c, uncertain=True)
            row.changes.add("split_pause_ambiguous")
            return [row]
        k = min(candidates, key=lambda k: abs(times[k] - (a + b) / 2))
        ends[k - 1], starts[k] = a, b
        assigned.add(k)
    if any(round(b * 1000) - round(a * 1000) < 600 for a, b in zip(starts, ends)):
        if assigned:
            row.cue = replace(c, uncertain=True)
            row.changes.add("split_pause_ambiguous")
        return [row]
    return [
        _Unit(
            replace(c, text=p, original_text=p, start=a, end=b, uncertain=True),
            row.indices,
            changes=row.changes | {"sentence_split"},
            basis="pause_bounded_estimate" if assigned else "text_proportional",
            part=i,
        )
        for i, (p, a, b) in enumerate(zip(parts, starts, ends), 1)
    ]


def repair_source_times(rows, barriers):
    """Repair bounded timing defects without changing any source text or ID."""
    covered = 0.0
    for i, row in enumerate(rows):
        c = row.cue
        following = rows[i + 1] if i + 1 < len(rows) else None
        if row.locked:
            covered = max(covered, c.end)
            continue
        if (
            following
            and _same_voice(c, following.cue)
            and c.start < following.cue.start < c.end <= following.cue.start + 0.12
        ):
            c = replace(c, end=following.cue.start, uncertain=True)
            row.changes.add("micro_overlap_trim")
            row.basis = "neighbor_bounded"
        n = _letters(c.text)
        wide = any(_unspaced(ch) for ch in c.text)
        duration = c.end - c.start
        fast = n >= 4 and n > max(0.001, duration) * (27 if wide else 43)
        slow = n and duration > max(5, n * (1.4 if wide else 0.9))
        if fast or slow or duration == 0:
            c = replace(c, uncertain=True)
            row.changes.add("timing_mismatch")
        if slow:
            # Trim only measured quiet touching an implausibly long cue edge.
            # An isolated word anywhere inside a long active span has no such anchor.
            a, b = c.start, c.end
            for lo, hi in barriers.pauses_in(c.start, c.end):
                if lo == c.start:
                    a = hi
                if hi == c.end:
                    b = lo
            if b - a >= 0.12 and (a, b) != (c.start, c.end):
                c = replace(c, start=a, end=b)
                row.changes.add("timing_estimate")
                row.basis = "pause_bounded_estimate"
        if fast and following and c.end <= following.cue.start:
            # An estimate is bounded by the next actual onset. Do not move
            # a speech onset or borrow from another active caption/speaker.
            end = min(
                c.start + min(1.4, max(0.6, n / (15 if wide else 24))),
                c.end + 0.6,
                following.cue.start - 0.08,
            )
            if (
                round(end, 3) > c.end
                and covered <= c.start
                and _same_voice(c, following.cue)
                and not barriers.quiet_between(c.end, end)
                and not barriers.turn_between(c.end, end)
            ):
                c = replace(c, end=round(end, 3))
                row.changes.add("timing_estimate")
                row.basis = "neighbor_bounded"
        # A long phrase jammed against the next onset can have room only on
        # its left. Two neighboring events bound this estimate; never shift
        # another cue or infer a lead-in at the start of the media/clip.
        severe = n >= 10 and round((c.end - c.start) * 1000) < 200
        if (
            severe
            and i
            and following
            and c.end == row.cue.end
            and 0 <= following.cue.start - c.end <= 0.08
            and 0 < c.start - covered <= 2.5
        ):
            previous = rows[i - 1].cue
            if previous.end == covered and _same_voice(previous, c):
                target = min(1.4, max(0.6, n / (15 if wide else 24)))
                left = max(covered + 0.08, c.end - target)
                # Only the portion after the last measured pause or speaker
                # change can belong to this phrase.
                for _, quiet_end in barriers.pauses_in(left, c.start):
                    left = quiet_end
                turn = bisect_right(barriers.turns, c.start) - 1
                if turn >= 0:
                    left = max(left, barriers.turns[turn])
                left = math.ceil(left * 1000 - 1e-7) / 1000
                if c.start - left >= 0.12:
                    c = replace(c, start=left, uncertain=True)
                    row.changes.add("timing_estimate")
                    row.basis = "neighbor_bounded"
        if n >= 10 and round((c.end - c.start) * 1000) < 200:
            row.changes.add("timing_unresolved")
        row.cue = c
        covered = max(covered, c.end)


def source_rows(original, *, source_kind, protected_ids=(), speaker_changes=(), quiet_intervals=()):
    """Clean empty events and rolling duplicates before either regrouping mode."""
    protected = set(protected_ids)
    ids = Counter(c.id for c in original)
    rows, dropped = [], []
    for i, c in enumerate(original):
        locked = c.id in protected or bool(c.translation or c.explanation or c.segmented_texts)
        if locked and ids[c.id] != 1:
            raise ValueError("Protected subtitle IDs must be unique")
        if not locked and (not any(x.isalnum() for x in c.text) or _NOISE.fullmatch(c.text.strip())):
            dropped.append(i)
        else:
            rows.append(_Unit(c, [i], locked))
    rows.sort(key=lambda r: (r.cue.start, r.indices[0]))
    barriers = _Barriers(speaker_changes, quiet_intervals)
    before_rolling = len(rows)
    if source_kind in CAPTION_KINDS | {"local_api", "external_api", "legacy"}:
        rows = _rolling(rows, barriers, require_overlap=source_kind not in CAPTION_KINDS)
    folded = before_rolling - len(rows)
    return rows, dropped, folded, barriers


def normalize(cues, *, source_kind, language, protected_ids=(), speaker_changes=(), quiet_intervals=()):
    """Return teaching cues, content-free counters, and a reversible local record.

    Speaker changes and quiet intervals use absolute media seconds. Explicit
    translations, corrections, and learned materials keep their original cues.
    """
    if source_kind not in ASR_KINDS | CAPTION_KINDS | {"legacy"}:
        raise ValueError(f"Unknown subtitle source kind: {source_kind}")
    original = list(cues)
    rows, dropped, folded, barriers = source_rows(
        original,
        source_kind=source_kind,
        protected_ids=protected_ids,
        speaker_changes=speaker_changes,
        quiet_intervals=quiet_intervals,
    )
    ids = Counter(c.id for c in original)
    before_join = len(rows)
    rows = _fragments(rows, barriers, source_kind in ASR_KINDS)
    joined = before_join - len(rows)
    rows = [part for row in rows for part in _split(row, barriers)]
    rows.sort(key=lambda r: r.cue.start)
    repair_source_times(rows, barriers)
    rows.sort(key=lambda row: row.cue.start)
    reserved = set(ids)
    outputs, output_sources, counts, result = {}, {}, Counter(), []
    for row in rows:
        c = row.cue
        sources = [original[i].id for i in row.indices]
        if c.tokens and row.changes & {"rolling_fold", "fragment_join", "sentence_split"}:
            c = replace(c, tokens=[])
        if len(sources) > 1 or row.part or ids[c.id] > 1 or not c.id or c.id != sources[0]:
            identity = [VERSION, source_kind, sources, row.part]
            if any(ids[s] > 1 for s in sources):
                identity.append(row.indices)
            root = "pre-" + sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:20]
            ident, suffix = root, 1
            while ident in reserved:
                ident = f"{root}-{suffix}"
                suffix += 1
            reserved.add(ident)
            c = replace(c, id=ident)
        output_sources[c.id] = sources
        outputs[c.id] = {
            "source_indices": row.indices,
            "operations": sorted(row.changes),
            "timing_basis": row.basis,
        }
        counts.update(row.changes)
        result.append(c)
    audit = {
        "version": VERSION,
        "source_kind": source_kind,
        "input_cue_count": len(original),
        "output_cue_count": len(result),
        "coalesced_count": joined,
        "rolling_fold_count": folded,
        "split_runon_count": sum(row.part == 1 for row in rows),
        "repaired_timing_count": sum(
            bool(row.changes & {"timing_estimate", "micro_overlap_trim"}) for row in rows
        ),
        "sub_500ms_count": sum(round(c.end * 1000) - round(c.start * 1000) < 500 for c in result),
        "short_fragment_count": sum(_letters(c.text) <= 3 for c in result),
        "dropped_count": len(dropped),
        "protected_count": sum(row.locked for row in rows),
        "counts": dict(counts),
    }
    if counts["timing_unresolved"]:
        audit["unresolved_timing_count"] = counts["timing_unresolved"]
    lineage = {
        "sources": [asdict(c) for c in original],
        "output_sources": output_sources,
        "outputs": outputs,
        "dropped_indices": dropped,
        "dropped_ids": [original[i].id for i in dropped],
    }
    return result, audit, lineage
