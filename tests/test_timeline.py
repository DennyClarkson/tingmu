from dataclasses import asdict

from tingmu.models import Cue, StudyDocument, LanguagePair, Token
from tingmu.timeline import SubtitleTimeline


def test_tiny_sources_stay_separate_and_only_short_blank_is_filled():
    cues = [Cue("a", 1, 1.08, "梗係", tokens=[Token("梗係", "gang2 hai6")]),
            Cue("b", 1.13, 2.8, "想啦。", tokens=[Token("想啦。")]),
            Cue("c", 5, 7.5, "下一句")]
    before = [asdict(c) for c in cues]
    timeline = SubtitleTimeline(cues)
    assert [c.id for c in timeline.cues] == ["a", "b", "c"]
    assert timeline.subtitle_at(1100).text == "梗係"
    assert timeline.subtitle_at(1130).text == "想啦。"
    assert timeline.source_ids("a") == ("a",)
    assert (timeline.cues[0].start, timeline.cues[0].end) == (1, 1.08)
    assert [asdict(c) for c in cues] == before


def test_gap_limit_boundaries_and_zero_gap_setting():
    cues = [Cue("a", 0, 3, "first"), Cue("b", 3.5, 6.2, "second")]
    timeline = SubtitleTimeline(cues, gap_fill_ms=500)
    assert timeline.subtitle_at(3499).id == "a"
    assert timeline.subtitle_at(3500).id == "b"
    assert SubtitleTimeline(cues, gap_fill_ms=499).subtitle_at(3000) is None
    assert SubtitleTimeline(cues, gap_fill_ms=0).subtitle_at(3050) is None
    assert cues[0].end == 3


def test_sparse_lines_never_hold_into_long_silence_and_seeks_are_immediate():
    timeline = SubtitleTimeline([Cue("a", 1, 1.1, "啊"), Cue("b", 10, 11, "好")],
                                min_duration_ms=5000, companion_hold_ms=8000)
    for at, identity in [(1050, "a"), (2300, None), (10100, "b"), (0, None),
                         (1050, "a"), (1100, None), (11000, None)]:
        for surface in (timeline.subtitle_at, timeline.companion_at):
            cue = surface(at)
            assert (cue.id if cue else None) == identity


def test_dense_cues_each_transition_once_without_merge_or_backlog():
    cues = [Cue(str(i), i * .2, i * .2 + .05, f"句{i}") for i in range(50)]
    timeline = SubtitleTimeline(cues)
    assert len(timeline.cues) == 50
    assert [timeline.subtitle_at(i * 200 + 1).id for i in range(50)] == [str(i) for i in range(50)]
    for i in reversed(range(49)):
        assert timeline.subtitle_at(i * 200 + 199).id == str(i)
        assert timeline.companion_at(i * 200 + 200).id == str(i + 1)
    assert timeline.subtitle_at(9900) is None


def test_overlap_returns_underlying_dialogue_without_extending_short_sign():
    timeline = SubtitleTimeline([Cue("long", 2, 10, "long"), Cue("short", 4, 4.2, "short"),
                                 Cue("other", 4.4, 4.5, "other"), Cue("next", 10.3, 11, "next")])
    assert timeline.subtitle_at(0) is None
    assert timeline.subtitle_at(4100).id == "short"
    assert timeline.subtitle_at(4250).id == "long"
    assert timeline.subtitle_at(4450).id == "other"
    assert timeline.subtitle_at(6500).id == "long"
    assert timeline.subtitle_at(10200).id == "long"
    assert timeline.subtitle_at(10300).id == "next"
    assert timeline.subtitle_at(11000) is None


