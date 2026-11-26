"""Boundary layout recovery preserves authoritative English text and acoustic times."""
from dataclasses import asdict
import json

import pytest

from tingmu.agent import AnnotationValidationError
from tingmu.models import Cue, LanguagePair, Token
from tingmu.review import validate_review
from tingmu.timeline import MAX_LINE_WIDTH, SubtitleTimeline, display_width, split_display_text


def validate(text, parts, *, source=None, reason=""):
    source = source or Cue("source", 1.25, 8.75, text, "untouched-raw")
    row = {"id": source.id, "text": text, "uncertain": False, "reason": reason, "segmented_texts": parts}
    return validate_review(json.dumps({"cues": [row]}), [source], LanguagePair("zh-CN", "en"))


def test_verified_layout_moves_title_to_name_and_keeps_other_valid_boundaries():
    parts = ["We waited outside until morning. ", "Then we went back with Mr. ", "Lambert to speak to the manager."]
    text = "".join(parts)
    output, metadata = validate(text, parts)
    segments = output[0].segmented_texts
    assert segments[0] == parts[0]
    assert "".join(segments) == text
    assert all(display_width(p) <= MAX_LINE_WIDTH for p in segments)
    assert any("Mr. Lambert" in part for part in segments)
    assert metadata["source"]["segmentation_repair"]["reason_codes"] == ["abbreviation_boundary"]


@pytest.mark.parametrize("separator", [" ", "  ", "\t", "\u00a0"])
def test_restore_only_segment_edge_spacing_preserves_every_source_character(separator):
    parts = ["When the weather clears,", "we'll take the train", "and meet at five."]
    text = separator.join(parts)
    source = Cue("source", 1.25, 8.75, text, "raw recognition")
    before = asdict(source)
    output, metadata = validate(text, parts, source=source)
    repaired = output[0]
    assert repaired.text == text and "".join(repaired.segmented_texts) == text
    assert repaired.segmented_texts == [parts[0] + separator, parts[1] + separator, parts[2]]
    assert asdict(source) == before
    assert (repaired.id, repaired.start, repaired.end, repaired.original_text) == (
        source.id, source.start, source.end, source.original_text)
    record = metadata["source"]
    assert not record["changed"] and not repaired.uncertain
    assert record["segmentation_repair"]["reason_codes"] == ["segment_boundary_whitespace"]
    assert text not in json.dumps(record)


@pytest.mark.parametrize("text,parts", [
    ("Go nowhere, please.", ["Go now here,", "please."]),
    ("Go now here, please.", ["Go nowhere,", "please."]),
    ("Keep  both spaces, please.", ["Keep both spaces,", "please."]),
    ("Don't rewrite this, please.", ["Don’t rewrite this,", "please."]),
    ("Keep the comma, please.", ["Keep the comma", "please."]),
    ("Keep the words, please.", ["Keep the word,", "please."]),
    ("Keep the words, please.", ["Keep the words,", "words, please."]),
    ("Keep the newline\nexactly.", ["Keep the newline", "exactly."]),
])
def test_whitespace_repair_rejects_word_punctuation_internal_space_and_content_changes(text, parts):
    with pytest.raises(AnnotationValidationError, match="逐字重构"):
        validate(text, parts)


def test_boundary_repair_cannot_bypass_reason_or_speculative_edit_guards():
    source = Cue("source", 1, 3, "Hello, friend.")
    with pytest.raises(AnnotationValidationError, match="缺少原因"):
        validate("Hello, friends.", ["Hello,", "friends."], source=source)
    output, metadata = validate("A completely invented story replaces everything.",
                                ["A completely invented story", "replaces everything."], source=source, reason="Guess")
    assert output[0].text == source.text
    assert metadata["source"]["state"] == "retained"
    assert "segmentation_repair" not in metadata["source"]


def test_restored_space_at_maximum_width_uses_existing_safe_layout_repair():
    first = "x" * MAX_LINE_WIDTH
    text = first + " and a shorter sentence."
    output, metadata = validate(text, [first, "and a shorter sentence."])
    assert "".join(output[0].segmented_texts) == text
    assert all(display_width(p) <= MAX_LINE_WIDTH and p.strip() for p in output[0].segmented_texts)
    assert metadata["source"]["segmentation_repair"]["reason_codes"] == [
        "segment_boundary_whitespace", "overlong_segment"]


def test_short_english_source_coalesces_after_exact_boundary_recovery():
    output, metadata = validate("Hello, friend.", ["Hello,", "friend."])
    assert output[0].segmented_texts == ["Hello, friend."]
    assert metadata["source"]["segmentation_repair"]["reason_codes"] == [
        "segment_boundary_whitespace", "short_source_split"]


def test_fallback_prefers_short_natural_boundary_over_splitting_a_latin_word():
    words = ["acknowledgements", "counterrevolutionaries", "arrived."]
    text = " ".join(words)
    parts = split_display_text(text)
    assert "".join(parts) == text
    assert all(display_width(p) <= MAX_LINE_WIDTH for p in parts)
    assert [word for part in parts for word in part.split()] == words


def test_recovered_english_display_timing_stays_proportional_inside_untouched_source():
    pieces = ["If the weather clears,", "we'll take the train", "and meet at five."]
    text = " ".join(pieces)
    output, metadata = validate(text, pieces)
    source = output[0]
    source.tokens = [Token(word, "reading", "meaning") for word in text.split()]
    before = asdict(source)
    timeline = SubtitleTimeline([source], gap_fill_ms=0)
    assert len(timeline.cues) == 3
    assert timeline.cues[0].start == source.start
    assert timeline.cues[-1].end == source.end
    assert "".join(c.text for c in timeline.cues) == text
    assert all(segment.timing_basis == "text_proportional" for segment in timeline.segments)
    assert metadata["source"]["timing_basis"] == "text_proportional"
    for left, right in zip(timeline.cues, timeline.cues[1:]):
        assert left.end == right.start
        assert timeline.subtitle_at(round(right.start * 1000)).id == right.id
    assert [t.text for c in timeline.cues for t in c.tokens] == text.split()
    assert asdict(source) == before
