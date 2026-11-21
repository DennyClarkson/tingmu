from dataclasses import replace

import pytest
import json

from tingmu import pipeline
from tingmu.models import AppSettings, Cue
from tingmu.storage import Store


def test_translation_repairs_only_bad_cue_and_retains_hint_across_resume(tmp_path, monkeypatch):
    from tingmu.agent import LearningAgent, AnnotationValidationError
    from tingmu.batch import _failure_details
    from tingmu.models import LanguagePair, StudyDocument
    from tingmu.translation_review import TRANSLATION_REVIEW_VERSION, translation_verified

    store = Store(tmp_path / "data")
    media = tmp_path / "video.mkv"
    media.touch()
    cues = [Cue(str(i), i * 3, i * 3 + 2, f"line {i}", translation="draft", explanation="lesson")
            for i in range(6)]
    cues[2].segmented_texts = ["line", " 2"]
    review = {"version": TRANSLATION_REVIEW_VERSION, "verified": {}, "segments": {}, "responses": []}
    doc = StudyDocument(str(media), LanguagePair("en", "en"), cues, metadata={"translation_review": review})
    agent = LearningAgent("local")
    agent.max_workers = 1
    calls, fail = [], True

    def complete(worker, prompt, payload, schema):
        rows = payload["cues"]
        calls.append([r["text"] for r in rows])
        if len(calls) > 1:
            assert len(rows) == 1
            assert "segments" in rows[0]["validation_error"] and "2" in rows[0]["validation_error"]
            checkpoint = store.load_document(str(media), doc.language)
            assert len(checkpoint.metadata["translation_review"]["verified"]) == 5
        worker.last_response_metadata = {"returned_model": "deepseek-flash"}
        return json.dumps({"cues": [{"id": r["id"], "translation": "ok",
                                     "segments": [] if fail else r["segments"]} for r in rows]})

    monkeypatch.setattr(LearningAgent, "_complete", complete)
    with pytest.raises(AnnotationValidationError) as error:
        pipeline._verify_document(doc, agent, store, lambda _: None, lambda: None)
    assert (error.value.cue_number, error.value.cue_start) == (3, 6)
    displayed = _failure_details(error.value, doc)
    assert "第 3 句（00:06）" in displayed["error_message"] and "segments" in displayed["error_message"]
    assert (displayed["completed_cues"], displayed["total_cues"]) == (5, 6)
    assert calls == [[f"line {i}" for i in range(6)], ["line 2"], ["line 2"]]
    fail = False
    doc = store.load_document(str(media), doc.language)
    pipeline._verify_document(doc, agent, store, lambda _: None, lambda: None)
    assert len(calls) == 4 and calls[-1] == ["line 2"]
    review = doc.metadata["translation_review"]
    assert not review["pending_errors"] and all(translation_verified(c, review) for c in doc.cues)


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

    def verify_translations(self, cues, language, context):
        return [(c, []) for c in cues]

    def __init__(self, **kwargs):
        self.preserve_text = False

    def annotate(self, cues, language, **kwargs):
        assert self.preserve_text
        self.calls.append([c.text for c in cues])
        assert all(c.text.startswith("reviewed ") for c in kwargs["context_cues"])
        return [replace(c, translation="translation", explanation="teaching") for c in cues]


def test_invalid_batch_is_retried_in_smaller_groups_and_missing_export_is_rebuilt(tmp_path, monkeypatch):
    video, store, settings = setup(tmp_path, monkeypatch)
    checks = []

    class SplitVerifier(Teacher):
        def verify_translations(self, cues, language, context):
            checks.append(len(cues))
            if len(cues) > 1:
                raise pipeline.AnnotationValidationError("译文核验返回的分段不完整。")
            return super().verify_translations(cues, language, context)

    monkeypatch.setattr(pipeline, "TranscriptReviewer", Reviewer)
    monkeypatch.setattr(pipeline, "LearningAgent", SplitVerifier)
    doc = pipeline.prepare_media(video, settings, store)
    translated = store.material_paths(doc.media_path, settings.language)[0].with_suffix(".translation.srt")
    expected = translated.read_bytes()
    translated.unlink()
    pipeline.prepare_media(video, settings, store)
    assert checks == [2, 1, 1]
    assert translated.read_bytes() == expected


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



def test_legacy_drafts_resume_translation_review_and_detect_edits(tmp_path, monkeypatch):
    video, store, settings = setup(tmp_path, monkeypatch)
    settings.review_enabled = False
    settings.llm_high_concurrency = False
    settings.target_language = "is"
    settings.source_language = "sw"
    raw = [Cue(str(i), i, i + 1, "Góðan dag", segmented_texts=["Góðan ", "dag"]) for i in range(26)]
    monkeypatch.setattr(pipeline, "transcribe", lambda *args, **kwargs: raw)
    checks = []

    class Verifier:
        def __init__(self, **kwargs):
            pass

        def annotate(self, cues, language, **kwargs):
            return [replace(c, translation="draft", explanation="lesson", segmented_texts=["Góðan ", "dag"])
                    for c in cues]

        def verify_translations(self, cues, language, context):
            assert language.source == "sw" and language.target == "is"
            checks.append([c.id for c in cues])
            if len(checks) == 2:
                raise RuntimeError("interrupted")
            return [(replace(c, translation="Habari"), ["Habari", "za leo"]) for c in cues]

    monkeypatch.setattr(pipeline, "LearningAgent", Verifier)
    with pytest.raises(RuntimeError):
        pipeline.prepare_media(video, settings, store)
    saved = store.load_document(str(video), settings.language)
    assert saved.metadata["translation_review"]["status"] == "partial"
    assert len(saved.metadata["translation_review"]["verified"]) == 24

    def unexpected(*args, **kwargs):
        pytest.fail("Repeated ASR or teaching for existing drafts")

    monkeypatch.setattr(pipeline, "transcribe", unexpected)
    monkeypatch.setattr(Verifier, "annotate", unexpected)
    result = pipeline.prepare_media(video, settings, store)
    assert checks[-1] == ["24", "25"]
    assert result.metadata["lesson_status"] == "verified"
    assert result.metadata["translation_review"]["segments"]["0"] == ["Habari", "za leo"]
    result.cues[3].translation = "edited"
    store.save_document(result)
    result = pipeline.prepare_media(video, settings, store)
    assert checks[-1] == ["3"]
    result.metadata.pop("translation_review")
    result.metadata.pop("lesson_status")
    store.save_document(result)
    pipeline.prepare_media(video, settings, store)
    assert list(map(len, checks[-2:])) == [24, 2]
    count = len(checks)
    pipeline.prepare_media(video, settings, store)
    assert len(checks) == count



