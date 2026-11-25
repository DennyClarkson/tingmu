"""Whisper's untouched segment evidence and conservative no-VAD decisions."""

import math
import re


def _finite(value):
    try:
        value = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def segment_evidence(segment, duration, offset):
    """Keep absolute provider times, even for segments that cannot become cues."""
    raw_start, raw_end = _finite(segment.start), _finite(segment.end)
    words = list(getattr(segment, "words", None) or [])
    raw_words, valid = [], []
    for word in words:
        a, b = _finite(word.start), _finite(word.end)
        raw_words.append(
            {
                "text": word.word,
                "start": None if a is None else offset + a,
                "end": None if b is None else offset + b,
                "probability": _finite(getattr(word, "probability", None)),
            }
        )
        if a is not None and b is not None and 0 <= a < b <= duration + 0.02:
            valid.append((a, min(duration, b)))
    evidence = {
        "text": segment.text,
        "start": None if raw_start is None else offset + raw_start,
        "end": None if raw_end is None else offset + raw_end,
        "words": raw_words,
        "avg_logprob": _finite(segment.avg_logprob),
        "no_speech_prob": _finite(segment.no_speech_prob),
        "compression_ratio": _finite(segment.compression_ratio),
        "cue_ids": [],
    }
    a = max(0.0, raw_start) if raw_start is not None else duration
    b = min(duration, raw_end) if raw_end is not None else 0.0
    if round(a, 3) >= round(b, 3):
        if not valid or "".join(w.word for w in words).strip() != segment.text.strip():
            evidence["disposition"] = "unplaced_segment"
            return None, evidence
        a, b = min(v[0] for v in valid), max(v[1] for v in valid)
        if round(a, 3) >= round(b, 3):
            evidence["disposition"] = "unplaced_segment"
            return None, evidence
        evidence["segment_bounds_basis"] = "word_span"
    return (a, b), evidence


def quality_evidence(evidence, span, pauses, previous, vad_filter):
    """Repetition by itself is common in dialogue. Suppression needs acoustic support."""
    a, b = span
    quiet = sum(max(0.0, min(b, y) - max(a, x)) for x, y in pauses) / (b - a)
    clean = re.sub(r"[\W_]+", "", evidence["text"]).casefold()
    repeated = bool(clean and (previous == clean or re.search(r"(.{1,12}?)\1{3,}", clean)))
    logprob = evidence["avg_logprob"]
    no_speech = evidence["no_speech_prob"]
    ratio = evidence["compression_ratio"]
    low = logprob is not None and logprob < -0.8
    unlikely = no_speech is not None and no_speech > 0.5
    compressed = ratio is not None and ratio > 2.4
    skip = (
        not vad_filter
        and quiet >= 0.9
        and (
            (repeated and (low or unlikely or compressed))
            or (low and no_speech is not None and no_speech >= 0.85)
        )
    )
    evidence.update(quiet_fraction=round(quiet, 4), repeated_text=repeated)
    uncertain = (
        low
        or unlikely
        or compressed
        or b - a > 15
        or bool(evidence.get("segment_bounds_basis"))
        or (not vad_filter and (repeated or quiet >= 0.9))
    )
    return skip, uncertain, clean
