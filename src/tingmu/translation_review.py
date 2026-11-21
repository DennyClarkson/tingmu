"""Persisted proof of a bilingual model review for the exact subtitle revision."""

import hashlib
import json


TRANSLATION_REVIEW_VERSION = "bilingual-segments-v1"


def lesson_digest(cue, segments):
    fields = [cue.text, cue.translation, cue.explanation, cue.segmented_texts, segments]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def translation_verified(cue, review):
    segments = review.get("segments", {}).get(cue.id, [])
    expected = len(cue.segmented_texts) if len(cue.segmented_texts) > 1 else 0
    return (bool(cue.translation.strip() and cue.explanation.strip())
            and isinstance(segments, list) and len(segments) == expected and all(isinstance(s, str) and s.strip() for s in segments)
            and review.get("version") == TRANSLATION_REVIEW_VERSION
            and review.get("verified", {}).get(cue.id) == lesson_digest(cue, segments))