def test_reviewed_segments_have_explicit_estimates_and_preserve_raw_source():
    text = "今日我哋一齊去睇戲，返到屋企再慢慢傾。"
    parts = ["今日我哋一齊去睇戲，", "返到屋企再慢慢傾。"]
    cue = Cue("source", 2, 10, text, "原始ASR", tokens=[Token(p) for p in parts], segmented_texts=parts)
    before = asdict(cue)
    timeline = SubtitleTimeline([cue])
    assert len(timeline.cues) == 2
    assert "".join(c.text for c in timeline.cues) == text
    assert timeline.cues[0].start == 2
    assert timeline.cues[-1].end == 10
    assert timeline.cues[0].end == timeline.cues[1].start
    assert all(s.timing_basis == "text_proportional" for s in timeline.segments)
    assert all(timeline.source_ids(c.id) == ("source",) for c in timeline.cues)
    assert [c.id for c in timeline.cues] == ["source:part:1", "source:part:2"]
    assert all("".join(t.text for t in c.tokens) == c.text for c in timeline.cues)
    assert asdict(cue) == before
    doc = StudyDocument("media", LanguagePair(), [cue])
    assert StudyDocument.from_dict(doc.to_dict()).cues[0].segmented_texts == parts


def test_split_word_does_not_invent_partial_word_gloss_or_reading():
    cue = Cue("s", 0, 2, "abcdef", tokens=[Token("abcdef", "reading", "meaning", True)],
              segmented_texts=["abc", "def"])
    timeline = SubtitleTimeline([cue])
    assert [c.tokens[0].text for c in timeline.cues] == ["abc", "def"]
    assert all(c.tokens[0].meaning == c.tokens[0].reading == "" for c in timeline.cues)


def test_corrupt_saved_segments_fall_back_to_source_and_zero_duration_is_not_invented():
    cue = Cue("s", 1, 1, "source", segmented_texts=["different"])
    timeline = SubtitleTimeline([cue, Cue("next", 1.1, 2, "next")])
    assert timeline.cues[0].id == "s"
    assert timeline.subtitle_at(1000) is None
    assert timeline.subtitle_at(1050) is None


def test_reading_minimum_borrows_only_until_a_breath_before_the_next_cue():
    cues = [Cue("first", 1, 1.08, "One"), Cue("second", 4.08, 4.16, "Two"),
            Cue("last", 5.08, 5.28, "Three")]
    timeline = SubtitleTimeline(cues, min_duration_ms=1200)
    assert [(s.start_ms, s.end_ms) for s in timeline.segments] == [
        (1000, 2200), (4080, 4980), (5080, 5280)]
    for pos, expected in ((1080, "first"), (2200, None), (4979, "second"), (4980, None),
                          (5079, None), (5080, "last"), (5280, None)):
        assert getattr(timeline.subtitle_at(pos), "id", None) == expected
        assert getattr(timeline.companion_at(pos), "id", None) == expected
    assert [c.end for c in timeline.cues] == [1.08, 4.16, 5.28]
    assert [c.end for c in timeline.export_cues()] == [2.2, 4.98, 5.28]
    assert [c.end for c in SubtitleTimeline(cues, min_duration_ms=0).export_cues()] == [1.08, 4.16, 5.28]


def test_legacy_reviewed_split_below_600ms_uses_source_text_and_verified_whole_translation():
    from tingmu.translation_review import TRANSLATION_REVIEW_VERSION, lesson_digest

    cue = Cue("s", 0, 2.39, "Xabc", "Raw original", translation="完整译文", explanation="why",
              segmented_texts=["X", "abc"])
    parts = ["片段一", "片段二"]
    review = {"version": TRANSLATION_REVIEW_VERSION, "segments": {"s": parts},
              "verified": {"s": lesson_digest(cue, parts)}}
    before = asdict(cue)
    timeline = SubtitleTimeline([cue], translation_review=review)
    assert [(s.cue.id, s.source_ids, s.timing_basis) for s in timeline.segments] == [("s", ("s",), "source")]
    assert [c.text for c in timeline.export_cues()] == ["Xabc"]
    assert [c.text for c in timeline.export_cues(translated=True)] == ["完整译文"]
    assert asdict(cue) == before
    cue.end = 2.4
    assert [(s.cue.id, s.end_ms) for s in SubtitleTimeline([cue]).segments] == [
        ("s:part:1", 600), ("s:part:2", 2400)]
