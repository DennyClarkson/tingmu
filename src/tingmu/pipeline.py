"""Resumable local transcription → validated language material preparation."""

from dataclasses import asdict, replace
from pathlib import Path
import time
import hashlib
import json

from .agent import ANNOTATION_VERSION, LearningAgent, annotate_local
from .media import load_subtitles, transcribe
from .language import normalize_cues
from .models import AppSettings, StudyDocument
from .review import REVIEW_VERSION, TranscriptReviewer
from .storage import Store, media_fingerprint


class PreparationCancelled(RuntimeError):
    pass


def find_subtitle(media_path: Path, target_language: str) -> Path | None:
    for stem in (f"{media_path.stem}.{target_language}", media_path.stem):
        for extension in (".srt", ".ass", ".vtt", ".ssa"):
            candidate = media_path.with_name(stem + extension)
            if candidate.is_file():
                return candidate
    return None


def _review_document(document, settings, store, spec, report, check_cancel, cancelled, profile=None):
    """Checkpoint the whole-film reviewer independently from lesson annotation."""
    metadata = document.metadata
    same = metadata.get("review_spec") == spec
    if same and metadata.get("review_stage") == "ready":
        return
    raw = metadata.get("raw_display_text", {})
    original_cues = normalize_cues(
        [replace(c, text=raw.get(c.id, c.original_text or c.text), tokens=[], translation="", explanation="",
                 segmented_texts=[])
         for c in document.cues], document.language, settings.target_script)
    completed = set(metadata.get("review_completed_ids", [])) if same else set()
    if not same:
        document.cues = original_cues
    responses = list(metadata.get("review_responses", [])) if same else []
    batches = list(metadata.get("review_batches", [])) if same else []
    records = dict(metadata.get("review_records", {})) if same else {}
    metadata.update(review_spec=spec, review_stage="running", stage="reviewing",
                    review_completed_ids=sorted(completed), reviewed_count=len(completed),
                    review_records=records, review_responses=responses, review_batches=batches,
                    completed_ids=[], annotated_count=0, error="")
    store.save_document(document)
    reviewer = TranscriptReviewer(provider=spec["provider"], model=spec["model"],
                                  **({"profile": profile} if profile else {}))

    def checkpoint(cues):
        update = {c.id: c for c in normalize_cues(cues, document.language, settings.target_script)}
        document.cues = [update.get(c.id, c) for c in document.cues]
        completed.update(update)
        metadata.update(review_completed_ids=sorted(completed), reviewed_count=len(completed),
                        review_records={**records, **reviewer.review_metadata},
                        review_responses=responses + reviewer.response_metadata,
                        review_batches=batches + reviewer.batch_metadata,
                        reviewed_text={c.id: c.text for c in document.cues if c.id in completed},
                        reviewed_segmented_texts={c.id: c.segmented_texts for c in document.cues if c.id in completed})
        store.save_document(document)

    try:
        check_cancel()
        pending = [c for c in document.cues if c.id not in completed]
        report(f"{spec['model']} 全片复核：{len(completed)}/{len(document.cues)} 句")
        if pending:
            result = reviewer.review(pending, document.language, progress=report,
                                     cancelled=cancelled, checkpoint=checkpoint, context_cues=original_cues)
            checkpoint(result)
        check_cancel()
        metadata.update(review_stage="ready", stage="reviewed", review_finished_at=time.time())
        store.save_document(document)
    except Exception as exc:
        metadata.update(review_stage="partial", stage="partial", error=type(exc).__name__,
                        review_responses=responses + reviewer.response_metadata,
                        review_batches=batches + reviewer.batch_metadata)
        store.save_document(document)
        raise


