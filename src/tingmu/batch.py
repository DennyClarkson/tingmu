"""Persistent, sequential video preparation queue with per-video checkpoints."""

from copy import deepcopy
from dataclasses import asdict, fields
import hashlib
import json
from pathlib import Path
import threading
import time

from .models import AppSettings
from .diagnostics import redact_text
from .pipeline import find_subtitle, prepare_media
from .providers import ProviderError
from .storage import Store, atomic_json, canonical_path, media_fingerprint


def _failure_details(error, document):
    """Expose our safe validation errors, never arbitrary upstream payloads."""
    metadata = document.metadata if document else {}
    reviewing = metadata.get("review_stage") in ("partial", "running")
    stage = "文本复核" if reviewing else "学习讲解" if metadata.get("annotation_spec") else "本地识别"
    count = metadata.get("reviewed_count" if reviewing else "annotated_count", 0)
    if isinstance(error, ProviderError):
        reason = redact_text(str(error))[:300]
    elif isinstance(error, FileNotFoundError):
        reason = "找不到视频或所需文件，请检查原文件位置。"
    else:
        reason = "处理遇到异常，请查看开发者日志中的错误位置。"
    return {"progress": f"{stage}未完成，可重试", "error_message": reason + " 已完成步骤已保存，重试会继续处理。",
            "completed_cues": count, "total_cues": len(document.cues) if document else 0}


