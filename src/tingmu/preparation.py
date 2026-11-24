"""Explicit preparation stages with request-owned state and injected public ports.

The storage publication journal and checkpoint schemas remain owned by Store.
No stage imports the compatibility pipeline module.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable
import hashlib
import json
import time

from .agent import AnnotationValidationError
from .language import learning_text
from .models import AppSettings, LanguagePair, StudyDocument
from .providers import ProviderError
from .storage import Store, media_fingerprint
from .task_progress import Progress
from .translation_review import lesson_digest, translation_available


class PreparationCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparationPorts:
    find_subtitle: Callable
    transcribe: Callable
    load_subtitles: Callable
    normalize_cues: Callable
    annotate_local: Callable
    LearningAgent: Callable
    TranscriptReviewer: Callable
    ANNOTATION_VERSION: str
    REVIEW_VERSION: str
    TRANSLATION_REVIEW_VERSION: str
    review_document: Callable
    verify_document: Callable


@dataclass
class PreparationPlan:
    cached: StudyDocument | None = None
    agent_workflow: bool = False
    current_media: dict = field(default_factory=dict)
    resume_requested: bool = False
    saved_plan: dict | None = None
    evidence: Any = None
    decision: dict | None = None
    source_captions: bool = False
    legacy_supplement: bool = False
    role_error: Exception | None = None
    teach_enabled: bool = False
    annotation_profile: dict | None = None
    review_enabled: bool = False
    review_profile: dict | None = None
    transcript_spec: dict = field(default_factory=dict)
    annotation_spec: dict = field(default_factory=dict)
    review_spec: dict | None = None
    reusable: bool = False
    reusable_asr: bool = False
    adopted_source: list | None = None
    stages: list[str] = field(default_factory=list)


@dataclass
class PreparationContext:
    media_path: Path
    settings: AppSettings
    store: Store
    ports: PreparationPorts
    subtitle_path: Path | None = None
    start: float = 0
    duration: float | None = None
    progress: Callable | None = None
    cancelled: Callable | None = None
    force_asr: bool = False
    audio_permission: Callable | None = None
    allow_model_downloads: bool = False
    reuse_existing_transcript: bool = False
    language: LanguagePair | None = None
    plan: PreparationPlan = field(default_factory=PreparationPlan)
    finished: bool = False
    teaching_initialized: bool = False
    document: StudyDocument | None = None
    corrections: dict = field(default_factory=dict)
    same_annotation: bool = False
    completed: set[str] = field(default_factory=set)
    provided: dict = field(default_factory=dict)
    previous_responses: list = field(default_factory=list)
    previous_batches: list = field(default_factory=list)
    previous_checkpoints: list = field(default_factory=list)
    teaching_errors: dict = field(default_factory=dict)
    review: dict = field(default_factory=dict)
    pending: list = field(default_factory=list)
    agent: Any = None

    def report(self, message):
        code = getattr(message, "details", {}).get("stage_code")
        if code and self.document is not None:
            self.document.metadata["stage_code"] = code
        if self.progress:
            self.progress(message)

    def check_cancel(self):
        if self.cancelled and self.cancelled():
            raise PreparationCancelled("已取消；已完成的转写和学习材料已保存")


def _compatible_asr_cache(cached, spec):
    """Reuse only a matching, explicitly identified ASR run, never a legacy subtitle import."""
    if not cached or not cached.cues:
        return False
    previous = cached.metadata.get("transcript_spec", {})
    if previous.get("subtitle") or previous.get("asr_backend") not in ("local", "local_api", "external"):
        return False
    ignored = {"preparation_workflow", "subtitle_plan_identity", "force_asr"}
    return ({k: v for k, v in previous.items() if k not in ignored}
            == {k: v for k, v in spec.items() if k not in ignored})


def _evidence_progress(decision, plan):
    candidates = decision.get("candidates", [])
    names = {c["id"]: c.get("name", "") for c in candidates}
    notes = list(decision.get("notes", []))
    for c in candidates:
        if c.get("reason"):
            notes.append(f"{c.get('name', '')}：{c['reason']}")
    return Progress(decision["summary"], stage=plan[1], stage_code="transcript", plan=plan, decision=decision["summary"],
                    evidence_notes=list(dict.fromkeys(notes))[:16],
                    source_name=names.get(decision.get("source_id"), ""),
                    reference_name=names.get(decision.get("reference_id"), ""),
                    evidence_counts=[decision.get(k, 0) for k in ("source_count", "paired_count", "reference_count")])


def _needs_text_configuration(error):
    return error.category in ("credentials", "configuration") or error.status in (401, 402, 403, 404)



def review_document(document, settings, store, spec, report, check_cancel, cancelled, profile=None, *, ports):
    """Checkpoint the whole-film reviewer independently from lesson annotation."""
    metadata = document.metadata
    same = metadata.get("review_spec") == spec
    if same and metadata.get("review_stage") == "ready":
        report(Progress("复用已完成的文本复核", done=len(document.cues), total=len(document.cues), unit="句"))
        return
    raw = metadata.get("raw_display_text", {})
    original_cues = ports.normalize_cues(
        [replace(c, text=raw.get(c.id, c.original_text or c.text), tokens=[], translation="", explanation="",
                 segmented_texts=[])
         for c in document.cues], document.language, settings.target_script)
    completed = set(metadata.get("review_completed_ids", [])) if same else set()
    # Individually revised source cues stay authoritative even if the user switches the
    # whole-film reviewer. Do not overwrite a correction at an intermediate checkpoint.
    corrections = metadata.get("cue_corrections", {})
    if corrections:
        saved_lessons = {c.id: c for c in document.cues if c.id in corrections}
        original_cues = [replace(saved_lessons[c.id], text=corrections[c.id]["text"],
                                 segmented_texts=corrections[c.id]["segmented_texts"])
                         if c.id in corrections else c for c in original_cues]
        completed.update(c.id for c in original_cues if c.id in corrections)
    if not same:
        document.cues = original_cues
    responses = list(metadata.get("review_responses", [])) if same else []
    batches = list(metadata.get("review_batches", [])) if same else []
    records = dict(metadata.get("review_records", {})) if same else {}
    review_errors = dict(metadata.get("review_pending_errors", {})) if same else {}
    metadata.update(review_spec=spec, review_stage="running", stage="reviewing", stage_code="review",
                    review_pending_errors=review_errors,
                    review_completed_ids=sorted(completed), reviewed_count=len(completed),
                    review_records=records, review_responses=responses, review_batches=batches,
                    completed_ids=[], annotated_count=0, error="")
    store.save_document(document)
    reviewer = ports.TranscriptReviewer(provider=spec["provider"], model=spec["model"],
                                  **({"profile": profile} if profile else {}))

    reviewer.max_workers = settings.llm_workers
    reviewer.repair_feedback = review_errors
    reviewer.reference_hints = metadata.get("subtitle_reference_hints", {})

    def checkpoint(cues):
        # The scheduler publishes cumulative snapshots of immutable validated batches.
        # A save retry can contain only previously merged IDs and must still write.
        new_cues = [c for c in cues if c.id not in completed]
        update = {c.id: c for c in ports.normalize_cues(new_cues, document.language, settings.target_script)}
        document.cues = [update.get(c.id, c) for c in document.cues]
        completed.update(update)
        metadata.update(review_completed_ids=sorted(completed), reviewed_count=len(completed),
                        review_pending_errors=dict(getattr(reviewer, "pending_repair_errors", {})),
                        review_records={**records, **reviewer.review_metadata},
                        review_responses=responses + reviewer.response_metadata,
                        review_batches=batches + reviewer.batch_metadata,
                        reviewed_text={c.id: c.text for c in document.cues if c.id in completed},
                        reviewed_segmented_texts={c.id: c.segmented_texts for c in document.cues if c.id in completed})
        store.save_document(document)
        report(Progress(f"文本复核已保存 {len(completed)}/{len(document.cues)} 句",
                        done=len(completed), total=len(document.cues), unit="句"))

    try:
        check_cancel()
        pending = [c for c in document.cues if c.id not in completed]
        report(Progress(f"{spec['model']} 全片复核：{len(completed)}/{len(document.cues)} 句",
                        done=len(completed), total=len(document.cues), unit="句"))
        if pending:
            result = reviewer.review(pending, document.language, progress=report,
                                     cancelled=cancelled, checkpoint=checkpoint, context_cues=original_cues)
            checkpoint(result)
        check_cancel()
        metadata.update(review_stage="ready", stage="reviewed", review_finished_at=time.time())
        store.save_document(document)
    except Exception as exc:
        metadata.update(review_stage="partial", stage="partial", error=type(exc).__name__,
                        review_pending_errors=dict(getattr(reviewer, "pending_repair_errors", reviewer.repair_feedback)),
                        review_responses=responses + reviewer.response_metadata,
                        review_batches=batches + reviewer.batch_metadata)
        store.save_document(document)
        raise


def plan_preparation(ctx: PreparationContext):
    ctx.media_path = Path(ctx.media_path).resolve()
    if not ctx.media_path.is_file():
        raise FileNotFoundError(f"视频不存在：{ctx.media_path}")
    if ctx.start < 0 or (ctx.duration is not None and ctx.duration <= 0):
        raise ValueError("片段时间范围无效")
    ctx.language = ctx.settings.language

    ctx.check_cancel()
    if ctx.reuse_existing_transcript and (ctx.force_asr or ctx.subtitle_path or ctx.start or ctx.duration is not None):
        raise ValueError("补齐已有字幕的学习资料时不能更换字幕、音轨范围或重新识别。")
    ctx.plan.cached = ctx.store.load_document(str(ctx.media_path), ctx.language)
    ctx.plan.agent_workflow = ctx.settings.preparation_workflow == "subtitle_agent"
    ctx.plan.current_media = media_fingerprint(ctx.media_path)
    ctx.plan.resume_requested = bool(ctx.plan.cached and not ctx.force_asr and
                            (ctx.reuse_existing_transcript or ctx.plan.cached.metadata.get("reuse_transcript_once")))
    ctx.plan.saved_plan = ctx.plan.cached.metadata.get("subtitle_plan") if ctx.plan.resume_requested else None
    if ctx.plan.saved_plan:
        ctx.plan.agent_workflow = True
    if ctx.plan.agent_workflow and ctx.plan.cached and (ctx.plan.cached.metadata.get("media_fingerprint") != ctx.plan.current_media
                                      or Path(ctx.plan.cached.media_path).resolve() != ctx.media_path
                                      or ctx.plan.cached.language != ctx.language):
        ctx.plan.cached, ctx.plan.resume_requested, ctx.plan.saved_plan = None, False, None
    ctx.plan.evidence = None
    ctx.plan.decision = None
    if ctx.plan.agent_workflow:
        if ctx.plan.saved_plan:
            ctx.plan.decision = ctx.plan.saved_plan
        elif ctx.force_asr:
            ctx.plan.decision = {"decision": "asr", "identity": "forced-asr", "summary": "按请求重新识别所选音轨。"}
        else:
            from .subtitle_sources import plan_subtitle_sources
            ctx.report(Progress("检查本片的外挂字幕与内嵌字幕轨", stage="字幕评估", stage_code="planning", plan=["字幕评估"]))
            ctx.plan.evidence = plan_subtitle_sources(ctx.media_path, ctx.language, ctx.subtitle_path, cancelled=ctx.cancelled,
                                             audio_track=ctx.settings.audio_track)
            ctx.plan.decision = ctx.plan.evidence.metadata
        ctx.subtitle_path = None
    else:
        ctx.subtitle_path = (None if ctx.force_asr or ctx.reuse_existing_transcript else
                         Path(ctx.subtitle_path).resolve() if ctx.subtitle_path else ctx.ports.find_subtitle(ctx.media_path, ctx.language.target))
    ctx.plan.source_captions = bool(ctx.plan.decision and ctx.plan.decision["decision"] in ("source", "bilingual"))
    # An old generic subtitle import has no evidence for its language or episode.
    # Preserve an explicitly requested recorded Agent source, otherwise rediscover.
    ctx.plan.legacy_supplement = bool(ctx.plan.agent_workflow and not ctx.plan.saved_plan and ctx.plan.cached and ctx.reuse_existing_transcript)
    if ctx.plan.legacy_supplement:
        ctx.reuse_existing_transcript = False
    if (ctx.plan.saved_plan and not ctx.plan.source_captions and ctx.settings.review_enabled
            and ctx.plan.cached.metadata.get("review_stage") != "ready"):
        ctx.reuse_existing_transcript = False


def resolve_models(ctx: PreparationContext):
    registry = ctx.store.model_registry
    ctx.plan.role_error = None
    try:
        ctx.plan.teach_enabled, ctx.plan.annotation_profile = registry.text_role(ctx.settings, "annotation")
    except ProviderError as exc:
        if not ctx.plan.agent_workflow:
            raise
        ctx.plan.teach_enabled, ctx.plan.annotation_profile, ctx.plan.role_error = False, None, exc
    if ctx.reuse_existing_transcript and not ctx.plan.teach_enabled and not ctx.plan.agent_workflow:
        raise ProviderError("请在工作区启用可用的讲解模型，再补齐讲解与译文。")
    try:
        ctx.plan.review_enabled, ctx.plan.review_profile = (registry.text_role(ctx.settings, "review")
                                          if ctx.settings.review_enabled and not ctx.reuse_existing_transcript
                                          and not ctx.plan.source_captions and (ctx.plan.teach_enabled or not ctx.plan.agent_workflow)
                                          else (False, None))
    except ProviderError as exc:
        if not ctx.plan.agent_workflow:
            raise
        ctx.plan.review_enabled, ctx.plan.review_profile, ctx.plan.role_error = False, None, exc

    def profile_identity(profile):
        public = {k: profile.get(k) for k in ("id", "provider", "protocol", "model", "base_url",
                                             "reasoning_effort", "max_tokens")}
        return hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()[:24]
    ctx.plan.transcript_spec = {
        "audio_track": ctx.settings.audio_track, "asr_model": ctx.settings.asr_model,
        "target": ctx.language.target, "start": ctx.start, "duration": ctx.duration,
        "subtitle": str(ctx.subtitle_path) if ctx.subtitle_path else None,
        "subtitle_fingerprint": media_fingerprint(ctx.subtitle_path) if ctx.subtitle_path else None,
    }
    if ctx.force_asr:
        ctx.plan.transcript_spec["force_asr"] = True
    if not ctx.subtitle_path and not ctx.plan.source_captions and not ctx.reuse_existing_transcript:
        if ctx.settings.asr_backend not in ("local", "local_api", "external"):
            raise ValueError("未知的语音识别方式。")
        ctx.plan.transcript_spec.update(asr_backend=ctx.settings.asr_backend, suppress_bgm=ctx.settings.suppress_bgm)
        if ctx.settings.asr_backend in ("external", "local_api"):
            from .external_asr import api_options
            endpoint, model, fmt, _ = api_options(ctx.settings)
            ctx.plan.transcript_spec.update(asr_model=model, endpoint=endpoint, response_format=fmt)
        else:
            ctx.plan.transcript_spec["speaker_turns"] = ctx.settings.speaker_turns
    if ctx.plan.decision:
        if ctx.plan.source_captions:
            ctx.plan.transcript_spec.pop("asr_model")
        ctx.plan.transcript_spec.update(preparation_workflow="subtitle_agent", subtitle_plan_identity=ctx.plan.decision["identity"])
    ctx.plan.annotation_spec = {
        "provider": ctx.settings.provider if ctx.plan.teach_enabled else "local",
        "model": ctx.settings.model if ctx.plan.teach_enabled else "",
        "language": asdict(ctx.language),
        "target_script": ctx.settings.target_script,
        "text_normalization": "hk-cantonese-v1",
        "teaching_version": ctx.ports.ANNOTATION_VERSION,
    }
    if ctx.plan.annotation_profile:
        ctx.plan.annotation_spec.update(provider=ctx.plan.annotation_profile["provider"], model=ctx.plan.annotation_profile["model"],
                               profile=profile_identity(ctx.plan.annotation_profile))
    if ctx.settings.teaching_preferences:
        if (not isinstance(ctx.settings.teaching_preferences, str) or len(ctx.settings.teaching_preferences) > 6000
                or "\x00" in ctx.settings.teaching_preferences):
            raise ValueError("讲解偏好最多 6000 字符")
        ctx.plan.annotation_spec["teaching_preferences"] = ctx.settings.teaching_preferences
    ctx.plan.review_spec = ({"provider": ctx.settings.review_provider, "model": ctx.settings.review_model,
                    "version": ctx.ports.REVIEW_VERSION, "language": asdict(ctx.language),
                    "target_script": ctx.settings.target_script}
                   if ctx.plan.review_enabled else None)
    if ctx.plan.review_spec and ctx.plan.review_profile:
        ctx.plan.review_spec.update(provider=ctx.plan.review_profile["provider"], model=ctx.plan.review_profile["model"],
                           profile=profile_identity(ctx.plan.review_profile))
    ctx.plan.annotation_spec["review"] = ctx.plan.review_spec
    if ctx.reuse_existing_transcript and (not ctx.plan.cached or not ctx.plan.cached.cues):
        raise ProviderError("此视频没有可复用的字幕，请先生成或导入字幕。")
    if ctx.plan.resume_requested and (not ctx.plan.agent_workflow or ctx.plan.saved_plan):
        ctx.plan.transcript_spec = ctx.plan.cached.metadata.get("transcript_spec", ctx.plan.transcript_spec)
        if ctx.plan.cached.metadata.get("review_stage") == "ready" and ctx.plan.cached.metadata.get("review_spec"):
            ctx.plan.review_spec = ctx.plan.cached.metadata.get("review_spec", ctx.plan.review_spec)
            ctx.plan.annotation_spec["review"] = ctx.plan.review_spec
    # Old workspaces keep their existing transcript when reopened or resumed.
    # Only an explicit full regeneration opts that material into the new audio stages.
    if ctx.plan.cached and not ctx.plan.agent_workflow and not ctx.subtitle_path and not ctx.force_asr and ctx.settings.asr_backend == "local":
        old_spec = ctx.plan.cached.metadata.get("transcript_spec", {})
        if "asr_backend" not in old_spec and all(ctx.plan.transcript_spec.get(k) == v for k, v in old_spec.items()) and old_spec:
            ctx.plan.transcript_spec = old_spec
    ctx.plan.reusable = ctx.plan.cached is not None and (ctx.reuse_existing_transcript or ctx.plan.cached.metadata.get("transcript_spec") == ctx.plan.transcript_spec)
    ctx.plan.reusable_asr = ctx.plan.agent_workflow and not ctx.force_asr and not ctx.plan.source_captions and _compatible_asr_cache(ctx.plan.cached, ctx.plan.transcript_spec)
    if ctx.plan.agent_workflow:
        ctx.plan.annotation_spec["subtitle_plan_identity"] = ctx.plan.decision["identity"]
    ctx.plan.stages = (["字幕评估", "读取原字幕" if ctx.plan.source_captions else "字幕识别"] if ctx.plan.agent_workflow else ["字幕识别"])
    if ctx.plan.review_spec and not ctx.reuse_existing_transcript:
        ctx.plan.stages.append("文本复核")
    if ctx.plan.agent_workflow:
        ctx.plan.stages.append("逐句讲解")
        if ctx.plan.evidence and ctx.plan.source_captions:
            needs_translation = any(not c.translation.strip() for c in ctx.plan.evidence.source_cues)
        elif ctx.plan.saved_plan and ctx.plan.source_captions:
            needs_translation = any(c.id not in ctx.plan.cached.metadata.get("provided_translations", {}) for c in ctx.plan.cached.cues)
        else:
            needs_translation = True
        if needs_translation:
            ctx.plan.stages.append("译稿核验")
    else:
        ctx.plan.stages += ["逐句讲解", "译稿核验"] if ctx.plan.teach_enabled else ["本地分词"]
    ctx.plan.stages.append("保存字幕")
    if ctx.plan.decision:
        ctx.report(_evidence_progress(ctx.plan.decision, ctx.plan.stages))
    else:
        ctx.report(Progress("检查识别与字幕缓存", stage=ctx.plan.stages[0], stage_code="transcript", plan=ctx.plan.stages))
    ctx.plan.adopted_source = None
    if ctx.plan.legacy_supplement and not ctx.plan.reusable_asr:
        matching_source = ctx.plan.source_captions and [(c.start, c.end, c.original_text) for c in ctx.plan.cached.cues] == [
            (c.start, c.end, c.original_text) for c in ctx.plan.evidence.source_cues]
        if not matching_source:
            raise ProviderError("旧字幕缺少可核实的音轨或原文来源。请从准备队列启动字幕 Agent，重新判断字幕和识别音轨。")
        # Library links and notes refer to the old IDs. Only adopt evidence after
        # checking the entire sequence, text and timing, preserving those links.
        ctx.plan.adopted_source = [replace(c, id=old.id) for c, old in zip(ctx.plan.evidence.source_cues, ctx.plan.cached.cues)]


def acquire_transcript(ctx: PreparationContext):
    if ctx.plan.reusable:
        ctx.document = ctx.plan.cached
        ctx.report(f"读取已保存转写：{len(ctx.document.cues)} 句")
        if (ctx.plan.agent_workflow and not ctx.plan.teach_enabled and ctx.document.metadata.get("stage") == "ready"
                and all(translation_available(c, ctx.document.metadata.get("translation_review", {})) for c in ctx.document.cues)):
            ctx.store.ensure_subtitle_files(ctx.document)
            ctx.report("已复用保存的逐句讲解与双语字幕")
            ctx.finished = True
            return
        if ctx.document.metadata.get("annotation_spec") == ctx.plan.annotation_spec and ctx.document.metadata.get("stage") == "ready":
            ctx.review = ctx.document.metadata.get("translation_review", {})
            if ctx.plan.teach_enabled and all(translation_available(c, ctx.review) for c in ctx.document.cues):
                ctx.store.ensure_subtitle_files(ctx.document)
                ctx.report("原字幕译文与逐句讲解已就绪" if ctx.review.get("provided") else "逐句译文核验和讲解已就绪")
                ctx.finished = True
                return
            if not ctx.plan.teach_enabled and ctx.document.metadata.get("lesson_status") == "local_only":
                ctx.report("本地字幕已就绪，尚未生成模型讲解与译文")
                ctx.finished = True
                return
    else:
        asr_metadata = {}
        if ctx.plan.source_captions:
            limit = ctx.start + ctx.duration if ctx.duration is not None else float("inf")
            cues = [c for c in (ctx.plan.adopted_source or ctx.plan.evidence.source_cues) if c.end > ctx.start and c.start < limit]
        elif ctx.subtitle_path:
            ctx.report(f"读取字幕：{ctx.subtitle_path.name}")
            cues = ctx.ports.load_subtitles(ctx.subtitle_path)
            if ctx.start or ctx.duration is not None:
                limit = ctx.start + ctx.duration if ctx.duration is not None else float("inf")
                cues = [c for c in cues if c.end > ctx.start and c.start < limit]
        elif ctx.plan.reusable_asr:
            ctx.report("复用同一视频、音轨和时间范围内的原始识别")
            raw = ctx.plan.cached.metadata.get("raw_display_text", {})
            cues = [replace(c, text=raw.get(c.id, c.original_text), explanation="", translation="", tokens=[],
                            segmented_texts=[]) for c in ctx.plan.cached.cues]
            # Keep recognizer provenance, without carrying forward old teaching or review state.
            asr_metadata = {k: v for k, v in ctx.plan.cached.metadata.items()
                            if k.startswith(("asr_", "raw_asr", "alignment_", "speaker_"))
                            or k in ("bgm_method", "audio_chunks")}
        elif ctx.settings.asr_backend in ("local_api", "external"):
            from .external_asr import transcribe_external
            cues = transcribe_external(ctx.media_path, ctx.settings, ctx.store.data_dir, start=ctx.start, duration=ctx.duration,
                                       progress=ctx.report, cancelled=ctx.cancelled, permission=ctx.audio_permission,
                                       metadata_out=asr_metadata)
        else:
            ctx.report("本地语音识别")
            model = ctx.settings.asr_model
            if model.startswith("local:"):
                from .local_whisper import resolve_local_model
                model = str(resolve_local_model(model))
            cues = ctx.ports.transcribe(ctx.media_path, language=ctx.language.target, model=model,
                              device=ctx.settings.device, audio_track=ctx.settings.audio_track, start=ctx.start,
                              duration=ctx.duration, progress=ctx.report, cancelled=ctx.cancelled,
                              metadata_out=asr_metadata, suppress_bgm=ctx.settings.suppress_bgm,
                              speaker_turns=ctx.settings.speaker_turns, audit_dir=ctx.store.data_dir / "asr-audit",
                              allow_model_downloads=ctx.allow_model_downloads)
        if not cues:
            raise ValueError("此片段没有识别到字幕。请检查音轨、时间范围，或导入现有字幕。")
        ctx.document = StudyDocument(str(ctx.media_path), ctx.language, cues, metadata={
            "stage": "transcribed", "stage_code": "transcript", "transcript_spec": ctx.plan.transcript_spec,
            "media_fingerprint": ctx.plan.current_media,
            "created_at": time.time(), "completed_ids": [],
            "raw_display_text": {c.id: c.text for c in cues},
            **asr_metadata,
        })
        if ctx.plan.decision:
            from .subtitle_sources import align_reference_hints
            references = ctx.plan.evidence.reference_cues if ctx.plan.evidence else []
            ctx.provided = {c.id: c.translation for c in cues if ctx.plan.source_captions and c.translation.strip()}
            if ctx.plan.legacy_supplement:
                # The caller asked to fill existing lessons. Keep validated fields and
                # hand revisions now that we have established the raw source identity.
                ctx.document = ctx.plan.cached
                corrected = ctx.document.metadata.get("cue_corrections", {})
                adopted = []
                for c in ctx.document.cues:
                    if c.id in corrected or c.text != learning_text(c.original_text, ctx.language, ctx.settings.target_script):
                        ctx.provided.pop(c.id, None)
                    if c.id in ctx.provided:
                        matches = c.translation == ctx.provided[c.id] and len(c.segmented_texts) <= 1
                        c = replace(c, translation=ctx.provided[c.id], segmented_texts=[],
                                    explanation=c.explanation if matches else "", tokens=c.tokens if matches else [])
                    adopted.append(c)
                ctx.document.cues = adopted
                ctx.document.metadata["transcript_spec"] = ctx.plan.transcript_spec
                ctx.reuse_existing_transcript = ctx.plan.source_captions or ctx.document.metadata.get("review_stage") == "ready"
            hints = {}
            if references and not ctx.plan.source_captions:
                end = min(ctx.start + ctx.duration if ctx.duration is not None else float("inf"),
                          ctx.plan.decision.get("duration") or float("inf"))
                in_range = [c for c in cues if ctx.start <= c.start < c.end <= end]
                hints = align_reference_hints(in_range, references)
            ctx.document.metadata.update(
                subtitle_plan={**ctx.plan.decision, "prepared_count": len(cues), "provided_count": len(ctx.provided),
                               "reference_hint_count": len(hints)},
                provided_translations=ctx.provided,
                subtitle_reference_cues=[{"id": c.id, "start": c.start, "end": c.end, "text": c.text}
                                         for c in references],
                subtitle_reference_hints=hints,
            )
        # Save immediately, before any optional model call can fail.
        ctx.store.save_document(ctx.document)


def run_review_stage(ctx: PreparationContext):
    ctx.check_cancel()
    if ctx.plan.agent_workflow and (not ctx.plan.teach_enabled or ctx.plan.role_error):
        ctx.document.metadata.update(stage="partial", lesson_status="needs_teaching", error="TeachingUnavailable")
        ctx.store.save_document(ctx.document)
        if ctx.plan.role_error:
            raise ProviderError(f"字幕已保存。请检查工作区文本模型设置并重试。{ctx.plan.role_error}",
                                category="configuration") from ctx.plan.role_error
        raise ProviderError("原字幕已保存。请在工作区启用逐句讲解模型，允许其使用本机或指定的远程文本服务后重试。")
    if ctx.plan.review_spec and not ctx.reuse_existing_transcript:
        ctx.report(Progress("开始文本复核", stage="文本复核", stage_code="review"))
        try:
            ctx.ports.review_document(ctx.document, ctx.settings, ctx.store, ctx.plan.review_spec, ctx.report, ctx.check_cancel, ctx.cancelled, ctx.plan.review_profile)
        except ProviderError as exc:
            if ctx.plan.agent_workflow and _needs_text_configuration(exc):
                ctx.document.metadata.update(stage="partial", lesson_status="needs_teaching", error=type(exc).__name__)
                ctx.store.save_document(ctx.document)
            raise


def run_teaching_stage(ctx: PreparationContext):
    ctx.corrections = ctx.document.metadata.get("cue_corrections", {})
    ctx.same_annotation = ctx.document.metadata.get("annotation_spec") == ctx.plan.annotation_spec
    ctx.completed = set(ctx.document.metadata.get("completed_ids", [])) if ctx.same_annotation else set()
    if ctx.plan.teach_enabled:
        ctx.completed.intersection_update(c.id for c in ctx.document.cues if c.translation.strip() and c.explanation.strip())
    if ctx.reuse_existing_transcript:
        ctx.completed.update(c.id for c in ctx.document.cues if c.translation.strip() and c.explanation.strip())
    if not ctx.same_annotation and not ctx.reuse_existing_transcript:
        raw_display = ctx.document.metadata.get("reviewed_text" if ctx.plan.review_spec else "raw_display_text", {})
        reviewed_parts = ctx.document.metadata.get("reviewed_segmented_texts", {})
        ctx.document.cues = [c if c.id in ctx.corrections else replace(c, text=raw_display.get(c.id, c.original_text or c.text),
                                 translation="", explanation="", tokens=[],
                                 segmented_texts=reviewed_parts.get(c.id, c.segmented_texts) if ctx.plan.review_spec else [])
                         for c in ctx.document.cues]
    if ctx.corrections:
        ctx.completed.update(c.id for c in ctx.document.cues if c.id in ctx.corrections
                         and c.translation.strip() and c.explanation.strip())
        ctx.document.cues = [replace(c, text=ctx.corrections[c.id]["text"],
                                 segmented_texts=ctx.corrections[c.id]["segmented_texts"])
                         if c.id in ctx.corrections else c for c in ctx.document.cues]
    ctx.provided = ctx.document.metadata.get("provided_translations", {}) if ctx.plan.agent_workflow else {}
    if ctx.provided:
        ctx.document.cues = [replace(c, translation=ctx.provided[c.id]) if c.id in ctx.provided else c for c in ctx.document.cues]
    if not ctx.reuse_existing_transcript:
        ctx.document.cues = ctx.ports.normalize_cues(ctx.document.cues, ctx.language, ctx.settings.target_script)
    ctx.previous_responses = list(ctx.document.metadata.get("model_responses", [])) if ctx.same_annotation or ctx.reuse_existing_transcript else []
    ctx.previous_batches = list(ctx.document.metadata.get("analysis_batches", [])) if ctx.same_annotation or ctx.reuse_existing_transcript else []
    ctx.previous_checkpoints = list(ctx.document.metadata.get("checkpoint_events", [])) if ctx.same_annotation or ctx.reuse_existing_transcript else []
    ctx.teaching_errors = dict(ctx.document.metadata.get("teaching_pending_errors", {})) if ctx.same_annotation else {}
    ctx.review = ctx.document.metadata.get("translation_review", {})
    verification_model = {k: ctx.plan.annotation_spec[k] for k in ("provider", "model", "profile") if k in ctx.plan.annotation_spec}
    reuse_checks = (ctx.same_annotation or ctx.reuse_existing_transcript
                    and all(ctx.review.get(k) == v for k, v in verification_model.items())
                    and ctx.review.get("profile") == verification_model.get("profile"))
    if not reuse_checks or ctx.review.get("version") != ctx.ports.TRANSLATION_REVIEW_VERSION:
        ctx.review = {"version": ctx.ports.TRANSLATION_REVIEW_VERSION, **verification_model, "verified": {}, "segments": {}, "responses": []}
        ctx.document.metadata["translation_review"] = ctx.review
    if ctx.provided:
        ctx.review["provided"] = {c.id: lesson_digest(c, []) for c in ctx.document.cues
                              if c.id in ctx.provided and c.id in ctx.completed and c.explanation.strip()
                              and c.translation == ctx.provided[c.id] and len(c.segmented_texts) <= 1}
    ctx.review["status"] = "pending" if ctx.plan.teach_enabled else "disabled"
    ctx.document.metadata.update(annotation_spec=ctx.plan.annotation_spec, stage="annotating",
                             teaching_pending_errors=ctx.teaching_errors,
                             lesson_status="pending" if ctx.plan.teach_enabled else "local_only",
                             completed_ids=sorted(ctx.completed), annotated_count=len(ctx.completed),
                             model_responses=ctx.previous_responses, analysis_batches=ctx.previous_batches,
                             checkpoint_events=ctx.previous_checkpoints, error="")
    ctx.document.provider = ctx.plan.annotation_spec["provider"]
    ctx.document.model = ctx.plan.annotation_spec["model"]
    ctx.pending = [c for c in ctx.document.cues if c.id not in ctx.completed]
    ctx.report(Progress("准备逐句学习资料", stage="逐句讲解" if ctx.plan.teach_enabled else "本地分词",
                    stage_code="teaching" if ctx.plan.teach_enabled else "local",
                    done=len(ctx.completed), total=len(ctx.document.cues), unit="句"))
    ctx.agent = None
    ctx.store.save_document(ctx.document)

    def checkpoint(cues):
        cues = [c for c in cues if c.id not in ctx.completed]
        if not ctx.reuse_existing_transcript:
            cues = ctx.ports.normalize_cues(cues, ctx.language, ctx.settings.target_script)
        update = {c.id: c for c in cues}
        for c in cues:
            if c.id in ctx.provided:
                if c.translation != ctx.provided[c.id] or not c.explanation.strip() or len(c.segmented_texts) > 1:
                    raise AnnotationValidationError("已有译文或原始分段发生变化，请重新检查此句。")
                ctx.review["provided"][c.id] = lesson_digest(c, [])
        ctx.document.cues = [update.get(c.id, c) for c in ctx.document.cues]
        ctx.completed.update(update)
        ctx.document.metadata["completed_ids"] = sorted(ctx.completed)
        ctx.document.metadata["annotated_count"] = len(ctx.completed)
        if ctx.agent is not None:
            ctx.document.metadata["teaching_pending_errors"] = dict(getattr(ctx.agent, "pending_repair_errors", {}))
            ctx.document.metadata["model_responses"] = ctx.previous_responses + getattr(ctx.agent, "response_metadata", [])
            ctx.document.metadata["analysis_batches"] = ctx.previous_batches + getattr(ctx.agent, "batch_metadata", [])
            state = getattr(ctx.agent, "checkpoint_metadata", None)
            ctx.document.metadata["checkpoint_events"] = ctx.previous_checkpoints + ([dict(state)] if isinstance(state, dict) else [])
        ctx.store.save_document(ctx.document)

        ctx.report(Progress(f"学习资料已保存 {len(ctx.completed)}/{len(ctx.document.cues)} 句",
                        done=len(ctx.completed), total=len(ctx.document.cues), unit="句"))
    ctx.teaching_initialized = True
    if ctx.document.provider == "local":
        ctx.report("本地分词与读音")
        checkpoint(ctx.ports.annotate_local(ctx.pending, ctx.language))
    else:
        ctx.agent = ctx.ports.LearningAgent(provider=ctx.document.provider, model=ctx.document.model or None,
                              **({"profile": ctx.plan.annotation_profile} if ctx.plan.annotation_profile else {}))
        ctx.agent.max_workers = ctx.settings.llm_workers
        ctx.agent.split_invalid_batches = True
        ctx.agent.repair_feedback = ctx.teaching_errors
        ctx.agent.preserve_text = bool(ctx.plan.agent_workflow or ctx.reuse_existing_transcript or ctx.plan.review_spec or ctx.corrections)
        ctx.agent.teaching_preferences = ctx.settings.teaching_preferences
        ctx.agent.provided_translations = ctx.provided
        ctx.agent.reference_hints = ctx.document.metadata.get("subtitle_reference_hints", {}) if ctx.plan.agent_workflow else {}
        ctx.agent.unreviewed_source = bool(ctx.plan.agent_workflow and ctx.document.metadata.get("review_stage") != "ready")
        if ctx.pending:
            ctx.report(f"{ctx.document.provider} 分析：{len(ctx.pending)} 句")
            result = ctx.agent.annotate(ctx.pending, ctx.language, progress=ctx.report, cancelled=ctx.cancelled,
                                    checkpoint=checkpoint, context_cues=ctx.document.cues)
            checkpoint(result)
        if not all(c.translation.strip() and c.explanation.strip() for c in ctx.document.cues):
            raise ValueError("仍有字幕缺少逐句译文或讲解。")


def run_verification_stage(ctx: PreparationContext):
    if ctx.document.provider == "local":
        return
    if len(ctx.provided) != len(ctx.document.cues):
        ctx.report(Progress("开始核验新生成的译稿", stage="译稿核验", stage_code="verification", done=len(ctx.review["verified"]),
                        total=len(ctx.document.cues) - len(ctx.provided), unit="句"))
        ctx.ports.verify_document(ctx.document, ctx.agent, ctx.store, ctx.report, ctx.check_cancel)
    elif all(translation_available(c, ctx.review) for c in ctx.document.cues):
        ctx.review["status"] = "ready"
    else:
        raise AnnotationValidationError("已有译文与逐句讲解尚未配对完成。")
    ctx.document.metadata["lesson_status"] = ("provided" if len(ctx.provided) == len(ctx.document.cues) else
                                          "mixed" if ctx.provided else "verified")


def publish_document(ctx: PreparationContext):
    ctx.check_cancel()
    ctx.document.metadata.update(stage="ready", finished_at=time.time(), error="")
    ctx.document.metadata.pop("reuse_transcript_once", None)
    ctx.report(Progress("正在保存字幕与学习资料", stage="保存字幕", stage_code="publish"))
    ctx.store.save_document(ctx.document)
    ctx.report(f"已准备 {len(ctx.document.cues)} 句" if ctx.plan.teach_enabled else "本地字幕已就绪，尚未生成模型讲解与译文")
    return ctx.document


def checkpoint_failure(ctx: PreparationContext, exc: Exception):
    if ctx.review["status"] == "running":
        ctx.review["status"] = "partial"
    ctx.document.metadata["stage"] = "partial"
    if ctx.agent is not None:
        ctx.document.metadata["teaching_pending_errors"] = dict(getattr(ctx.agent, "pending_repair_errors", ctx.teaching_errors))
        ctx.document.metadata["model_responses"] = ctx.previous_responses + getattr(ctx.agent, "response_metadata", [])
        ctx.document.metadata["analysis_batches"] = ctx.previous_batches + getattr(ctx.agent, "batch_metadata", [])
        state = getattr(ctx.agent, "checkpoint_metadata", None)
        ctx.document.metadata["checkpoint_events"] = ctx.previous_checkpoints + ([dict(state)] if isinstance(state, dict) else [])
    # Error class only: an upstream exception could contain provider request details.
    ctx.document.metadata["error"] = type(exc).__name__
    if ctx.plan.agent_workflow and isinstance(exc, ProviderError) and _needs_text_configuration(exc):
        ctx.document.metadata["lesson_status"] = "needs_teaching"
    ctx.store.save_document(ctx.document)
