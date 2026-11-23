"""Resumable local transcription → validated language material preparation."""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from threading import Event
from dataclasses import asdict, replace
from pathlib import Path
import time
import hashlib
import json

from .agent import (ANNOTATION_VERSION, AnnotationValidationError, LearningAgent, PartialBatchResult,
                    annotate_local, batch_worker, splittable_batch_error)
from .media import load_subtitles, transcribe
from .language import learning_text, normalize_cues
from .models import AppSettings, StudyDocument
from .review import REVIEW_VERSION, TranscriptReviewer
from .storage import Store, media_fingerprint
from .task_progress import Progress, batch_progress
from .translation_review import TRANSLATION_REVIEW_VERSION, lesson_digest, translation_available


class PreparationCancelled(RuntimeError):
    pass


def find_subtitle(media_path: Path, target_language: str) -> Path | None:
    for stem in (f"{media_path.stem}.{target_language}", media_path.stem):
        for extension in (".srt", ".ass", ".vtt", ".ssa"):
            candidate = media_path.with_name(stem + extension)
            if candidate.is_file():
                return candidate
    return None


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
    return Progress(decision["summary"], stage=plan[1], plan=plan, decision=decision["summary"],
                    evidence_notes=list(dict.fromkeys(notes))[:16],
                    source_name=names.get(decision.get("source_id"), ""),
                    reference_name=names.get(decision.get("reference_id"), ""),
                    evidence_counts=[decision.get(k, 0) for k in ("source_count", "paired_count", "reference_count")])


