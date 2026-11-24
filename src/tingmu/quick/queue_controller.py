"""Queue domain state and explicit UI/service ports."""

from dataclasses import dataclass, field
from typing import Any, Callable
from dataclasses import replace
from pathlib import Path
from tingmu.storage import canonical_path


@dataclass
class QueueState:
    queue: Any = None
    jobs: list[dict] = field(default_factory=list)
    last_queue_states: dict = field(default_factory=dict)
    timer: Any = None
    unsubscribe_queue: Any = None


@dataclass(frozen=True)
class QueuePorts:
    start_queue: Callable
    read_lesson_model_status: Callable
    enqueue: Callable
    document: Callable
    cancel_task: Callable
    appAction: Any
    add_paths: Callable
    inbox_state: Any
    is_closed: Callable
    job_stage: Callable
    jobsChanged: Any
    library_cache: Any
    library_rows: Callable
    load_document: Callable
    log: Any
    media_path: Callable
    publish_library_rows: Callable
    refresh_jobs: Callable
    refresh_library: Callable
    resume_inbox: Callable
    set_state: Callable
    settings: Callable
    start_job: Callable
    store: Any
    task_keys: Callable


class QueueController:
    def __init__(self, state: QueueState, ports: QueuePorts):
        self.state = state
        self.ports = ports

    def _schedule_queue_refresh(self):
        if not self.ports.is_closed() and not self.state.timer.isActive():
            self.state.timer.start()

    def _refresh_jobs(self):
        self.state.timer.stop()
        snapshot = self.state.queue.snapshot()
        rows = []
        for job in snapshot:
            done, total = job.get("completed_cues", 0), job.get("total_cues", 0)
            stage_done, stage_total = job.get("stage_done", 0), job.get("stage_total", 0)
            terminal = job["status"] == "completed"
            fraction = min(1.0, max(0.0, stage_done / stage_total)) if stage_total else 0.0
            if not job.get("plan"):
                fraction = done / total if total else 0.0
            rows.append(
                {
                    **job,
                    "path": job["media_path"],
                    "message": job.get("progress", ""),
                    "error": job.get("error_message")
                    or (
                        "模型返回的字幕未通过校验；已完成步骤已保留，可重试。"
                        if job.get("error") == "AnnotationValidationError"
                        else job.get("error", "")
                    ),
                    "progress": 1.0 if terminal else fraction,
                    "stage": self.ports.job_stage(job),
                    "done": done,
                    "total": total,
                }
            )
            phase = (job["status"], self.ports.job_stage(job))
            if self.state.last_queue_states.get(job["id"]) != phase:
                self.state.last_queue_states[job["id"]] = phase
                self.ports.log.event(
                    "queue_stage",
                    "视频处理阶段更新",
                    job_id=job["id"],
                    status=phase[0],
                    stage=phase[1],
                    done=done,
                    total=total,
                )
        if rows != self.state.jobs:
            prior = {j["id"]: j["status"] for j in self.state.jobs}
            self.state.jobs = rows
            self.ports.jobsChanged.emit()
            for job in rows:
                if job["status"] in ("completed", "paused", "failed") and job["status"] != prior.get(
                    job["id"]
                ):
                    self.ports.library_cache.invalidate(job["path"])
            if prior != {j["id"]: j["status"] for j in rows}:
                self.ports.refresh_library()
            else:
                # The queue already read this checkpoint. Avoid deserializing every
                # film and hashing every lesson on the UI thread for each batch.
                latest = {j["path"]: j for j in rows}
                updated = []
                for item in self.ports.library_rows():
                    job = latest.get(item["path"])
                    if job and job["status"] == "running":
                        item = {
                            **item,
                            "progress": job["message"],
                            "cueCount": job["total"] or item["cueCount"],
                        }
                    updated.append(item)
                if updated != self.ports.library_rows():
                    self.ports.publish_library_rows(updated)
            if any(
                j["path"] == self.ports.media_path()
                and j["status"] in ("completed", "paused", "failed")
                and j["status"] != prior.get(j["id"])
                for j in rows
            ):
                self.ports.load_document()

    @staticmethod
    def _job_stage(job):
        from tingmu.preparation_stages import stage_label

        text = job.get("progress", "")
        if job["status"] == "completed":
            return "完成"
        label = stage_label(job.get("stage_code", ""))
        if label:
            return label
        if job.get("stage_name"):
            return job["stage_name"]
        if "译文核验" in text:
            return "译稿核验"
        if "复核" in text:
            return "文本复核"
        if any(word in text for word in ("讲解", "教学", "分析", "标注")):
            return "学习材料"
        if any(word in text for word in ("识别", "音频", "模型", "转写", "Qwen", "Whisper")):
            return "外部识别" if job.get("settings", {}).get("asr_backend") == "external" else "本地识别"
        return {"queued": "等待", "paused": "已暂停", "failed": "失败"}.get(job["status"], "准备")

    def _enqueue(self, paths, subtitle_paths=None):
        if not paths:
            raise ValueError("请先勾选要处理的视频")
        self.ports.add_paths(paths)
        subtitle_paths = dict(subtitle_paths or {})
        for media in paths:
            if canonical_path(media) in subtitle_paths:
                continue
            doc = self.ports.store.load_document(str(media), self.ports.settings().language)
            subtitle = doc.metadata.get("subtitle_path") if doc else None
            if subtitle:
                subtitle_paths[canonical_path(media)] = subtitle
        added = self.state.queue.enqueue(
            [Path(p) for p in paths], replace(self.ports.settings()), subtitle_paths
        )
        self.ports.refresh_jobs()
        self.ports.set_state(status=f"{len(added)} 个视频已加入处理队列", error="")

    def _start_queue(self):
        if "queue" in self.ports.task_keys():
            return
        if not self.state.queue.pending_count:
            self.state.queue.retry_failed()
        if not self.state.queue.pending_count:
            raise ValueError("队列中没有待处理视频")
        self.ports.resume_inbox()
        self.ports.start_job(
            "queue",
            lambda progress, cancelled: self.state.queue.run(
                progress, cancelled, on_error=lambda exc: self.ports.log.exception(exc, "queue_error")
            ),
            lambda result: self.ports.set_state(
                status="队列已暂停" if self.state.queue.pending_count else "队列处理结束"
            ),
        )

    def _resume_inbox(self):
        self.ports.inbox_state.inbox_paused = False
        self.state.queue.resume_automatic()

    def command_enqueue(self, name, p):
        self.ports.enqueue(p.get("paths", []))

    def command_prepareCurrent(self, name, p):
        self.ports.enqueue([self.ports.media_path()] if self.ports.media_path() else [])
        self.ports.start_queue()

    def command_openLessonSettings(self, name, p):
        self.ports.appAction.emit("lessonModels", {})

    def command_prepareLessons(self, name, p):
        if not self.ports.document() or not self.ports.document().cues:
            raise ValueError("请先为这个视频准备学习字幕。")
        if not self.ports.read_lesson_model_status()["configured"]:
            raise ValueError("请先在工作区的文本模型设置中分配逐句讲解模型。")
        if "queue" in self.ports.task_keys():
            raise ValueError("正在准备视频，请等待当前任务完成。")
        ident = self.state.queue.enqueue_lessons(
            Path(self.ports.media_path()), replace(self.ports.settings())
        )
        self.ports.start_job(
            "queue",
            lambda progress, cancel: self.state.queue.run(
                progress,
                cancel,
                on_error=lambda exc: self.ports.log.exception(exc, "queue_error"),
                job_ids=[ident],
            ),
            lambda _: self.ports.load_document(),
        )

    def command_startQueue(self, name, p):
        self.ports.start_queue()

    def command_pauseQueue(self, name, p):
        self.ports.inbox_state.inbox_paused = True
        self.state.queue.pause_automatic()
        self.state.queue.pause()
        if "queue" in self.ports.task_keys():
            self.ports.cancel_task("queue")
        self.ports.set_state(status="正在暂停；已完成步骤会保留")

    def command_retryJob(self, name, p):
        self.state.queue.retry(p["id"])
        self.ports.refresh_jobs()
        self.ports.start_queue()

    def command_retryFailed(self, name, p):
        self.state.queue.retry_failed()
        self.ports.refresh_jobs()

    def command_removeJob(self, name, p):
        self.state.queue.remove(p["id"])
        self.ports.refresh_jobs()

    def command_clearCompleted(self, name, p):
        self.state.queue.clear_completed()
        self.ports.refresh_jobs()

    def command_resetJobs(self, name, p):
        return reset_materials(
            name,
            p,
            queue=self.state.queue,
            settings=self.ports.settings(),
            workers=self.ports.task_keys(),
            cache=self.ports.library_cache,
            load_document=self.ports.load_document,
            refresh_jobs=self.ports.refresh_jobs,
            refresh_library=self.ports.refresh_library,
            set_state=self.ports.set_state,
        )


