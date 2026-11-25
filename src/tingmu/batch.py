"""Persistent, sequential video preparation queue with per-video checkpoints."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import fields
import hashlib
import json
from pathlib import Path
import threading
import time

from .models import AppSettings, preparation_identity_settings
from .diagnostics import redact_text
from .preparation_stages import stage_code, stage_label
from .translation_review import TRANSLATION_REVIEW_VERSION
from .storage import Store, atomic_json, canonical_path, media_fingerprint


def prepare_media(*args, **kwargs):
    from .pipeline import prepare_media as prepare
    return prepare(*args, **kwargs)


def find_subtitle(media_path: Path, target_language: str) -> Path | None:
    from .media_files import find_subtitle as find
    return find(media_path, target_language)


def _failure_details(error, document):
    """Expose our safe validation errors, never arbitrary upstream payloads."""
    from .media import AsrSetupError, MediaError
    from .providers import ProviderError

    metadata = document.metadata if document else {}
    reviewing = metadata.get("review_stage") in ("partial", "running")
    stage = "文本复核" if reviewing else "学习讲解" if metadata.get("annotation_spec") else "字幕识别"
    count = metadata.get("reviewed_count" if reviewing else "annotated_count", 0)
    review = metadata.get("translation_review", {})
    if not reviewing and review.get("status") in ("running", "partial"):
        stage = "译文核验"
        count = len(review["verified"])
    if isinstance(error, ProviderError):
        reason = ("模型服务已限流（HTTP 429），可调低「同时请求上限」，稍后重试。"
                  if error.status == 429 else redact_text(str(error))[:300])
        if error.retry_after is not None and error.status in (429, 503):
            reason += f" 服务建议等待 {error.retry_after} 秒。"
        number = getattr(error, "cue_number", None)
        if document and isinstance(number, int) and 1 <= number <= len(document.cues):
            seconds = int(document.cues[number - 1].start)
            reason = f"第 {number} 句（{seconds // 60:02d}:{seconds % 60:02d}） · {reason}"
    elif isinstance(error, FileNotFoundError):
        reason = "找不到视频或所需文件，请检查原文件位置。"
    elif isinstance(error, AsrSetupError):
        reason = redact_text(str(error))[:300]
    elif isinstance(error, MediaError):
        reason = "请检查工作区的语音识别方式、所选音轨和模型安装状态。"
    else:
        reason = "处理遇到异常，请查看开发者日志中的错误位置。"
    return {"progress": f"{stage}未完成，可重试", "error_message": reason + " 已完成步骤已保存，重试会继续处理。",
            "stage_code": metadata.get("stage_code") or stage_code(stage),
            "completed_cues": count, "total_cues": len(document.cues) if document else 0}


def _apply_progress(job, details):
    if "decision" in details:
        job["decision"] = details["decision"]
        for key in ("evidence_counts", "source_name", "reference_name", "evidence_notes"):
            if key in details:
                job[key] = details[key]
    if "plan" in details:
        job["plan"] = details["plan"]
    if "stage" in details or "stage_code" in details:
        code = details.get("stage_code") or stage_code(details.get("stage", ""))
        job.update(stage_name=details.get("stage", stage_label(code)), stage_code=code,
                   activity="", stage_done=0, stage_total=0, unit="", batches=[])
    if "activity" in details and details["activity"] != job.get("activity"):
        job.update(activity=details["activity"], stage_done=0, stage_total=0, unit="")
    if "total" in details and "done" in details:
        job.update(stage_done=details["done"], stage_total=details["total"], unit=details.get("unit", ""))
        if job["unit"] == "句":
            job.update(completed_cues=details["done"], total_cues=details["total"])
    if batch := details.get("batch"):
        rows = [item for item in job.get("batches", []) if item["id"] != batch["id"]]
        rows.append(batch)
        running = sorted((item for item in rows if item["status"] == "running"), key=lambda item: item["id"])
        recent = [item for item in rows if item["status"] != "running"][-3:]
        job["batches"] = running + recent


def _end_batches(job):
    for batch in job.get("batches", []):
        if batch["status"] == "running":
            batch["status"] = "interrupted"


def subtitle_inputs(media, subtitle):
    """Cheap queue identity. Track changes inside the container follow its fingerprint."""
    from .subtitle_sources import discover_sidecars
    if subtitle:
        return []  # The explicit sidecar and its fingerprint are already in the identity.
    return [{"name": p.name, "fingerprint": media_fingerprint(p)} for p in discover_sidecars(Path(media))]


class PreparationQueue:
    def __init__(self, store: Store):
        self.store = store
        self.path = store.data_dir / "preparation-queue.json"
        self._lock = threading.RLock()
        self._subscribers = []
        self._revision = 0
        self._saved_payload = None
        self._pause = threading.Event()
        self._running = False
        saved = {}
        try:
            saved = json.loads(self.path.read_text("utf-8"))
            self._jobs = saved["jobs"] if isinstance(saved.get("jobs"), list) else []
        except (OSError, ValueError, TypeError, KeyError):
            self._jobs = []
        legacy_ids = None
        if self._jobs and saved.get("model_scope") != "global":
            from .providers import ModelRegistry
            legacy_ids = ModelRegistry(store.data_dir).legacy_profile_ids()
        for job in self._jobs:
            if legacy_ids is not None:
                from .global_models import ROLES
                settings = job.get("settings", {})
                for ident, _, _ in ROLES.values():
                    original = settings.get(ident)
                    if original:
                        settings[ident] = legacy_ids.get(original, "missing:" + original)
            if job.get("status") == "running":
                job.update(status="paused", progress="上次处理已中断，可继续")
                _end_batches(job)
        self._save()

    def subscribe(self, callback):
        """Observe changes on their originating thread; callbacks must stay lightweight.

        Return an idempotent release function. Observers run outside the queue lock
        and can safely read, unsubscribe, or mutate the queue. One failing observer
        cannot prevent persistence or delivery to the remaining observers.
        """
        if not callable(callback):
            raise TypeError("Queue observer must be callable")
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)
        return lambda: self.unsubscribe(callback)

    def unsubscribe(self, callback):
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    @contextmanager
    def _mutation(self):
        callbacks = ()
        try:
            with self._lock:
                revision = self._revision
                try:
                    yield
                finally:
                    if self._revision != revision:
                        callbacks = tuple(self._subscribers)
        finally:
            for callback in callbacks:
                # An earlier observer may unsubscribe this one during delivery.
                with self._lock:
                    subscribed = callback in self._subscribers
                if subscribed:
                    try:
                        callback()
                    except Exception:
                        # An observer owns its error reporting, never queue state.
                        continue

    def _save(self):
        # Timestamp-only changes do not require a new durable snapshot.
        signature = json.dumps([{key: value for key, value in job.items() if key != "updated_at"}
                                for job in self._jobs], ensure_ascii=False, sort_keys=True)
        if signature == self._saved_payload:
            return
        atomic_json(self.path, {"version": 1, "model_scope": "global", "jobs": self._jobs})
        self._saved_payload = signature
        self._revision += 1

    def snapshot(self) -> list[dict]:
        with self._lock:
            return deepcopy(self._jobs)

    @property
    def pending_count(self) -> int:
        return sum(j["status"] in ("queued", "paused") for j in self.snapshot())

    def enqueue(self, paths: list[Path], settings: AppSettings,
                subtitle_paths: dict[str, str] | None = None, *, automatic: bool = False) -> list[dict]:
        added = []
        with self._mutation():
            for path in paths:
                media = canonical_path(path)
                fingerprint = media_fingerprint(media)
                if not fingerprint:
                    raise FileNotFoundError(f"视频不存在：{path}")
                spec = settings.to_dict()
                subtitle = (subtitle_paths or {}).get(media)
                agent_workflow = settings.preparation_workflow == "subtitle_agent"
                if not subtitle and not agent_workflow:
                    discovered = find_subtitle(Path(media), settings.target_language)
                    subtitle = str(discovered) if discovered else None
                parts = [media, fingerprint, preparation_identity_settings(spec), subtitle,
                         media_fingerprint(subtitle) if subtitle else None]
                if agent_workflow:
                    parts.append(subtitle_inputs(media, subtitle))
                identity = json.dumps(parts, sort_keys=True)
                job_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
                existing = next((j for j in self._jobs if j["id"] == job_id), None)
                if existing:
                    if automatic or existing["status"] != "completed":
                        continue
                    if existing.get("preparation_version") == TRANSLATION_REVIEW_VERSION:
                        # One published document per movie and language pair. A second workflow
                        # can supersede the material from this still-completed queue entry.
                        if not agent_workflow and not any(j["media_path"] == media and
                                j["settings"].get("preparation_workflow") == "subtitle_agent" for j in self._jobs):
                            continue
                        doc = self.store.load_document(media, settings.language)
                        plan = doc.metadata.get("subtitle_plan", {}) if doc else {}
                        if doc and doc.metadata.get("stage") == "ready" and bool(plan) == agent_workflow and (
                                not agent_workflow or plan.get("identity") == existing.get("plan_identity")):
                            continue
                    existing.update(status="queued", progress="等待更新学习资料", updated_at=time.time())
                    added.append(deepcopy(existing))
                    continue
                job = {"id": job_id, "media_path": media, "name": Path(media).name,
                       "fingerprint": fingerprint, "settings": spec, "subtitle_path": subtitle,
                       "status": "queued", "progress": "等待处理", "error": "",
                       "completed_cues": 0, "total_cues": 0,
                       "created_at": time.time(), "updated_at": time.time()}
                if automatic:
                    job.update(origin="media-inbox", allow_model_downloads=False)
                if agent_workflow:
                    job["subtitle_inputs"] = parts[-1]
                self._jobs.append(job)
                added.append(deepcopy(job))
            self._save()
        return added

    def enqueue_lessons(self, path: Path, settings: AppSettings) -> str:
        """Refresh one existing document without resetting lessons or invoking ASR."""
        media = canonical_path(path)
        spec = settings.to_dict()
        with self._mutation():
            document = self.store.load_document(media, settings.language)
            if not document or not document.cues:
                from .providers import ProviderError
                raise ProviderError("此视频没有可复用的字幕，请先生成或导入字幕。")
            fingerprint = media_fingerprint(media)
            identity = json.dumps(["existing-lessons", media, fingerprint, preparation_identity_settings(spec)],
                                  sort_keys=True)
            job_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
            job = next((item for item in self._jobs if item["id"] == job_id), None)
            if job is None:
                job = {"id": job_id, "media_path": media, "name": Path(media).name, "fingerprint": fingerprint,
                       "settings": spec, "subtitle_path": None, "created_at": time.time(),
                       "total_cues": len(document.cues), "completed_cues": 0}
                self._jobs.append(job)
            if job.get("status") != "running":
                job.update(status="queued", progress="等待补齐学习资料", error="", error_message="",
                           reuse_existing_transcript=True, force_asr=False, updated_at=time.time())
                self._save()
            return job_id

    def pause(self):
        self._pause.set()

    def pause_automatic(self):
        """Keep a deliberate pause distinct from an interrupted application shutdown."""
        with self._mutation():
            for job in self._jobs:
                if job.get("origin") == "media-inbox" and job["status"] in ("running", "queued", "paused"):
                    job["auto_paused"] = True
            self._save()

    def resume_automatic(self):
        with self._mutation():
            paused = [job for job in self._jobs if job.get("auto_paused")]
            for job in paused:
                job.pop("auto_paused")
            if paused:
                self._save()

    def retry(self, job_id: str):
        with self._mutation():
            for job in self._jobs:
                if job["id"] == job_id and job["status"] in ("failed", "paused", "cleared"):
                    if job.get("needs_teaching") and job["settings"].get("preparation_workflow") == "subtitle_agent":
                        # Explicit retry after the user enables or assigns a teaching model.
                        active = self.store.load_settings()
                        job["retry_text_settings"] = {key: getattr(active, key) for key in (
                            "text_models_enabled", "cloud_text_enabled", "annotation_profile_id", "review_profile_id",
                            "provider", "model", "review_provider", "review_model")}
                    job.pop("auto_paused", None)
                    job.update(status="queued", error="", error_message="", progress="等待继续", updated_at=time.time())
            self._save()

    def retry_failed(self):
        for job in self.snapshot():
            if job["status"] == "failed":
                self.retry(job["id"])

    def remove(self, job_id: str):
        with self._mutation():
            if any(j["id"] == job_id and j["status"] == "running" for j in self._jobs):
                raise ValueError("请先暂停正在处理的视频")
            self._jobs = [j for j in self._jobs if j["id"] != job_id]
            self._save()

    def clear_completed(self):
        with self._mutation():
            self._jobs = [j for j in self._jobs if j["status"] != "completed"]
            self._save()

    def reset(self, ids: list[str], mode: str = "all", requeue: bool = False,
              settings: AppSettings | None = None) -> int:
        """Reset selected materials atomically with respect to the queue worker."""
        if mode not in ("all", "teaching"):
            raise ValueError("无效的清除范围")
        wanted = set(ids)
        if not wanted:
            raise ValueError("请先选择队列中的视频")
        with self._mutation():
            if self._running:
                raise ValueError("请先暂停队列，等待当前处理结束后清除材料")
            selected = [j for j in self._jobs if j["id"] in wanted]
            if len(selected) != len(wanted):
                raise ValueError("所选任务已变化，请重新选择")
            allowed = {f.name for f in fields(AppSettings) if not f.name.startswith("_")}
            identities = set()
            for job in selected:
                previous = AppSettings(**{k: v for k, v in job["settings"].items() if k in allowed})
                identity = (job["media_path"], previous.source_language, previous.target_language, previous.level)
                if identity not in identities:
                    self.store.reset_material(job["media_path"], previous.language, mode)
                    identities.add(identity)
                from .material_reset import reset_job_material_state
                reset = reset_job_material_state(job, mode, requeue, settings)
                reset["updated_at"] = time.time()
                job.clear()
                job.update(reset)
            self._save()
            return len(selected)

    def run(self, progress=None, cancelled=None, on_error=None, *, job_ids=None,
            allow_model_downloads=None) -> list[dict]:
        selected = frozenset(job_ids) if job_ids is not None else None
        with self._mutation():
            if self._running:
                raise RuntimeError("批量队列已在运行")
            self._running = True
            self._pause.clear()
        report = progress or (lambda message: None)

        def stopped():
            return self._pause.is_set() or bool(cancelled and cancelled())

        try:
            while not stopped():
                with self._mutation():
                    job = next((j for j in self._jobs if j["status"] in ("queued", "paused")
                                and (selected is None or j["id"] in selected)), None)
                    if job is None:
                        break
                    job.update(status="running", error="", error_message="", progress="开始处理", updated_at=time.time(),
                               plan=[], stage_name="", stage_code="", activity="", stage_total=0, stage_done=0, unit="", batches=[])
                    self._save()
                allowed = {f.name for f in fields(AppSettings) if not f.name.startswith("_")}
                spec = {**job["settings"], **job.get("retry_text_settings", {})}
                settings = AppSettings(**{k: v for k, v in spec.items() if k in allowed})
                active = self.store.load_settings()
                settings.text_models_enabled = settings.text_models_enabled and active.text_models_enabled
                settings.cloud_text_enabled = settings.cloud_text_enabled and active.cloud_text_enabled
                if (self.store.data_dir / "settings.json").is_file():
                    settings.llm_high_concurrency = active.llm_high_concurrency
                    settings.llm_max_concurrency = active.llm_max_concurrency
                last_snapshot = None

                def update(message):
                    nonlocal last_snapshot
                    now = time.monotonic()
                    details = getattr(message, "details", {})
                    stage_event = "stage" in details or "stage_code" in details
                    notify = last_snapshot is None or now - last_snapshot >= 0.5 or stage_event
                    cached = (self.store.load_document(job["media_path"], settings.language)
                              if notify and not details and not job.get("plan") else None)
                    with self._mutation():
                        previous_stage = (job.get("stage_code"), job.get("stage_name"))
                        job.update(progress=str(message), updated_at=time.time())
                        _apply_progress(job, details)
                        if cached:
                            job["total_cues"] = len(cached.cues)
                            stage = cached.metadata.get("stage")
                            job["completed_cues"] = cached.metadata.get(
                                "reviewed_count" if stage == "reviewing" else "annotated_count", 0)
                            if stage == "verifying":
                                job["completed_cues"] = len(cached.metadata["translation_review"]["verified"])
                        changed_stage = stage_event and previous_stage != (job.get("stage_code"), job.get("stage_name"))
                        if changed_stage or "plan" in details or "decision" in details:
                            last_snapshot = now
                            self._save()
                        elif notify:
                            # The material checkpoint owns resumable work. Keep
                            # live counters visible between durable stage/status changes.
                            last_snapshot = now
                            self._revision += 1
                    report(f"{job['name']} · {message}")

                try:
                    extra = {"force_asr": True} if job.get("force_asr") else {}
                    if job.get("reuse_existing_transcript"):
                        extra["reuse_existing_transcript"] = True
                    if allow_model_downloads is False or job.get("allow_model_downloads") is False:
                        extra["allow_model_downloads"] = False
                    if job.get("origin") == "media-inbox":
                        from .media_inbox import _not_being_written
                        media = Path(job["media_path"])
                        if (not media.resolve().is_relative_to(self.store.data_dir / "media")
                                or media.resolve() != media or media_fingerprint(media) != job["fingerprint"]
                                or not _not_being_written(media)):
                            raise FileNotFoundError("自动准备的媒体已经变化或仍在复制")
                    if job.get("teaching_only") and not self.store.load_document(job["media_path"], settings.language):
                        from .providers import ProviderError
                        raise ProviderError("此视频没有可复用的字幕，请先生成或导入字幕。")
                    if settings.asr_backend == "external":
                        def current_permission():
                            active = self.store.load_settings()
                            return (active.asr_backend == "external" and active.allow_audio_upload is True
                                    and active.asr_endpoint == settings.asr_endpoint
                                    and active.asr_key_env == settings.asr_key_env)
                        extra["audio_permission"] = current_permission
                    document = prepare_media(Path(job["media_path"]), settings, self.store,
                                             subtitle_path=Path(job["subtitle_path"]) if job["subtitle_path"] else None,
                                             progress=update, cancelled=stopped, **extra)
                    with self._mutation():
                        local_only = document.metadata.get("lesson_status") == "local_only"
                        job.pop("needs_teaching", None)
                        if plan := document.metadata.get("subtitle_plan"):
                            job["plan_identity"] = plan.get("identity")
                            job["decision"] = job.get("decision") or plan.get("summary", "")
                        job.update(status="completed", progress="本地字幕就绪，未生成讲解与译文" if local_only else "已准备",
                                   preparation_version=TRANSLATION_REVIEW_VERSION, completed_cues=len(document.cues),
                                   total_cues=len(document.cues), error="", error_message="", updated_at=time.time())
                        self._save()
                except Exception as exc:
                    partial = self.store.load_document(job["media_path"], settings.language)
                    details = _failure_details(exc, partial)
                    if partial and partial.metadata.get("lesson_status") == "needs_teaching":
                        details.update(needs_teaching=True, progress="等待配置逐句讲解模型")
                        if (job.get("stage_code") or stage_code(job.get("stage_name", ""))) in ("planning", "transcript"):
                            details.update(stage_name="逐句讲解", stage_code="teaching",
                                           activity="", stage_done=0, stage_total=0, unit="")
                    with self._mutation():
                        job.update(details)
                        _end_batches(job)
                        job.update(status="paused" if stopped() else "failed",
                                   progress="已暂停，可继续" if stopped() else details["progress"],
                                   error_message="" if stopped() else details["error_message"],
                                   error="" if stopped() else type(exc).__name__, updated_at=time.time())
                        self._save()
                    if stopped():
                        break
                    if on_error:
                        on_error(exc)
                    report(f"{job['name']} · {details['progress']}：{details['error_message']}")
            return self.snapshot()
        finally:
            with self._mutation():
                self._running = False
