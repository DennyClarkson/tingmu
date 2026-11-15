from dataclasses import replace

import pytest

from tingmu import pipeline
from tingmu.models import AppSettings, Cue
from tingmu.storage import Store


def setup(tmp_path, monkeypatch):
    video = tmp_path / "video.mkv"
    video.touch()
    store = Store(tmp_path / "data")
    source = [Cue("one", 0, 1, "raw one"), Cue("two", 1, 2, "raw two")]
    monkeypatch.setattr(pipeline, "transcribe", lambda *args, **kwargs: source)
    settings = AppSettings(source_language="en", target_language="en", review_enabled=True)
    return video, store, settings


class Reviewer:
    calls = []

    def __init__(self, **kwargs):
        self.review_metadata = {}
        self.response_metadata = []
        self.batch_metadata = []

    def review(self, cues, language, **kwargs):
        self.calls.append([c.text for c in cues])
        result = [replace(c, text="reviewed " + c.text, tokens=[], translation="", explanation="",
                          segmented_texts=["reviewed " + c.text]) for c in cues]
        self.review_metadata = {c.id: {"state": "corrected", "reason": "test correction"} for c in result}
        kwargs["checkpoint"](result)
        return result


class Teacher:
    calls = []

    def __init__(self, **kwargs):
        self.preserve_text = False

    def annotate(self, cues, language, **kwargs):
        assert self.preserve_text
        self.calls.append([c.text for c in cues])
        assert all(c.text.startswith("reviewed ") for c in kwargs["context_cues"])
        return [replace(c, translation="translation", explanation="teaching") for c in cues]


def test_review_precedes_teaching_and_ready_cache_makes_no_calls(tmp_path, monkeypatch):
    video, store, settings = setup(tmp_path, monkeypatch)
    Reviewer.calls = []
    Teacher.calls = []
    monkeypatch.setattr(pipeline, "TranscriptReviewer", Reviewer)
    monkeypatch.setattr(pipeline, "LearningAgent", Teacher)
    result = pipeline.prepare_media(video, settings, store)
    assert Reviewer.calls == [["raw one", "raw two"]]
    assert Teacher.calls == [["reviewed raw one", "reviewed raw two"]]
    assert [c.original_text for c in result.cues] == ["raw one", "raw two"]
    assert [(c.start, c.end) for c in result.cues] == [(0, 1), (1, 2)]
    assert result.metadata["review_stage"] == "ready"
    assert [c.segmented_texts for c in result.cues] == [["reviewed raw one"], ["reviewed raw two"]]
    assert store.load_document(str(video), settings.language).cues[0].segmented_texts == ["reviewed raw one"]
    monkeypatch.setattr(pipeline, "TranscriptReviewer", lambda **k: pytest.fail("Review repeated"))
    monkeypatch.setattr(pipeline, "LearningAgent", lambda **k: pytest.fail("Teaching repeated"))
    pipeline.prepare_media(video, settings, store)


def test_partial_review_resumes_then_teacher_failure_does_not_repeat_review(tmp_path, monkeypatch):
    video, store, settings = setup(tmp_path, monkeypatch)
    seen = []

    class Interrupted(Reviewer):
        def review(self, cues, language, **kwargs):
            seen.append([c.id for c in cues])
            if len(seen) == 1:
                kwargs["checkpoint"]([replace(cues[0], text="reviewed raw one")])
                raise RuntimeError("interrupted")
            return super().review(cues, language, **kwargs)

    class FailedTeacher(Teacher):
        def annotate(self, *args, **kwargs):
            raise RuntimeError("annotation interruption")

    monkeypatch.setattr(pipeline, "TranscriptReviewer", Interrupted)
    monkeypatch.setattr(pipeline, "LearningAgent", FailedTeacher)
    with pytest.raises(RuntimeError):
        pipeline.prepare_media(video, settings, store)
    saved = store.load_document(str(video), settings.language)
    assert saved.metadata["review_stage"] == "partial"
    assert saved.metadata["review_completed_ids"] == ["one"]
    with pytest.raises(RuntimeError):
        pipeline.prepare_media(video, settings, store)
    assert seen == [["one", "two"], ["two"]]
    monkeypatch.setattr(pipeline, "TranscriptReviewer", lambda **k: pytest.fail("Finished review repeated"))
    monkeypatch.setattr(pipeline, "LearningAgent", Teacher)
    result = pipeline.prepare_media(video, settings, store)
    assert result.metadata["stage"] == "ready"


def test_cloud_off_blocks_review_even_when_enabled(tmp_path, monkeypatch):
    video, store, settings = setup(tmp_path, monkeypatch)
    settings.cloud_text_enabled = False
    monkeypatch.setattr(pipeline, "TranscriptReviewer", lambda **k: pytest.fail("Cloud review attempted"))
    monkeypatch.setattr(pipeline, "annotate_local", lambda cues, lang: cues)
    result = pipeline.prepare_media(video, settings, store)
    assert result.provider == "local"
    assert result.cues[0].text == "raw one"


def test_review_version_change_restarts_from_raw_and_cloud_off_clears_boundaries(tmp_path, monkeypatch):
    video, store, settings = setup(tmp_path, monkeypatch)
    Reviewer.calls = []
    monkeypatch.setattr(pipeline, "TranscriptReviewer", Reviewer)
    monkeypatch.setattr(pipeline, "LearningAgent", Teacher)
    pipeline.prepare_media(video, settings, store)
    monkeypatch.setattr(pipeline, "REVIEW_VERSION", "future-test-version")
    result = pipeline.prepare_media(video, settings, store)
    assert Reviewer.calls == [["raw one", "raw two"], ["raw one", "raw two"]]
    assert [c.original_text for c in result.cues] == ["raw one", "raw two"]
    settings.cloud_text_enabled = False
    monkeypatch.setattr(pipeline, "annotate_local", lambda cues, lang: cues)
    result = pipeline.prepare_media(video, settings, store)
    assert [c.text for c in result.cues] == ["raw one", "raw two"]
    assert [c.segmented_texts for c in result.cues] == [[], []]
    settings.cloud_text_enabled = True
    monkeypatch.setattr(pipeline, "TranscriptReviewer", lambda **k: pytest.fail("Saved review repeated"))
    result = pipeline.prepare_media(video, settings, store)
    assert [c.text for c in result.cues] == ["reviewed raw one", "reviewed raw two"]
    assert [c.segmented_texts for c in result.cues] == [["reviewed raw one"], ["reviewed raw two"]]