class PreparationQueue:
    def __init__(self, store: Store):
        self.store = store
        self.path = store.data_dir / "preparation-queue.json"
        self._lock = threading.RLock()
        self._pause = threading.Event()
        self._running = False
        try:
            saved = json.loads(self.path.read_text("utf-8"))
            self._jobs = saved["jobs"] if isinstance(saved.get("jobs"), list) else []
        except (OSError, ValueError, TypeError, KeyError):
            self._jobs = []
        for job in self._jobs:
            if job.get("status") == "running":
                job.update(status="paused", progress="上次处理已中断，可继续")
        self._save()

    def _save(self):
        atomic_json(self.path, {"version": 1, "jobs": self._jobs})

    def snapshot(self) -> list[dict]:
        with self._lock:
            return deepcopy(self._jobs)

    @property
    def pending_count(self) -> int:
        return sum(j["status"] in ("queued", "paused") for j in self.snapshot())

    def enqueue(self, paths: list[Path], settings: AppSettings,
                subtitle_paths: dict[str, str] | None = None) -> list[dict]:
        added = []
        with self._lock:
            for path in paths:
                media = canonical_path(path)
                fingerprint = media_fingerprint(media)
                if not fingerprint:
                    raise FileNotFoundError(f"视频不存在：{path}")
                spec = asdict(settings)
                subtitle = (subtitle_paths or {}).get(media)
                if not subtitle:
                    discovered = find_subtitle(Path(media), settings.target_language)
                    subtitle = str(discovered) if discovered else None
                identity = json.dumps([media, fingerprint, spec, subtitle,
                                       media_fingerprint(subtitle) if subtitle else None], sort_keys=True)
                job_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
                if any(j["id"] == job_id for j in self._jobs):
                    continue
                job = {"id": job_id, "media_path": media, "name": Path(media).name,
                       "fingerprint": fingerprint, "settings": spec, "subtitle_path": subtitle,
                       "status": "queued", "progress": "等待处理", "error": "",
                       "completed_cues": 0, "total_cues": 0,
                       "created_at": time.time(), "updated_at": time.time()}
                self._jobs.append(job)
                added.append(deepcopy(job))
            self._save()
        return added

    def pause(self):
        self._pause.set()

    def retry(self, job_id: str):
        with self._lock:
            for job in self._jobs:
                if job["id"] == job_id and job["status"] in ("failed", "paused", "cleared"):
                    job.update(status="queued", error="", error_message="", progress="等待继续", updated_at=time.time())
            self._save()

    def retry_failed(self):
        for job in self.snapshot():
            if job["status"] == "failed":
                self.retry(job["id"])

    def remove(self, job_id: str):
        with self._lock:
            if any(j["id"] == job_id and j["status"] == "running" for j in self._jobs):
                raise ValueError("请先暂停正在处理的视频")
            self._jobs = [j for j in self._jobs if j["id"] != job_id]
            self._save()

    def clear_completed(self):
        with self._lock:
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
        with self._lock:
            if self._running:
                raise ValueError("请先暂停队列，等待当前处理结束后清除材料")
            selected = [j for j in self._jobs if j["id"] in wanted]
            if len(selected) != len(wanted):
                raise ValueError("所选任务已变化，请重新选择")
            allowed = {f.name for f in fields(AppSettings)}
            identities = set()
            for job in selected:
                previous = AppSettings(**{k: v for k, v in job["settings"].items() if k in allowed})
                identity = (job["media_path"], previous.source_language, previous.target_language, previous.level)
                if identity not in identities:
                    self.store.reset_material(job["media_path"], previous.language, mode)
                    identities.add(identity)
                # Retain the job's language identity; use today's model/ASR choices.
                current = deepcopy(settings) if settings else previous
                current.source_language, current.target_language, current.level = identity[1:]
                if mode == "teaching":
                    current.asr_model, current.device, current.audio_track = (
                        previous.asr_model, previous.device, previous.audio_track)
                job.update(settings=asdict(current), status="queued" if requeue else "cleared",
                           progress="等待重新生成" if requeue else "材料已清除", error="", error_message="",
                           completed_cues=0, total_cues=0, updated_at=time.time())
                job["force_asr"] = mode == "all"
                if mode == "all":
                    job["subtitle_path"] = None
            self._save()
            return len(selected)

    def run(self, progress=None, cancelled=None, on_error=None, *, job_ids=None) -> list[dict]:
        selected = frozenset(job_ids) if job_ids is not None else None
        with self._lock:
            if self._running:
                raise RuntimeError("批量队列已在运行")
            self._running = True
            self._pause.clear()
        report = progress or (lambda message: None)

        def stopped():
            return self._pause.is_set() or bool(cancelled and cancelled())

        try:
            while not stopped():
                with self._lock:
                    job = next((j for j in self._jobs if j["status"] in ("queued", "paused")
                                and (selected is None or j["id"] in selected)), None)
                    if job is None:
                        break
                    job.update(status="running", error="", error_message="", progress="开始处理", updated_at=time.time())
                    self._save()
                allowed = {f.name for f in fields(AppSettings)}
                settings = AppSettings(**{k: v for k, v in job["settings"].items() if k in allowed})

                def update(message):
                    cached = self.store.load_document(job["media_path"], settings.language)
                    with self._lock:
                        job.update(progress=str(message), updated_at=time.time())
                        if cached:
                            job["total_cues"] = len(cached.cues)
                            stage = cached.metadata.get("stage")
                            job["completed_cues"] = cached.metadata.get(
                                "reviewed_count" if stage == "reviewing" else "annotated_count", 0)
                        self._save()
                    report(f"{job['name']} · {message}")

                try:
                    extra = {"force_asr": True} if job.get("force_asr") else {}
                    document = prepare_media(Path(job["media_path"]), settings, self.store,
                                             subtitle_path=Path(job["subtitle_path"]) if job["subtitle_path"] else None,
                                             progress=update, cancelled=stopped, **extra)
                    with self._lock:
                        job.update(status="completed", progress="已准备", completed_cues=len(document.cues),
                                   total_cues=len(document.cues), error="", error_message="", updated_at=time.time())
                        self._save()
                except Exception as exc:
                    details = _failure_details(exc, self.store.load_document(job["media_path"], settings.language))
                    with self._lock:
                        job.update(details)
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
            with self._lock:
                self._running = False
