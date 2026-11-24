"""Preparation ports, structured stages, and pure reset/readiness contracts."""

from copy import deepcopy
from dataclasses import asdict, replace
from types import SimpleNamespace

import pytest

from tingmu.models import AppSettings, Cue, LanguagePair, StudyDocument, Token
from tingmu.storage import Store


def test_local_pipeline_freezes_settings_and_persists_stage_codes_with_raw_sources(tmp_path, monkeypatch):
    from tingmu import pipeline

    media = tmp_path / "synthetic.mkv"
    media.write_bytes(b"synthetic media identity")
    store = Store(tmp_path / "data")
    settings = AppSettings(source_language="zh-CN", target_language="en", cloud_text_enabled=False,
                           audio_track=2, asr_model="small", review_enabled=False)
    source = Cue("raw-id", 1.25, 4.75, "Hello, friend.", "Original ASR, unmodified")
    source_before = asdict(source)
    calls, events, checkpoints = [], [], []

    def transcribe(path, **kwargs):
        calls.append((path, kwargs))
        return [source]

    def progress(event):
        events.append(event)
        # A UI settings edit while a request runs must affect only the next job.
        settings.audio_track = 9
        settings.target_language = "ja"
        settings.asr_model = "large-v3"

    save = store.save_document

    def checkpoint(document):
        checkpoints.append(deepcopy(document))
        return save(document)

    monkeypatch.setattr(pipeline, "transcribe", transcribe)
    monkeypatch.setattr(pipeline, "annotate_local", lambda cues, language: [replace(c, tokens=[Token(c.text)]) for c in cues])
    monkeypatch.setattr(pipeline, "LearningAgent", lambda **kwargs: pytest.fail("local stage constructed a text client"))
    monkeypatch.setattr(store, "save_document", checkpoint)
    result = pipeline.prepare_media(media, settings, store, progress=progress)
    assert len(calls) == 1
    assert calls[0][1]["audio_track"] == 2
    assert calls[0][1]["language"] == "en"
    assert calls[0][1]["model"] == "small"
    assert result.language == LanguagePair("zh-CN", "en")
    codes = [event.details["stage_code"] for event in events if getattr(event, "details", {}).get("stage_code")]
    assert codes == ["transcript", "local", "publish"]
    assert checkpoints[0].metadata["stage_code"] == "transcript"
    assert checkpoints[-1].metadata["stage_code"] == "publish"
    for saved in checkpoints:
        cue = saved.cues[0]
        assert (cue.id, cue.start, cue.end, cue.original_text) == (
            source.id, source.start, source.end, source.original_text)
        assert saved.metadata["raw_display_text"] == {source.id: source.text}
    assert asdict(source) == source_before
    assert result.metadata["lesson_status"] == "local_only"


def test_explicit_context_cancellation_stops_before_backend_or_storage(tmp_path, monkeypatch):
    from tingmu import pipeline, preparation

    media = tmp_path / "synthetic.mkv"
    media.write_bytes(b"synthetic")
    store = Store(tmp_path / "data")
    monkeypatch.setattr(store, "load_document", lambda *args: pytest.fail("cancelled context read storage"))
    monkeypatch.setattr(pipeline, "transcribe", lambda *args, **kwargs: pytest.fail("cancelled context ran ASR"))
    context = preparation.PreparationContext(media, AppSettings(), store, pipeline._ports(), cancelled=lambda: True)
    with pytest.raises(preparation.PreparationCancelled):
        preparation.plan_preparation(context)
    assert context.document is None


def test_queue_stage_code_is_authoritative_even_when_label_means_another_stage():
    from tingmu.batch import _apply_progress
    from tingmu.preparation_stages import stage_code, stage_label

    job = {"stage_name": "Old label", "stage_code": "transcript", "batches": [{"id": 7}]}
    _apply_progress(job, {"stage": "字幕识别", "stage_code": "verification", "done": 2, "total": 3, "unit": "句"})
    assert job["stage_code"] == "verification"
    assert job["stage_done"] == 2 and job["stage_total"] == 3
    assert job["batches"] == []
    assert stage_code(stage_label("verification")) == "verification"
    # The legacy compatibility path still understands persisted old queue labels.
    _apply_progress(job, {"stage": "文本复核"})
    assert job["stage_code"] == "review"


@pytest.mark.parametrize("state,expected_code", [("ready", "ready"), ("weights", "model_missing"),
                                                ("support", "support_missing"), ("runtime", "runtime_missing")])
def test_model_readiness_returns_typed_status_without_downloads(state, expected_code, monkeypatch, tmp_path):
    from tingmu import workspace_models

    monkeypatch.setattr(workspace_models, "_assets", lambda model: (tmp_path, ["synthetic/repository"]))
    monkeypatch.setattr(workspace_models, "_cached", lambda *args: state != "weights")
    monkeypatch.setattr(workspace_models, "_support", lambda model: SimpleNamespace(
        inventory=lambda: {"complete": state != "support"}))

    def runtime():
        if state == "runtime":
            raise RuntimeError("Synthetic runtime unavailable")
        return "synthetic-python"

    monkeypatch.setattr(workspace_models, "_qwen_python", runtime)
    result = workspace_models.model_readiness("qwen3-asr-0.6b")
    assert isinstance(result, workspace_models.ModelReadiness)
    assert result.ready is (state == "ready")
    assert result.code == expected_code
    assert workspace_models.model_status("qwen3-asr-0.6b") == result.message