def _review_document(document, settings, store, spec, report, check_cancel, cancelled, profile=None):
    """Checkpoint the whole-film reviewer independently from lesson annotation."""
    metadata = document.metadata
    same = metadata.get("review_spec") == spec
    if same and metadata.get("review_stage") == "ready":
        report(Progress("复用已完成的文本复核", done=len(document.cues), total=len(document.cues), unit="句"))
        return
    raw = metadata.get("raw_display_text", {})
    original_cues = normalize_cues(
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
    metadata.update(review_spec=spec, review_stage="running", stage="reviewing",
                    review_pending_errors=review_errors,
                    review_completed_ids=sorted(completed), reviewed_count=len(completed),
                    review_records=records, review_responses=responses, review_batches=batches,
                    completed_ids=[], annotated_count=0, error="")
    store.save_document(document)
    reviewer = TranscriptReviewer(provider=spec["provider"], model=spec["model"],
                                  **({"profile": profile} if profile else {}))

    reviewer.max_workers = settings.llm_workers
    reviewer.repair_feedback = review_errors
    reviewer.reference_hints = metadata.get("subtitle_reference_hints", {})

    def checkpoint(cues):
        # The scheduler publishes cumulative snapshots of immutable validated batches.
        # A save retry can contain only previously merged IDs and must still write.
        new_cues = [c for c in cues if c.id not in completed]
        update = {c.id: c for c in normalize_cues(new_cues, document.language, settings.target_script)}
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


def _verify_document(document, agent, store, report, check_cancel):
    review = document.metadata["translation_review"]
    total = len(document.cues) - len(review.get("provided", {}))
    review["status"] = "running"
    document.metadata["stage"] = "verifying"
    store.save_document(document)
    positions = {c.id: i for i, c in enumerate(document.cues)}
    pending_errors = review.setdefault("pending_errors", {})
    jobs = deque()
    for start in range(0, len(document.cues), 24):
        batch = document.cues[start:start + 24]
        pending = [c for c in batch if not translation_available(c, review)]
        if pending:
            jobs.append((pending, 0))

    stop = Event()
    template = batch_worker(agent)

    def verify(pending, feedback):
        worker = batch_worker(template)
        worker.allow_partial_translations = True
        worker.repair_feedback = feedback
        neighbors = {i for cue in pending for i in range(max(0, positions[cue.id] - 3),
                     min(len(document.cues), positions[cue.id] + 4))}
        context = [document.cues[i].text for i in sorted(neighbors)]
        try:
            result = worker.verify_translations(pending, document.language, context)
        except Exception as exc:
            if splittable_batch_error(exc):
                return None, worker.last_response_metadata, str(exc)
            stop.set()
            raise
        return result, worker.last_response_metadata, ""

    error = None
    sequence = 0
    with ThreadPoolExecutor(max_workers=template.max_workers, thread_name_prefix="tingmu-translation") as pool:
        active = {}
        while active or (jobs and not stop.is_set()):
            try:
                check_cancel()
                while jobs and len(active) < template.max_workers and not stop.is_set():
                    pending, attempt = jobs.popleft()
                    check_cancel()
                    if not stop.is_set():
                        feedback = {c.id: pending_errors[c.id] for c in pending if c.id in pending_errors}
                        sequence += 1
                        event = batch_progress(sequence, positions[pending[0].id]+1, positions[pending[-1].id]+1,
                                               len(pending), repair=bool(feedback))
                        report(Progress(f"逐句译文核验 · {event}", **event.details))
                        active[pool.submit(verify, pending, feedback)] = (pending, attempt, sequence)
            except Exception as exc:
                stop.set()
                error = error or exc
            if not active:
                break
            done, _ = wait(active, timeout=0.1, return_when=FIRST_COMPLETED)
            for future in done:
                pending, attempt, index = active.pop(future)
                first_number, last_number = positions[pending[0].id]+1, positions[pending[-1].id]+1
                repair = any(c.id in pending_errors for c in pending)
                try:
                    result, metadata, batch_error = future.result()
                    failures = {}
                    if batch_error:
                        failures = {c.id: batch_error for c in pending}
                        result = []
                    elif isinstance(result, PartialBatchResult):
                        failures = result.errors
                        result = result.completed
                    pending_errors.update(failures)
                    for cue, segments in result:
                        pending_errors.pop(cue.id, None)
                        document.cues[positions[cue.id]] = cue
                        if segments:
                            review["segments"][cue.id] = segments
                        else:
                            review["segments"].pop(cue.id, None)
                        review["verified"][cue.id] = lesson_digest(cue, segments)
                    if metadata or result:
                        review["responses"].append(dict(metadata, cue_start_index=positions[pending[0].id],
                            requested_count=len(pending), verified_count=len(result), repair_attempt=attempt))
                        review["responses"].sort(key=lambda row: row.get("cue_start_index", -1))
                    store.save_document(document)
                    batch = batch_progress(index, first_number, last_number, len(pending), done=len(result),
                                           status="partial" if failures else "completed", repair=repair)
                    report(Progress(f"译稿已核验 {len(review['verified'])}/{total} 句",
                                    **batch.details, done=len(review["verified"]), total=total, unit="句"))
                    invalid = [c for c in pending if c.id in failures]
                    if batch_error and len(invalid) > 1:
                        middle = len(invalid) // 2
                        jobs.appendleft((invalid[middle:], 0))
                        jobs.appendleft((invalid[:middle], 0))
                    elif invalid and attempt < 2:
                        jobs.appendleft((invalid, attempt + 1))
                    elif invalid:
                        first = invalid[0]
                        exc = AnnotationValidationError(failures[first.id])
                        exc.cue_number = positions[first.id] + 1
                        exc.cue_start = first.start
                        raise exc
                    if invalid:
                        first = invalid[0]
                        report(f"译文核验：第 {positions[first.id] + 1} 句 · {failures[first.id]} "
                               f"仅重试 {len(invalid)} 句。")
                except Exception as exc:
                    report(batch_progress(index, first_number, last_number, len(pending), status="failed", repair=repair,
                                          done=sum(c.id in review["verified"] for c in pending)))
                    stop.set()
                    error = error or exc
    if error is not None:
        raise error
    check_cancel()
    if not all(translation_available(c, review) for c in document.cues):
        raise ValueError("仍有译文未通过核验。")
    review["status"] = "ready"


def prepare_media(media_path: Path, settings: AppSettings, store: Store,
                  subtitle_path: Path | None = None, start: float = 0,
                  duration: float | None = None, progress=None, cancelled=None,
                  force_asr: bool = False, audio_permission=None, allow_model_downloads: bool = False,
                  reuse_existing_transcript: bool = False) -> StudyDocument:
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
    if reuse_existing_transcript and (force_asr or subtitle_path or start or duration is not None):
        raise ValueError("补齐已有字幕的学习资料时不能更换字幕、音轨范围或重新识别。")
    cached = store.load_document(str(media_path), language)
    agent_workflow = settings.preparation_workflow == "subtitle_agent"
    current_media = media_fingerprint(media_path)
    resume_requested = bool(cached and not force_asr and
                            (reuse_existing_transcript or cached.metadata.get("reuse_transcript_once")))
    saved_plan = cached.metadata.get("subtitle_plan") if resume_requested else None
    if saved_plan:
        agent_workflow = True
    if agent_workflow and cached and (cached.metadata.get("media_fingerprint") != current_media
                                      or Path(cached.media_path).resolve() != media_path
                                      or cached.language != language):
        cached, resume_requested, saved_plan = None, False, None
    evidence = None
    decision = None
    if agent_workflow:
        if saved_plan:
            decision = saved_plan
        elif force_asr:
            decision = {"decision": "asr", "identity": "forced-asr", "summary": "按请求重新识别所选音轨。"}
        else:
            from .subtitle_sources import plan_subtitle_sources
            report(Progress("检查本片的外挂字幕与内嵌字幕轨", stage="字幕评估", plan=["字幕评估"]))
            evidence = plan_subtitle_sources(media_path, language, subtitle_path, cancelled=cancelled,
                                             audio_track=settings.audio_track)
            decision = evidence.metadata
        subtitle_path = None
    else:
        subtitle_path = (None if force_asr or reuse_existing_transcript else
                         Path(subtitle_path).resolve() if subtitle_path else find_subtitle(media_path, language.target))
    source_captions = bool(decision and decision["decision"] in ("source", "bilingual"))
    # An old generic subtitle import has no evidence for its language or episode.
    # Preserve an explicitly requested recorded Agent source, otherwise rediscover.
    legacy_supplement = bool(agent_workflow and not saved_plan and cached and reuse_existing_transcript)
    if legacy_supplement:
        reuse_existing_transcript = False
    if (saved_plan and not source_captions and settings.review_enabled
            and cached.metadata.get("review_stage") != "ready"):
        reuse_existing_transcript = False
    from .providers import ModelRegistry, ProviderError
    registry = ModelRegistry(store.data_dir)
    role_error = None
    try:
        teach_enabled, annotation_profile = registry.text_role(settings, "annotation")
    except ProviderError as exc:
        if not agent_workflow:
            raise
        teach_enabled, annotation_profile, role_error = False, None, exc
    if reuse_existing_transcript and not teach_enabled and not agent_workflow:
        raise ProviderError("请在工作区启用可用的讲解模型，再补齐讲解与译文。")
    try:
        review_enabled, review_profile = (registry.text_role(settings, "review")
                                          if settings.review_enabled and not reuse_existing_transcript
                                          and not source_captions and (teach_enabled or not agent_workflow)
                                          else (False, None))
    except ProviderError as exc:
        if not agent_workflow:
            raise
        review_enabled, review_profile, role_error = False, None, exc

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
    if not subtitle_path and not source_captions and not reuse_existing_transcript:
        if settings.asr_backend not in ("local", "local_api", "external"):
            raise ValueError("未知的语音识别方式。")
        transcript_spec.update(asr_backend=settings.asr_backend, suppress_bgm=settings.suppress_bgm)
        if settings.asr_backend in ("external", "local_api"):
            from .external_asr import api_options
            endpoint, model, fmt, _ = api_options(settings)
            transcript_spec.update(asr_model=model, endpoint=endpoint, response_format=fmt)
        else:
            transcript_spec["speaker_turns"] = settings.speaker_turns
    if decision:
        if source_captions:
            transcript_spec.pop("asr_model")
        transcript_spec.update(preparation_workflow="subtitle_agent", subtitle_plan_identity=decision["identity"])
    annotation_spec = {
        "provider": settings.provider if teach_enabled else "local",
        "model": settings.model if teach_enabled else "",
        "language": asdict(language),
        "target_script": settings.target_script,
        "text_normalization": "hk-cantonese-v1",
        "teaching_version": ANNOTATION_VERSION,
    }
    if annotation_profile:
        annotation_spec.update(provider=annotation_profile["provider"], model=annotation_profile["model"],
                               profile=profile_identity(annotation_profile))
    if settings.teaching_preferences:
        if (not isinstance(settings.teaching_preferences, str) or len(settings.teaching_preferences) > 6000
                or "\x00" in settings.teaching_preferences):
            raise ValueError("讲解偏好最多 6000 字符")
        annotation_spec["teaching_preferences"] = settings.teaching_preferences
    review_spec = ({"provider": settings.review_provider, "model": settings.review_model,
                    "version": REVIEW_VERSION, "language": asdict(language),
                    "target_script": settings.target_script}
                   if review_enabled else None)
    if review_spec and review_profile:
        review_spec.update(provider=review_profile["provider"], model=review_profile["model"],
                           profile=profile_identity(review_profile))
    annotation_spec["review"] = review_spec
    if reuse_existing_transcript and (not cached or not cached.cues):
        raise ProviderError("此视频没有可复用的字幕，请先生成或导入字幕。")
    if resume_requested and (not agent_workflow or saved_plan):
        transcript_spec = cached.metadata.get("transcript_spec", transcript_spec)
        if cached.metadata.get("review_stage") == "ready" and cached.metadata.get("review_spec"):
            review_spec = cached.metadata.get("review_spec", review_spec)
            annotation_spec["review"] = review_spec
    # Old workspaces keep their existing transcript when reopened or resumed.
    # Only an explicit full regeneration opts that material into the new audio stages.
    if cached and not agent_workflow and not subtitle_path and not force_asr and settings.asr_backend == "local":
        old_spec = cached.metadata.get("transcript_spec", {})
        if "asr_backend" not in old_spec and all(transcript_spec.get(k) == v for k, v in old_spec.items()) and old_spec:
            transcript_spec = old_spec
    reusable = cached is not None and (reuse_existing_transcript or cached.metadata.get("transcript_spec") == transcript_spec)
    reusable_asr = agent_workflow and not force_asr and not source_captions and _compatible_asr_cache(cached, transcript_spec)
    if agent_workflow:
        annotation_spec["subtitle_plan_identity"] = decision["identity"]
    plan = (["字幕评估", "读取原字幕" if source_captions else "字幕识别"] if agent_workflow else ["字幕识别"])
    if review_spec and not reuse_existing_transcript:
        plan.append("文本复核")
    if agent_workflow:
        plan.append("逐句讲解")
        if evidence and source_captions:
            needs_translation = any(not c.translation.strip() for c in evidence.source_cues)
        elif saved_plan and source_captions:
            needs_translation = any(c.id not in cached.metadata.get("provided_translations", {}) for c in cached.cues)
        else:
            needs_translation = True
        if needs_translation:
            plan.append("译稿核验")
    else:
        plan += ["逐句讲解", "译稿核验"] if teach_enabled else ["本地分词"]
    plan.append("保存字幕")
    if decision:
        report(_evidence_progress(decision, plan))
    else:
        report(Progress("检查识别与字幕缓存", stage=plan[0], plan=plan))
    adopted_source = None
    if legacy_supplement and not reusable_asr:
        matching_source = source_captions and [(c.start, c.end, c.original_text) for c in cached.cues] == [
            (c.start, c.end, c.original_text) for c in evidence.source_cues]
        if not matching_source:
            raise ProviderError("旧字幕缺少可核实的音轨或原文来源。请从准备队列启动字幕 Agent，重新判断字幕和识别音轨。")
        # Library links and notes refer to the old IDs. Only adopt evidence after
        # checking the entire sequence, text and timing, preserving those links.
        adopted_source = [replace(c, id=old.id) for c, old in zip(evidence.source_cues, cached.cues)]
    if reusable:
        document = cached
        report(f"读取已保存转写：{len(document.cues)} 句")
        if (agent_workflow and not teach_enabled and document.metadata.get("stage") == "ready"
                and all(translation_available(c, document.metadata.get("translation_review", {})) for c in document.cues)):
            store.ensure_subtitle_files(document)
            report("已复用保存的逐句讲解与双语字幕")
            return document
        if document.metadata.get("annotation_spec") == annotation_spec and document.metadata.get("stage") == "ready":
            review = document.metadata.get("translation_review", {})
            if teach_enabled and all(translation_available(c, review) for c in document.cues):
                store.ensure_subtitle_files(document)
                report("原字幕译文与逐句讲解已就绪" if review.get("provided") else "逐句译文核验和讲解已就绪")
                return document
            if not teach_enabled and document.metadata.get("lesson_status") == "local_only":
                report("本地字幕已就绪，尚未生成模型讲解与译文")
                return document
    else:
        asr_metadata = {}
        if source_captions:
            limit = start + duration if duration is not None else float("inf")
            cues = [c for c in (adopted_source or evidence.source_cues) if c.end > start and c.start < limit]
        elif subtitle_path:
            report(f"读取字幕：{subtitle_path.name}")
            cues = load_subtitles(subtitle_path)
            if start or duration is not None:
                limit = start + duration if duration is not None else float("inf")
                cues = [c for c in cues if c.end > start and c.start < limit]
        elif reusable_asr:
            report("复用同一视频、音轨和时间范围内的原始识别")
            raw = cached.metadata.get("raw_display_text", {})
            cues = [replace(c, text=raw.get(c.id, c.original_text), explanation="", translation="", tokens=[],
                            segmented_texts=[]) for c in cached.cues]
            # Keep recognizer provenance, without carrying forward old teaching or review state.
            asr_metadata = {k: v for k, v in cached.metadata.items()
                            if k.startswith(("asr_", "raw_asr", "alignment_", "speaker_"))
                            or k in ("bgm_method", "audio_chunks")}
        elif settings.asr_backend in ("local_api", "external"):
            from .external_asr import transcribe_external
            cues = transcribe_external(media_path, settings, store.data_dir, start=start, duration=duration,
                                       progress=report, cancelled=cancelled, permission=audio_permission,
                                       metadata_out=asr_metadata)
        else:
            report("本地语音识别")
            model = settings.asr_model
            if model.startswith("local:"):
                from .local_whisper import resolve_local_model
                model = str(resolve_local_model(model))
            cues = transcribe(media_path, language=language.target, model=model,
                              device=settings.device, audio_track=settings.audio_track, start=start,
                              duration=duration, progress=report, cancelled=cancelled,
                              metadata_out=asr_metadata, suppress_bgm=settings.suppress_bgm,
                              speaker_turns=settings.speaker_turns, audit_dir=store.data_dir / "asr-audit",
                              allow_model_downloads=allow_model_downloads)
        if not cues:
            raise ValueError("此片段没有识别到字幕。请检查音轨、时间范围，或导入现有字幕。")
        document = StudyDocument(str(media_path), language, cues, metadata={
            "stage": "transcribed", "transcript_spec": transcript_spec,
            "media_fingerprint": current_media,
            "created_at": time.time(), "completed_ids": [],
            "raw_display_text": {c.id: c.text for c in cues},
            **asr_metadata,
        })
        if decision:
            from .subtitle_sources import align_reference_hints
            references = evidence.reference_cues if evidence else []
            provided = {c.id: c.translation for c in cues if source_captions and c.translation.strip()}
            if legacy_supplement:
                # The caller asked to fill existing lessons. Keep validated fields and
                # hand revisions now that we have established the raw source identity.
                document = cached
                corrected = document.metadata.get("cue_corrections", {})
                adopted = []
                for c in document.cues:
                    if c.id in corrected or c.text != learning_text(c.original_text, language, settings.target_script):
                        provided.pop(c.id, None)
                    if c.id in provided:
                        matches = c.translation == provided[c.id] and len(c.segmented_texts) <= 1
                        c = replace(c, translation=provided[c.id], segmented_texts=[],
                                    explanation=c.explanation if matches else "", tokens=c.tokens if matches else [])
                    adopted.append(c)
                document.cues = adopted
                document.metadata["transcript_spec"] = transcript_spec
                reuse_existing_transcript = source_captions or document.metadata.get("review_stage") == "ready"
            hints = {}
            if references and not source_captions:
                end = min(start + duration if duration is not None else float("inf"),
                          decision.get("duration") or float("inf"))
                in_range = [c for c in cues if start <= c.start < c.end <= end]
                hints = align_reference_hints(in_range, references)
            document.metadata.update(
                subtitle_plan={**decision, "prepared_count": len(cues), "provided_count": len(provided),
                               "reference_hint_count": len(hints)},
                provided_translations=provided,
                subtitle_reference_cues=[{"id": c.id, "start": c.start, "end": c.end, "text": c.text}
                                         for c in references],
                subtitle_reference_hints=hints,
            )
        # Save immediately, before any optional model call can fail.
        store.save_document(document)
    check_cancel()
    if agent_workflow and (not teach_enabled or role_error):
        document.metadata.update(stage="partial", lesson_status="needs_teaching", error="TeachingUnavailable")
        store.save_document(document)
        if role_error:
            raise ProviderError(f"字幕已保存。请检查工作区文本模型设置并重试。{role_error}",
                                category="configuration") from role_error
        raise ProviderError("原字幕已保存。请在工作区启用逐句讲解模型，允许其使用本机或指定的远程文本服务后重试。")
    if review_spec and not reuse_existing_transcript:
        report(Progress("开始文本复核", stage="文本复核"))
        try:
            _review_document(document, settings, store, review_spec, report, check_cancel, cancelled, review_profile)
        except ProviderError as exc:
            if agent_workflow and _needs_text_configuration(exc):
                document.metadata.update(stage="partial", lesson_status="needs_teaching", error=type(exc).__name__)
                store.save_document(document)
            raise
    corrections = document.metadata.get("cue_corrections", {})
    same_annotation = document.metadata.get("annotation_spec") == annotation_spec
    completed = set(document.metadata.get("completed_ids", [])) if same_annotation else set()
    if teach_enabled:
        completed.intersection_update(c.id for c in document.cues if c.translation.strip() and c.explanation.strip())
    if reuse_existing_transcript:
        completed.update(c.id for c in document.cues if c.translation.strip() and c.explanation.strip())
    if not same_annotation and not reuse_existing_transcript:
        raw_display = document.metadata.get("reviewed_text" if review_spec else "raw_display_text", {})
        reviewed_parts = document.metadata.get("reviewed_segmented_texts", {})
        document.cues = [c if c.id in corrections else replace(c, text=raw_display.get(c.id, c.original_text or c.text),
                                 translation="", explanation="", tokens=[],
                                 segmented_texts=reviewed_parts.get(c.id, c.segmented_texts) if review_spec else [])
                         for c in document.cues]
    if corrections:
        completed.update(c.id for c in document.cues if c.id in corrections
                         and c.translation.strip() and c.explanation.strip())
        document.cues = [replace(c, text=corrections[c.id]["text"],
                                 segmented_texts=corrections[c.id]["segmented_texts"])
                         if c.id in corrections else c for c in document.cues]
    provided = document.metadata.get("provided_translations", {}) if agent_workflow else {}
    if provided:
        document.cues = [replace(c, translation=provided[c.id]) if c.id in provided else c for c in document.cues]
    if not reuse_existing_transcript:
        document.cues = normalize_cues(document.cues, language, settings.target_script)
    previous_responses = list(document.metadata.get("model_responses", [])) if same_annotation or reuse_existing_transcript else []
    previous_batches = list(document.metadata.get("analysis_batches", [])) if same_annotation or reuse_existing_transcript else []
    previous_checkpoints = list(document.metadata.get("checkpoint_events", [])) if same_annotation or reuse_existing_transcript else []
    teaching_errors = dict(document.metadata.get("teaching_pending_errors", {})) if same_annotation else {}
    review = document.metadata.get("translation_review", {})
    verification_model = {k: annotation_spec[k] for k in ("provider", "model", "profile") if k in annotation_spec}
    reuse_checks = (same_annotation or reuse_existing_transcript
                    and all(review.get(k) == v for k, v in verification_model.items())
                    and review.get("profile") == verification_model.get("profile"))
    if not reuse_checks or review.get("version") != TRANSLATION_REVIEW_VERSION:
        review = {"version": TRANSLATION_REVIEW_VERSION, **verification_model, "verified": {}, "segments": {}, "responses": []}
        document.metadata["translation_review"] = review
    if provided:
        review["provided"] = {c.id: lesson_digest(c, []) for c in document.cues
                              if c.id in provided and c.id in completed and c.explanation.strip()
                              and c.translation == provided[c.id] and len(c.segmented_texts) <= 1}
    review["status"] = "pending" if teach_enabled else "disabled"
    document.metadata.update(annotation_spec=annotation_spec, stage="annotating",
                             teaching_pending_errors=teaching_errors,
                             lesson_status="pending" if teach_enabled else "local_only",
                             completed_ids=sorted(completed), annotated_count=len(completed),
                             model_responses=previous_responses, analysis_batches=previous_batches,
                             checkpoint_events=previous_checkpoints, error="")
    document.provider = annotation_spec["provider"]
    document.model = annotation_spec["model"]
    pending = [c for c in document.cues if c.id not in completed]
    report(Progress("准备逐句学习资料", stage="逐句讲解" if teach_enabled else "本地分词",
                    done=len(completed), total=len(document.cues), unit="句"))
    agent = None
    store.save_document(document)

    def checkpoint(cues):
        cues = [c for c in cues if c.id not in completed]
        if not reuse_existing_transcript:
            cues = normalize_cues(cues, language, settings.target_script)
        update = {c.id: c for c in cues}
        for c in cues:
            if c.id in provided:
                if c.translation != provided[c.id] or not c.explanation.strip() or len(c.segmented_texts) > 1:
                    raise AnnotationValidationError("已有译文或原始分段发生变化，请重新检查此句。")
                review["provided"][c.id] = lesson_digest(c, [])
        document.cues = [update.get(c.id, c) for c in document.cues]
        completed.update(update)
        document.metadata["completed_ids"] = sorted(completed)
        document.metadata["annotated_count"] = len(completed)
        if agent is not None:
            document.metadata["teaching_pending_errors"] = dict(getattr(agent, "pending_repair_errors", {}))
            document.metadata["model_responses"] = previous_responses + getattr(agent, "response_metadata", [])
            document.metadata["analysis_batches"] = previous_batches + getattr(agent, "batch_metadata", [])
            state = getattr(agent, "checkpoint_metadata", None)
            document.metadata["checkpoint_events"] = previous_checkpoints + ([dict(state)] if isinstance(state, dict) else [])
        store.save_document(document)

        report(Progress(f"学习资料已保存 {len(completed)}/{len(document.cues)} 句",
                        done=len(completed), total=len(document.cues), unit="句"))

    try:
        if document.provider == "local":
            report("本地分词与读音")
            checkpoint(annotate_local(pending, language))
        else:
            agent = LearningAgent(provider=document.provider, model=document.model or None,
                                  **({"profile": annotation_profile} if annotation_profile else {}))
            agent.max_workers = settings.llm_workers
            agent.split_invalid_batches = True
            agent.repair_feedback = teaching_errors
            agent.preserve_text = bool(agent_workflow or reuse_existing_transcript or review_spec or corrections)
            agent.teaching_preferences = settings.teaching_preferences
            agent.provided_translations = provided
            agent.reference_hints = document.metadata.get("subtitle_reference_hints", {}) if agent_workflow else {}
            agent.unreviewed_source = bool(agent_workflow and document.metadata.get("review_stage") != "ready")
            if pending:
                report(f"{document.provider} 分析：{len(pending)} 句")
                result = agent.annotate(pending, language, progress=report, cancelled=cancelled,
                                        checkpoint=checkpoint, context_cues=document.cues)
                checkpoint(result)
            if not all(c.translation.strip() and c.explanation.strip() for c in document.cues):
                raise ValueError("仍有字幕缺少逐句译文或讲解。")
            if len(provided) != len(document.cues):
                report(Progress("开始核验新生成的译稿", stage="译稿核验", done=len(review["verified"]),
                                total=len(document.cues) - len(provided), unit="句"))
                _verify_document(document, agent, store, report, check_cancel)
            elif all(translation_available(c, review) for c in document.cues):
                review["status"] = "ready"
            else:
                raise AnnotationValidationError("已有译文与逐句讲解尚未配对完成。")
            document.metadata["lesson_status"] = ("provided" if len(provided) == len(document.cues) else
                                                  "mixed" if provided else "verified")
        check_cancel()
        document.metadata.update(stage="ready", finished_at=time.time(), error="")
        document.metadata.pop("reuse_transcript_once", None)
        report(Progress("正在保存字幕与学习资料", stage="保存字幕"))
        store.save_document(document)
        report(f"已准备 {len(document.cues)} 句" if teach_enabled else "本地字幕已就绪，尚未生成模型讲解与译文")
        return document
    except Exception as exc:
        if review["status"] == "running":
            review["status"] = "partial"
        document.metadata["stage"] = "partial"
        if agent is not None:
            document.metadata["teaching_pending_errors"] = dict(getattr(agent, "pending_repair_errors", teaching_errors))
            document.metadata["model_responses"] = previous_responses + getattr(agent, "response_metadata", [])
            document.metadata["analysis_batches"] = previous_batches + getattr(agent, "batch_metadata", [])
            state = getattr(agent, "checkpoint_metadata", None)
            document.metadata["checkpoint_events"] = previous_checkpoints + ([dict(state)] if isinstance(state, dict) else [])
        # Error class only: an upstream exception could contain provider request details.
        document.metadata["error"] = type(exc).__name__
        if agent_workflow and isinstance(exc, ProviderError) and _needs_text_configuration(exc):
            document.metadata["lesson_status"] = "needs_teaching"
        store.save_document(document)
        raise


def _needs_text_configuration(error):
    return error.category in ("credentials", "configuration") or error.status in (401, 402, 403, 404)
