"""Local PCM evidence and sentence boundaries shared by the ASR workers.

All spans in this module are relative to the supplied audio, in seconds.
"""

from __future__ import annotations

import math
import re


def frame_energy(audio, rate=16000):
    """20 ms RMS, measured in blocks to avoid a second full-track float buffer."""
    import numpy as np

    hop = rate // 50
    count = len(audio) // hop
    values = np.empty(count, dtype=np.float32)
    for first in range(0, count, 1500):
        last = min(count, first + 1500)
        frames = audio[first * hop : last * hop].reshape(-1, hop)
        values[first:last] = np.sqrt(np.einsum("ij,ij->i", frames, frames) / hop)
    return values


def quiet_intervals(energy):
    """Conservative quiet runs. Constant music and normal speech are not silence."""
    import numpy as np

    if not len(energy):
        return []
    threshold = min(0.009, max(0.001, float(np.percentile(energy, 75)) * 0.1))
    quiet = np.r_[False, energy <= threshold, False]
    edges = np.flatnonzero(quiet[1:] != quiet[:-1])
    return [(a / 50, b / 50) for a, b in zip(edges[::2], edges[1::2]) if b - a >= 12]


def pcm_windows(count, size, energy, rate=16000):
    """Prefer the middle of a pause in the last six seconds of each model window."""
    import numpy as np

    begin = 0
    while count - begin > size:
        lower = begin + size - 6 * rate
        upper = begin + size
        # Leave enough audio for a final model invocation, including decoder tails.
        upper = min(upper, count - 4 * rate)
        first, last = lower // (rate // 50), upper // (rate // 50)
        band = energy[first:last]
        pauses = quiet_intervals(band)
        if pauses:
            a, b = max(pauses, key=lambda span: (min(1.0, span[1] - span[0]), span[1]))
            finish = (first + round((a + b) * 25)) * (rate // 50)
        elif len(band) >= 9:
            # No silence: choose the quietest sustained local trough, falling back
            # to the latest boundary on a flat signal.
            means = np.convolve(band, np.ones(9) / 9, mode="valid")
            i = len(means) - 1 - int(np.argmin(means[::-1]))
            finish = (first + i + 4) * (rate // 50)
        else:
            finish = upper
        finish = min(upper, max(lower, finish))
        yield begin, finish
        begin = finish
    if begin < count:
        yield begin, count


def _speech_island(point, lo, hi, pauses):
    """Do not borrow duration from speech across a quiet run."""
    for a, b in pauses:
        if b <= point:
            lo = max(lo, b)
        elif a >= point:
            hi = min(hi, a)
            break
        else:
            return point, point
    return lo, hi


def repair_times(times, weights, duration, pauses, limits=()):
    """Interpolate only suspect contiguous units; lock adjacent measured endpoints.

    Returns repaired unit spans, affected indices, and the local evidence for each
    estimate. Infeasible floors stay explicitly constrained in the audit.
    """
    if any(not math.isfinite(t) for pair in times for t in pair):
        raise ValueError("时间对齐包含非有限时间")
    spans = [(min(duration, max(0.0, a)), min(duration, max(0.0, b))) for a, b in times]
    affected = set()
    reports = []
    clipped = set()
    for i, (a, b) in enumerate(spans):
        if b - a < 0.5:
            continue
        for x, y in pauses:
            if min(y, b) - max(x, a) < 0.45:
                continue
            # A dragged word may contain a real pause and a short lobe on
            # either side. Keep the supported lobe at its linguistic edge.
            before, after = max(0.0, x - a), max(0.0, b - y)
            if max(before, after) < 0.035:
                continue
            if after >= 0.035 and (
                before < 0.035 or i in limits or (i + 1 not in limits and after >= before)
            ):
                a = max(a, y)
            else:
                b = min(b, x)
            spans[i] = (a, b)
            clipped.add(i)
        if i in clipped:
            reports.append(
                {
                    "unit_start": i,
                    "unit_end": i + 1,
                    "method": "trimmed_at_silence",
                    "estimated_span": [round(a, 4), round(b, 4)],
                }
            )
        # A single unit sometimes starts at the chunk origin and lasts through
        # an entire musical introduction. Its end, beside the next measured
        # word, is a substantially better anchor than the chunk edge.
        if b - a > max(4.0, weights[i] * 1.5):
            near_next = i + 1 < len(spans) and 0 <= spans[i + 1][0] - b < 1.2
            near_previous = i > 0 and 0 <= a - spans[i - 1][1] < 1.2
            estimate = 0.45 * weights[i]
            if near_next and not near_previous:
                lo, _ = _speech_island(b, a, b, pauses)
                a = max(lo, b - estimate)
            elif near_previous and not near_next:
                _, hi = _speech_island(a, a, b, pauses)
                b = min(hi, a + estimate)
            else:
                continue
            spans[i] = (a, b)
            clipped.add(i)
            reports.append(
                {
                    "unit_start": i,
                    "unit_end": i + 1,
                    "method": "trimmed_unanchored_tail",
                    "estimated_span": [round(a, 4), round(b, 4)],
                }
            )
    for i, ((a, b), weight) in enumerate(zip(spans, weights)):
        if b - a < 0.035 or (weight >= 4 and b - a < 0.04 * weight):
            affected.add(i)
        if i and a < spans[i - 1][1] - 0.001:
            affected.update((i - 1, i))
    # Repeated and near-repeated onsets, including runs in the aligner's
    # adjacent 80 ms timestamp bins. Include the last token in the cluster.
    clusters = set()
    for width in (3, 5, 8):
        for end in range(width, len(spans) + 1):
            first = end - width
            if spans[end - 1][0] - spans[first][0] < (width - 1) * 0.042 and all(
                spans[j][0] - spans[j - 1][0] <= 0.085 for j in range(first + 1, end)
            ):
                clusters.update(range(first, end))
    affected.update(clusters)
    # The Qwen timestamp postprocessor can produce a few implausibly stretched
    # units immediately before a plateau (e.g. 2.1 s per single character).
    # These lack an independent anchor when duration and gaps have equal steps.
    for i in range(len(spans) - 1, 0, -1):
        if i not in clusters:
            continue
        a, b = spans[i - 1]
        step = b - a
        # Sometimes the preceding token absorbs all the missing speech and
        # ends precisely at the plateau, leaving its own onset as the anchor.
        borrowed = (
            step > 1.2
            and abs(spans[i][0] - b) <= 0.022
            and all(j in clusters for j in range(i, i + 4))
            and not any(min(y, b) - max(x, a) >= 0.35 for x, y in pauses)
        )
        if borrowed or (step > 1.0 and abs(spans[i][0] - b - step) < 0.035):
            affected.add(i - 1)
            clusters.add(i - 1)
    for i in range(1, len(spans) - 2):
        if i - 1 not in clusters or i in clusters:
            continue
        step = spans[i][1] - spans[i][0]
        # Off-grid equal-duration/equal-gap units following a plateau are
        # produced by the upstream monotonic interpolator. Three successive
        # matching steps distinguish these from independent measured anchors.
        if step < 0.08 or abs(step - round(step / 0.08) * 0.08) < 0.004:
            continue
        last = i
        while (
            last < len(spans)
            and abs(spans[last][0] - spans[last - 1][1] - step) < 0.004
            and abs(spans[last][1] - spans[last][0] - step) < 0.004
        ):
            last += 1
        if last - i >= 3:
            affected.update(range(i, last))
            clusters.update(range(i, last))
    first = 0
    while first < len(spans):
        if first not in affected:
            first += 1
            continue
        last = first + 1
        while last < len(spans) and last in affected:
            gap = spans[last][0] - spans[last - 1][1]
            if gap >= 0.24 and (last in limits or gap >= 0.45):
                uniform_step = (
                    last in clusters
                    and last - 1 in clusters
                    and gap > 1.0
                    and abs(gap - (spans[last - 1][1] - spans[last - 1][0])) < 0.035
                )
                if not uniform_step or any(spans[last - 1][1] <= x < y <= spans[last][0] for x, y in pauses):
                    break
            last += 1
        a, b = spans[first][0], spans[last - 1][1]
        count = sum(weights[first:last])
        desired = 0.085 * count
        # At a sentence edge, treat the observed inter-sentence gap as the
        # boundary. With PCM evidence we can borrow only adjoining speech.
        left = spans[first - 1][1] if first else 0.0
        right = spans[last][0] if last < len(spans) else duration
        orphan_onset = first == 0 and b <= 0.04 and last < len(spans) and right - b >= 0.65
        if not pauses:
            if a - left >= 0.65:
                left = a
            if right - b >= 0.65 and not orphan_onset:
                right = b
        if first in limits and first:
            left = max(left, (left + a) / 2)
        if last in limits and last < len(spans) and not orphan_onset:
            right = min(right, (right + b) / 2)
        pivot = min(duration, max(0.0, (a + b) / 2))
        if orphan_onset:
            # A zero at the chunk origin is not evidence that a character
            # sounded there. Bound the orphan to the next word's onset and
            # the local speech island, even after a long silent introduction.
            pivot = right
        # At the beginning of a clause, a collapsed initial unit can have
        # landed at the end of the preceding phrase. A measured intervening
        # silence plus the next word's real onset bounds the new speech.
        if first in limits and last < len(spans) and b - a < 0.05 * count:
            trailing = [
                y
                for x, y in pauses
                if b - 0.04 <= x and y <= right and y - x >= 0.5 and 0.035 <= right - y <= 0.45
            ]
            if trailing:
                left = max(left, trailing[-1])
                pivot = min(right, left + desired / 2)
        if right < left:
            raise ValueError("对齐锚点顺序无效")
        pivot = min(right, max(left, pivot))
        left, right = _speech_island(pivot, left, right, pauses)
        length = min(max(0.0, right - left), max(b - a, desired))
        start = max(left, min(pivot - length / 2, right - length))
        if length > 0.001 * (last - first):
            consumed = 0
            for i in range(first, last):
                x = start + length * consumed / count
                consumed += weights[i]
                spans[i] = (x, start + length * consumed / count)
            status = "bounded_estimate" if length >= desired - 0.001 else "anchor_limited"
        else:
            status = "no_supported_span"
        reports.append(
            {
                "unit_start": first,
                "unit_end": last,
                "method": status,
                "bounds": [round(left, 4), round(right, 4)],
                "estimated_span": [round(spans[first][0], 4), round(spans[last - 1][1], 4)],
                "unit_spans": [[round(x, 4), round(y, 4)] for x, y in spans[first:last]],
            }
        )
        first = last
    if any(b < a for a, b in spans) or any(
        a < prev_end - 0.0001 for (_, prev_end), (a, _) in zip(spans, spans[1:])
    ):
        raise ValueError("无法在相邻声学锚点内恢复单调时间")
    return spans, affected | clipped, reports


_SENTENCE_END = re.compile(r"""[。！？!?；;…]|\.(?:[”’"'）)]*\s|[”’"'）)]*$)""")
_CLAUSE = re.compile(r"[,，、：:]")
_PARTICLE = re.compile(r"[呀啊啦喇囉咯啫嘛㗎喎噃吖咋]$")


def sentence_breaks(text, ends, separators, spans, turns, pauses):
    """Indices immediately after the last aligned unit of a spoken sentence."""
    n = len(spans)
    if n < 2:
        return {n}
    ends_sentence = {i for i in range(1, n) if _SENTENCE_END.search(separators[i - 1])}
    clauses = {
        i
        for i in range(1, n)
        if _CLAUSE.search(separators[i - 1])
        or (
            _PARTICLE.search(text[: ends[i - 1]].rstrip())
            and not _PARTICLE.fullmatch(text[ends[i - 1] :].lstrip()[:1])
        )
    }
    gaps = [0.0] + [max(0.0, spans[i][0] - spans[i - 1][1]) for i in range(1, n)]
    centers = [0.0] + [(spans[i][0] + spans[i - 1][1]) / 2 for i in range(1, n)]
    cuts = set(ends_sentence)
    hard = set(ends_sentence)
    for i in range(1, n):
        if i in clauses and gaps[i] >= 0.30:
            cuts.add(i)
        witnessed = max((min(b, spans[i][0]) - max(a, spans[i - 1][1]) for a, b in pauses), default=0.0)
        if gaps[i] < 0.65 or (gaps[i] < 1.8 and witnessed < 0.35 and i not in clauses):
            continue
        # Qwen frequently places the first character of a new clause just
        # before a pause. Snap to its adjacent punctuation instead of peeling
        # that character off, or slicing a Chinese word midway.
        near = [k for k in ends_sentence | clauses if abs(k - i) <= 2]
        if near:
            k = min(near, key=lambda k: (abs(k - i), k not in ends_sentence, k))
            cuts.add(k)
        elif separators[i - 1].isspace() or (gaps[i] >= 1.8 and (witnessed >= 0.5 or not pauses)):
            cuts.add(i)
            if gaps[i] >= 1.8 and (witnessed >= 0.5 or not pauses):
                hard.add(i)
    for change in turns:
        nearby = [
            i
            for i in range(1, n)
            if min(abs(change - centers[i]), max(0.0, spans[i - 1][1] - change, change - spans[i][0])) <= 0.60
        ]
        if not nearby:
            continue

        def score(i):
            quiet = gaps[i] >= 0.24 or any(a <= centers[i] <= b for a, b in pauses)
            return 3 * (i in ends_sentence) + 2 * (i in clauses) + quiet - 2 * abs(change - centers[i])

        i = max(nearby, key=score)
        # A change inside a multi-character word is too weak on its own.
        if i in ends_sentence or i in clauses or separators[i - 1].isspace() or i in hard:
            cuts.add(i)
    # If soft cues flank a one-to-three-character fragment, retain the better
    # evidenced boundary; genuine punctuated short replies remain independent.
    for i in sorted(cuts.copy()):
        if i not in cuts or i in hard:
            continue
        prev = max((v for v in cuts if v < i), default=0)
        nxt = min((v for v in cuts if v > i), default=n)
        short_left = ends[i - 1] - (ends[prev - 1] if prev else 0) <= 3
        short_right = ends[nxt - 1] - ends[i - 1] <= 3
        # A clause comma or an isolated interjection can support a brief
        # utterance. A single character clipped from its phrase cannot.
        if i not in clauses and (short_left or short_right) and gaps[i] < 1.8:
            cuts.remove(i)
    return cuts | {n}