def test_queue_enriches_existing_cues_without_speech_or_source_review(tmp_path, monkeypatch):
    from tingmu.batch import PreparationQueue
    from tingmu.models import StudyDocument
    from tingmu.providers import ProviderError

    video, store, settings = setup(tmp_path, monkeypatch)
    settings.asr_backend = "external"
    settings.asr_endpoint = "invalid endpoint, unused for lessons"
    # An edited or imported document may have no transcript specification.
    cues = [Cue("a", 1, 2, "Edited", original_text="raw", translation="existing", explanation="manual lesson"),
            Cue("b", 3, 4, "Fill me")]
    document = StudyDocument(str(video), settings.language, cues, metadata={
        "cue_corrections": {"a": {"text": "Edited", "segmented_texts": [], "revision": "original-edit"}},
        "stage": "ready"})
    store.save_document(document)
    annotations, verified = [], []

    class LessonAgent:
        def __init__(self, **kwargs):
            pass

        def annotate(self, cues, language, **kwargs):
            assert self.preserve_text
            annotations.append([c.id for c in cues])
            return [replace(c, translation="draft", explanation="lesson") for c in cues]

        def verify_translations(self, cues, language, context):
            verified.append([c.id for c in cues])
            if len(verified) == 1:
                raise RuntimeError("temporary issue")
            return [(replace(c, translation="checked"), []) for c in cues]

    def unexpected(*args, **kwargs):
        pytest.fail("Existing lesson operation invoked ASR, discovery or source reviewer")

    monkeypatch.setattr(pipeline, "LearningAgent", LessonAgent)
    monkeypatch.setattr(pipeline, "TranscriptReviewer", unexpected)
    monkeypatch.setattr(pipeline, "transcribe", unexpected)
    monkeypatch.setattr(pipeline, "find_subtitle", unexpected)
    queue = PreparationQueue(store)
    job = queue.enqueue_lessons(video, settings)
    assert queue.run(job_ids=[job])[0]["status"] == "failed"
    assert queue.enqueue_lessons(video, settings) == job
    assert queue.run(job_ids=[job])[0]["status"] == "completed"
    result = store.load_document(str(video), settings.language)
    assert result.metadata["cue_corrections"]["a"]["revision"] == "original-edit"
    assert (result.cues[0].text, result.cues[0].original_text, result.cues[0].explanation) == (
        "Edited", "raw", "manual lesson")
    result.cues[0].translation = "new revision"
    store.save_document(result)
    assert queue.enqueue_lessons(video, settings) == job
    assert queue.run(job_ids=[job])[0]["status"] == "completed"
    assert annotations == [["b"]] and verified == [["a", "b"], ["a", "b"], ["a"]]
    before = store.load_document(str(video), settings.language).to_dict()
    settings.text_models_enabled = False
    with pytest.raises(ProviderError, match="讲解模型"):
        pipeline.prepare_media(video, settings, store, reuse_existing_transcript=True)
    assert store.load_document(str(video), settings.language).to_dict() == before


def test_cumulative_checkpoints_normalize_each_new_result_once(tmp_path, monkeypatch):
    from collections import Counter

    video, store, settings = setup(tmp_path, monkeypatch)
    source = [Cue(str(i), i, i + 1, f"raw {i}") for i in range(12)]
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **kw: source)
    normalized = Counter()
    normalize = pipeline.normalize_cues

    def count(cues, language, script):
        normalized.update(c.id for c in cues)
        return normalize(cues, language, script)

    class CumulativeReviewer(Reviewer):
        def review(self, cues, language, **kwargs):
            rows = [replace(c, text="reviewed " + c.text) for c in cues]
            for i in range(1, len(rows) + 1):
                kwargs["checkpoint"](rows[:i])
            return rows

    class CumulativeTeacher(Teacher):
        def annotate(self, cues, language, **kwargs):
            rows = [replace(c, translation="draft", explanation="lesson") for c in cues]
            for i in range(1, len(rows) + 1):
                kwargs["checkpoint"](rows[:i])
            return rows

    monkeypatch.setattr(pipeline, "normalize_cues", count)
    monkeypatch.setattr(pipeline, "TranscriptReviewer", CumulativeReviewer)
    monkeypatch.setattr(pipeline, "LearningAgent", CumulativeTeacher)
    doc = pipeline.prepare_media(video, settings, store)
    assert doc.metadata["lesson_status"] == "verified"
    # Full source before review and teaching, plus each new reviewer/teacher result.
    assert normalized == {cue.id: 4 for cue in source}
