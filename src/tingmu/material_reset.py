"""Pure reset rules shared by material storage and preparation queue adapters."""

from copy import deepcopy
from dataclasses import fields, replace

from .models import AppSettings, StudyDocument

TEACHING_METADATA = (
    "annotation_spec", "model_responses", "analysis_batches", "checkpoint_events", "finished_at",
    "translation_review", "lesson_status", "teaching_pending_errors",
)
QUEUE_PROGRESS_FIELDS = (
    "plan", "decision", "source_name", "reference_name", "evidence_counts", "evidence_notes",
    "plan_identity", "needs_teaching", "retry_text_settings", "stage_name", "stage_code", "activity",
    "stage_total", "stage_done", "unit", "batches", "reuse_existing_transcript",
)
PRESERVED_AUDIO_SETTINGS = (
    "asr_backend", "asr_model", "device", "audio_track", "suppress_bgm", "speaker_turns", "asr_endpoint",
    "external_asr_model", "asr_response_format", "asr_key_env", "allow_audio_upload", "local_asr_endpoint",
    "local_asr_api_model", "local_asr_key_env", "local_asr_response_format",
)


def reset_teaching_document(document: StudyDocument) -> StudyDocument:
    """Clear generated lessons while preserving sources, timing and human corrections."""
    document = deepcopy(document)
    document.cues = [replace(c, translation="", explanation="", tokens=[]) for c in document.cues]
    for key in TEACHING_METADATA:
        document.metadata.pop(key, None)
    reviewed = document.metadata.get("review_stage") == "ready"
    document.metadata.update(stage="reviewed" if reviewed else "transcribed",
                             stage_code="review" if reviewed else "transcript",
                             completed_ids=[], annotated_count=0, error="", reuse_transcript_once=True)
    return document


def reset_job_material_state(job: dict, mode: str, requeue: bool,
                             settings: AppSettings | None = None) -> dict:
    if mode not in ("all", "teaching"):
        raise ValueError("无效的清除范围")
    job = deepcopy(job)
    allowed = {f.name for f in fields(AppSettings) if not f.name.startswith("_")}
    previous = AppSettings(**{k: v for k, v in job["settings"].items() if k in allowed})
    current = deepcopy(settings) if settings else previous
    current.source_language = previous.source_language
    current.target_language = previous.target_language
    current.level = previous.level
    current.preparation_workflow = previous.preparation_workflow
    if mode == "teaching":
        for name in PRESERVED_AUDIO_SETTINGS:
            setattr(current, name, getattr(previous, name))
    job.update(settings=current.to_dict(), status="queued" if requeue else "cleared",
               progress="等待重新生成" if requeue else "材料已清除", error="", error_message="",
               completed_cues=0, total_cues=0)
    for key in QUEUE_PROGRESS_FIELDS:
        job.pop(key, None)
    job["force_asr"] = mode == "all" and current.preparation_workflow == "classic"
    job["teaching_only"] = mode == "teaching"
    if job["force_asr"]:
        job["subtitle_path"] = None
    return job