def reset_materials(
    name, p, *, queue, settings, workers, cache, load_document, refresh_jobs, refresh_library, set_state
):
    if {"queue", "import"} & set(workers):
        raise ValueError("请先暂停处理队列，等待后台任务结束后清除材料")
    ids = list(p.get("ids", []))
    jobs = queue.snapshot()
    if name == "resetMedia":
        from tingmu.storage import canonical_path

        paths = {canonical_path(x) for x in p.get("paths", [])}
        ids = [j["id"] for j in jobs if j["media_path"] in paths]
    count = queue.reset(ids, str(p.get("mode", "all")), bool(p.get("requeue", False)), settings)
    publish_material_reset(
        (j["media_path"] for j in jobs if j["id"] in ids),
        cache=cache,
        load_document=load_document,
        refresh_jobs=refresh_jobs,
        refresh_library=refresh_library,
    )
    set_state(domain="queue", status=f"已清除 {count} 个视频的材料；视频、收藏与笔记已保留")


def publish_material_reset(paths, *, cache, load_document, refresh_jobs, refresh_library):
    """Invalidate reset sources before any UI projection can reuse their old cues."""
    for media_path in set(paths):
        cache.invalidate(media_path)
    load_document()
    refresh_jobs()
    refresh_library()


def legacy_command(host, name, payload):
    """Finite adapter for previous unbound FeatureMixin integrations."""
    if name not in ("resetJobs", "resetMedia"):
        return False
    reset_materials(
        name,
        payload,
        queue=host.queue,
        settings=host._settings,
        workers=host._workers,
        cache=host._library_cache,
        load_document=host._load_document,
        refresh_jobs=host._refresh_jobs,
        refresh_library=host._refresh_library,
        set_state=host._set_state,
    )
    return True