def test_teaching_reset_is_pure_preserves_sources_and_clears_failed_teaching_state():
    from tingmu.material_reset import reset_teaching_document

    cue = Cue("source-id", 2.125, 5.75, "Reviewed wording", "Raw ASR wording",
              translation="Old translation", explanation="Old teaching", tokens=[Token("Reviewed")],
              segmented_texts=["Reviewed wording"])
    document = StudyDocument("synthetic.mkv", LanguagePair("zh-CN", "en"), [cue], metadata={
        "stage": "partial", "stage_code": "verification", "review_stage": "ready",
        "raw_display_text": {cue.id: "Raw display wording"}, "raw_asr_audit": "audit/original.json",
        "review_records": {cue.id: {"state": "corrected", "reason": "Saved review reason"}},
        "cue_corrections": {cue.id: {"text": cue.text, "segmented_texts": cue.segmented_texts,
                                     "note": "User-confirmed wording"}},
        "subtitle_plan": {"identity": "chosen-source", "source_id": "sidecar-one"},
        "annotation_spec": {"model": "old"}, "completed_ids": [cue.id], "annotated_count": 1,
        "teaching_pending_errors": {cue.id: "old failed row"}, "translation_review": {"status": "partial"},
        "model_responses": [{"usage": {"total_tokens": 12}}], "analysis_batches": [{"status": "failed"}],
        "checkpoint_events": [{"count": 1}], "lesson_status": "needs_teaching", "error": "OldError"})
    before = deepcopy(document.to_dict())
    reset = reset_teaching_document(document)
    assert document.to_dict() == before
    assert reset is not document and reset.cues[0] is not cue
    assert reset.cues[0].text == cue.text
    assert (reset.cues[0].id, reset.cues[0].start, reset.cues[0].end, reset.cues[0].original_text) == (
        cue.id, cue.start, cue.end, cue.original_text)
    assert reset.cues[0].translation == reset.cues[0].explanation == ""
    assert reset.cues[0].tokens == []
    for key in ("raw_display_text", "raw_asr_audit", "review_records", "cue_corrections", "subtitle_plan"):
        assert reset.metadata[key] == document.metadata[key]
    for key in ("annotation_spec", "teaching_pending_errors", "translation_review", "model_responses",
                "analysis_batches", "checkpoint_events", "lesson_status"):
        assert key not in reset.metadata
    assert reset.metadata["completed_ids"] == []
    assert reset.metadata["annotated_count"] == 0
    assert reset.metadata["reuse_transcript_once"] is True
    reset.metadata["cue_corrections"][cue.id]["note"] = "changed reset copy"
    assert document.to_dict() == before


def test_job_reset_is_pure_and_keeps_original_language_workflow_and_audio_settings():
    from tingmu.material_reset import reset_job_material_state

    previous = AppSettings(source_language="zh-CN", target_language="en", audio_track=2,
                           asr_model="small", preparation_workflow="subtitle_agent")
    updated = AppSettings(source_language="ja", target_language="ko", audio_track=9,
                          asr_model="large-v3", preparation_workflow="classic", teaching_preferences="New preference")
    job = {"id": "stable-job", "media_path": "synthetic.mkv", "settings": previous.to_dict(), "status": "failed",
           "stage_code": "verification", "stage_name": "译稿核验", "error": "OldError",
           "batches": [{"id": 1}], "subtitle_path": "provided.srt", "reuse_existing_transcript": True}
    before = deepcopy(job)
    reset = reset_job_material_state(job, "teaching", True, updated)
    assert job == before
    assert reset["id"] == job["id"] and reset["media_path"] == job["media_path"]
    assert reset["status"] == "queued" and reset["teaching_only"]
    assert not reset["force_asr"]
    for key in ("source_language", "target_language", "audio_track", "asr_model", "preparation_workflow"):
        assert reset["settings"][key] == job["settings"][key]
    assert reset["settings"]["teaching_preferences"] == "New preference"
    assert "stage_code" not in reset and "batches" not in reset
    assert "reuse_existing_transcript" not in reset
    reset["settings"]["audio_track"] = 5
    assert job == before and updated.audio_track == 9


def test_store_teaching_reset_still_keeps_personal_entries_and_backup(tmp_path):
    store = Store(tmp_path / "data")
    language = LanguagePair("zh-CN", "en")
    media = str(tmp_path / "synthetic.mkv")
    cue = Cue("source", 1.5, 6.25, "Source wording", "Untouched original",
              translation="Translation", explanation="Teaching")
    document = StudyDocument(media, language, [cue], metadata={"review_stage": "ready",
        "raw_display_text": {cue.id: cue.text}, "completed_ids": [cue.id], "annotated_count": 1})
    path = store.save_document(document)
    original_bytes = path.read_bytes()
    store.save_note(media, cue, "Personal note survives reset", language)
    entries = store.entries(media)
    assert store.reset_material(media, language, "teaching")
    reset = store.load_document(media, language)
    assert reset.cues[0].original_text == cue.original_text
    assert reset.cues[0].text == cue.text and reset.cues[0].translation == ""
    assert store.entries(media) == entries
    assert any(candidate.read_bytes() == original_bytes for candidate in store.data_dir.rglob("*.json")
               if candidate != path)
    assert not path.with_suffix(".pending").exists()