def prepare_media(media_path: Path, settings: AppSettings, store: Store,
                  subtitle_path: Path | None = None, start: float = 0,
                  duration: float | None = None, progress=None, cancelled=None,
                  force_asr: bool = False) -> StudyDocument:
    media_path = Path(media_path).resolve()
    if not media_path.is_file():
        raise FileNotFoundError(f"视频不存在：{media_path}")
    if start < 0 or (duration is not None and duration <= 0):
        raise ValueError("片段时间范围无效")
    language = settings.language
    report = progress or (lambda message: None)

    def check_cancel():
        if cancelled and cancelled():
            raise PreparationCancelled("已取消；已完成的转写和学习材料已保存")

    check_cancel()
    subtitle_path = (None if force_asr else
                     Path(subtitle_path).resolve() if subtitle_path else find_subtitle(media_path, language.target))
    annotation_profile = review_profile = None
    if settings.cloud_text_enabled and (settings.annotation_profile_id or settings.review_profile_id):
        from .providers import ModelRegistry
        registry = ModelRegistry(store.data_dir)
        if settings.annotation_profile_id:
            annotation_profile = registry.resolve(settings.annotation_profile_id)
        if settings.review_enabled and settings.review_profile_id:
            review_profile = registry.resolve(settings.review_profile_id)

    def profile_identity(profile):
        public = {k: profile.get(k) for k in ("id", "provider", "protocol", "model", "base_url",
                                             "reasoning_effort", "max_tokens")}
        return hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()[:24]
    transcript_spec = {
        "audio_track": settings.audio_track, "asr_model": settings.asr_model,
        "target": language.target, "start": start, "duration": duration,
        "subtitle": str(subtitle_path) if subtitle_path else None,
        "subtitle_fingerprint": media_fingerprint(subtitle_path) if subtitle_path else None,
    }
    if force_asr:
        transcript_spec["force_asr"] = True
    annotation_spec = {
        "provider": settings.provider if settings.cloud_text_enabled else "local",
        "model": settings.model if settings.cloud_text_enabled and settings.provider != "local" else "",
        "language": asdict(language),
        "target_script": settings.target_script,
        "text_normalization": "hk-cantonese-v1",
        "teaching_version": ANNOTATION_VERSION,
    }
    if annotation_profile:
        annotation_spec.update(provider=annotation_profile["provider"], model=annotation_profile["model"],
                               profile=profile_identity(annotation_profile))
    if settings.teaching_preferences:
        if not isinstance(settings.teaching_preferences, str) or len(settings.teaching_preferences) > 6000:
            raise ValueError("讲解偏好最多 6000 字符")
        annotation_spec["teaching_preferences"] = settings.teaching_preferences
    review_spec = ({"provider": settings.review_provider, "model": settings.review_model,
                    "version": REVIEW_VERSION, "language": asdict(language),
                    "target_script": settings.target_script}
                   if settings.review_enabled and settings.cloud_text_enabled else None)
    if review_spec and review_profile:
        review_spec.update(provider=review_profile["provider"], model=review_profile["model"],
                           profile=profile_identity(review_profile))
    annotation_spec["review"] = review_spec
    cached = store.load_document(str(media_path), language)
    if cached and cached.metadata.get("reuse_transcript_once") and not force_asr:
        transcript_spec = cached.metadata.get("transcript_spec", transcript_spec)
        if cached.metadata.get("review_stage") == "ready" and cached.metadata.get("review_spec"):
            review_spec = cached.metadata.get("review_spec", review_spec)
            annotation_spec["review"] = review_spec
    reusable = cached is not None and cached.metadata.get("transcript_spec") == transcript_spec
    if reusable:
        document = cached
        report(f"读取已保存转写：{len(document.cues)} 句")
        if document.metadata.get("annotation_spec") == annotation_spec and document.metadata.get("stage") == "ready":
            report("学习材料已就绪")
            return document
    else:
        asr_metadata = {}
        if subtitle_path:
            report(f"读取字幕：{subtitle_path.name}")
            cues = load_subtitles(subtitle_path)
            if start or duration is not None:
                limit = start + duration if duration is not None else float("inf")
                cues = [c for c in cues if c.end > start and c.start < limit]
        else:
            report("本地语音识别")
            cues = transcribe(media_path, language=language.target, model=settings.asr_model,
                              device=settings.device, audio_track=settings.audio_track, start=start,
                              duration=duration, progress=report, cancelled=cancelled,
                              metadata_out=asr_metadata)
        if not cues:
            raise ValueError("此片段没有识别到字幕。请检查音轨、时间范围，或导入现有字幕。")
        document = StudyDocument(str(media_path), language, cues, metadata={
            "stage": "transcribed", "transcript_spec": transcript_spec,
            "media_fingerprint": media_fingerprint(media_path),
            "created_at": time.time(), "completed_ids": [],
            "raw_display_text": {c.id: c.text for c in cues},
            **asr_metadata,
        })
        # Save immediately, before any optional model call can fail.
        store.save_document(document)
    check_cancel()
    if review_spec:
        _review_document(document, settings, store, review_spec, report, check_cancel, cancelled, review_profile)
    same_annotation = document.metadata.get("annotation_spec") == annotation_spec
    completed = set(document.metadata.get("completed_ids", [])) if same_annotation else set()
    if not same_annotation:
        raw_display = document.metadata.get("reviewed_text" if review_spec else "raw_display_text", {})
        reviewed_parts = document.metadata.get("reviewed_segmented_texts", {})
        document.cues = [replace(c, text=raw_display.get(c.id, c.original_text or c.text),
                                 translation="", explanation="", tokens=[],
                                 segmented_texts=reviewed_parts.get(c.id, c.segmented_texts) if review_spec else [])
                         for c in document.cues]
    document.cues = normalize_cues(document.cues, language, settings.target_script)
    previous_responses = list(document.metadata.get("model_responses", [])) if same_annotation else []
    previous_batches = list(document.metadata.get("analysis_batches", [])) if same_annotation else []
    previous_checkpoints = list(document.metadata.get("checkpoint_events", [])) if same_annotation else []
    document.metadata.update(annotation_spec=annotation_spec, stage="annotating",
                             completed_ids=sorted(completed), annotated_count=len(completed),
                             model_responses=previous_responses, analysis_batches=previous_batches,
                             checkpoint_events=previous_checkpoints, error="")
    document.provider = annotation_spec["provider"]
    document.model = annotation_spec["model"]
    pending = [c for c in document.cues if c.id not in completed]
    agent = None
    store.save_document(document)

    def checkpoint(cues):
        cues = normalize_cues(cues, language, settings.target_script)
        update = {c.id: c for c in cues}
        document.cues = [update.get(c.id, c) for c in document.cues]
        completed.update(update)
        document.metadata["completed_ids"] = sorted(completed)
        document.metadata["annotated_count"] = len(completed)
        if agent is not None:
            document.metadata["model_responses"] = previous_responses + getattr(agent, "response_metadata", [])
            document.metadata["analysis_batches"] = previous_batches + getattr(agent, "batch_metadata", [])
            state = getattr(agent, "checkpoint_metadata", None)
            document.metadata["checkpoint_events"] = previous_checkpoints + ([dict(state)] if isinstance(state, dict) else [])
        store.save_document(document)

    try:
        if document.provider == "local":
            report("本地分词与读音")
            checkpoint(annotate_local(pending, language))
        elif pending:
            report(f"{document.provider} 分析：{len(pending)} 句")
            agent = LearningAgent(provider=document.provider, model=document.model or None,
                                  **({"profile": annotation_profile} if annotation_profile else {}))
            agent.preserve_text = bool(review_spec)
            agent.teaching_preferences = settings.teaching_preferences
            result = agent.annotate(pending, language, progress=report, cancelled=cancelled,
                                    checkpoint=checkpoint, context_cues=document.cues)
            checkpoint(result)
        check_cancel()
        document.metadata.update(stage="ready", finished_at=time.time(), error="")
        document.metadata.pop("reuse_transcript_once", None)
        store.save_document(document)
        report(f"已准备 {len(document.cues)} 句")
        return document
    except Exception as exc:
        document.metadata["stage"] = "partial"
        if agent is not None:
            document.metadata["model_responses"] = previous_responses + getattr(agent, "response_metadata", [])
            document.metadata["analysis_batches"] = previous_batches + getattr(agent, "batch_metadata", [])
            state = getattr(agent, "checkpoint_metadata", None)
            document.metadata["checkpoint_events"] = previous_checkpoints + ([dict(state)] if isinstance(state, dict) else [])
        # Error class only: an upstream exception could contain provider request details.
        document.metadata["error"] = type(exc).__name__
        store.save_document(document)
        raise
