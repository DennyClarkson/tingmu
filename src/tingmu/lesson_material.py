"""Describe available learning material, including documents from earlier releases."""

from .timeline import SubtitleTimeline
from .translation_review import translation_provided, translation_verified


def lesson_counts(document):
    cues = document.cues if document else []
    review = document.metadata.get("translation_review", {}) if document else {}
    counts = {
        "total": len(cues),
        "taught": sum(bool(c.translation.strip() and c.explanation.strip()) for c in cues),
        "verified": sum(translation_verified(c, review) for c in cues) if review else 0,
    }
    if review.get("provided"):
        counts["provided"] = sum(translation_provided(c, review) and not translation_verified(c, review) for c in cues)
    return counts


def translated_subtitles(document, *, timeline=None):
    counts = lesson_counts(document)
    if not counts["total"] or counts["verified"] + counts.get("provided", 0) != counts["total"]:
        return []
    timeline = timeline or SubtitleTimeline.for_document(document)
    return timeline.export_cues(translated=True)
