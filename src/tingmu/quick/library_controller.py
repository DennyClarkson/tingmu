"""Library domain state and explicit UI/service ports."""

from dataclasses import dataclass, field
from typing import Any, Callable
from copy import deepcopy
from dataclasses import fields, replace
from pathlib import Path
from PySide6.QtCore import QFileSystemWatcher, QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from tingmu.models import AppSettings, preparation_identity_settings


class _FutureActivity:
    def __init__(self, future):
        self.future = future

    def is_alive(self):
        return not self.future.done()


@dataclass
class LibraryState:
    catalog: Any = None
    library: list[dict] = field(default_factory=list)
    library_cache: Any = None
    library_positions: dict = field(default_factory=dict)
    library_generation: int = 0
    library_future: Any = None
    library_pending: Any = None
    library_executor: Any = None
    inbox_scanner: Any = None
    inbox_thread: Any = None
    inbox_executor: Any = None
    inbox_stop: Any = None
    inbox_paused: bool = False
    setup_pending: bool = False
    media_inbox: dict = field(default_factory=dict)
    inbox_timer: Any = None
    watcher: Any = None
    watch_timer: Any = None
    inbox_generation: int = 0
    inbox_busy: bool = False
    inbox_requested: bool = False
    inbox_pending_visible: bool = False


@dataclass(frozen=True)
class LibraryPorts:
    scan_media_inbox: Callable
    open_media: Callable
    add_paths: Any
    inboxCompleted: Any
    is_closed: Callable
    libraryChanged: Any
    libraryCompleted: Any
    log: Any
    mediaInbox: Any
    mediaInboxChanged: Any
    publish_library: Callable
    queue: Callable
    refresh_jobs: Callable
    refresh_library: Callable
    set_inbox_state: Callable
    set_state: Callable
    settings: Callable
    start_job: Callable
    start_library_read: Callable
    state_snapshot: Callable
    store: Any
    task_keys: Callable


class LibraryController:
    def __init__(self, state: LibraryState, ports: LibraryPorts):
        self.state = state
        self.ports = ports

    def add_paths(self, paths):
        normalized = [QUrl(p).toLocalFile() if str(p).startswith("file:") else p for p in paths]
        added = self.state.catalog.add(normalized)
        self.ports.refresh_library()
        self.ports.set_state(status=f"已添加 {len(added)} 个视频", error="")
        self.ports.log.event("library_add", "媒体索引已更新", count=len(added))
        return added

    def _copy_media(self, selected):
        agent_workflow = self.ports.settings().preparation_workflow == "subtitle_agent"
        self.ports.start_job(
            "media-copy",
            lambda progress, cancel: self.state.catalog.copy_files(
                selected, progress, cancel, agent_workflow=agent_workflow
            ),
            self.ports.add_paths,
        )

    def start_inbox(self, parent):
        self.state.watcher = QFileSystemWatcher(parent)
        self.state.watch_timer = QTimer(parent)
        self.state.watch_timer.setSingleShot(True)
        self.state.watch_timer.setInterval(150)
        self.state.watcher.directoryChanged.connect(lambda _: self.state.watch_timer.start())
        self.state.watch_timer.timeout.connect(lambda: self._scan_media_inbox(background=True))
        self.state.inbox_timer = QTimer(parent)
        self.state.inbox_timer.timeout.connect(lambda: self._scan_media_inbox(background=True))
        parent.focusWindowChanged.connect(self._window_changed)
        self.state.inbox_timer.start(2000)
        QTimer.singleShot(0, lambda: self._scan_media_inbox(background=True))

    def stop_inbox(self):
        self.state.inbox_timer.stop()
        self.state.watch_timer.stop()
        paths = self.state.watcher.directories()
        if paths:
            self.state.watcher.removePaths(paths)
        self.state.inbox_stop.set()

    def invalidate_preflight(self):
        if not self.ports.is_closed():
            self.state.inbox_generation += 1
            self.state.watch_timer.start()

    def _window_changed(self):
        if not self.ports.is_closed():
            self._update_interval(waiting=bool(self.ports.mediaInbox().get("waiting")))

    def _update_interval(self, waiting=False):
        windows = QGuiApplication.topLevelWindows()
        hidden = bool(windows) and not any(w.isVisible() for w in windows)
        interval = 2000 if waiting else 60000 if hidden else 20000
        if self.state.inbox_timer.interval() != interval:
            self.state.inbox_timer.setInterval(interval)

    def _scan_media_inbox(self, *, background=False):
        if self.ports.is_closed() or self.state.setup_pending:
            return
        if self.state.inbox_busy:
            self.state.inbox_requested = True
            self.state.inbox_pending_visible |= not background
            return
        self.state.inbox_busy = True
        if not background:
            self.state.inbox_generation += 1
            self.ports.set_inbox_state(scanning=True)
        settings = replace(self.ports.settings())
        generation = self.state.inbox_generation
        scanner, store, stop = self.state.inbox_scanner, self.ports.store, self.state.inbox_stop

        def scan():
            try:
                if scanner.folder.resolve() == scanner.folder:
                    scanner.folder.mkdir(exist_ok=True)
                result = scanner.scan(
                    cancelled=stop.is_set, agent_workflow=settings.preparation_workflow == "subtitle_agent"
                )
                if result is None or stop.is_set():
                    return
                documents = (
                    scanner.document_stages(store, result["ready"], settings.language, stop.is_set)
                    if settings.auto_prepare_media
                    else {}
                )
                if stop.is_set():
                    return
                candidates = [p for p, stage in documents.items() if stage != "ready"]
                # A ready sidecar needs no speech model; each path gets its own gate.
                result["blocked"] = scanner.preparation_reasons(settings, candidates, generation, stop.is_set)
                result["directories"] = tuple(scanner.directories)
                result["generation"] = generation
                result["documents"] = documents
                result["settings"] = settings.to_dict()
            except Exception as exc:
                result = {"failure": type(exc).__name__}
            if not stop.is_set():
                self.ports.inboxCompleted.emit(result)

        self.state.inbox_thread = _FutureActivity(self.state.inbox_executor.submit(scan))

    def _inbox_completed(self, result):
        if self.ports.is_closed():
            return
        self.state.inbox_busy = False
        if self.state.inbox_requested:
            visible = self.state.inbox_pending_visible
            self.state.inbox_requested = self.state.inbox_pending_visible = False
            QTimer.singleShot(0, lambda: self._scan_media_inbox(background=not visible))
        if result.get("failure"):
            self._update_interval(waiting=True)
            self.ports.set_inbox_state(scanning=False, blocked="暂时无法检查媒体文件夹，稍后自动重试")
            return
        # Accept legacy dataclass snapshots without runtime-only bookkeeping.
        received_settings = {k: v for k, v in result["settings"].items() if not k.startswith("_")}
        if (received_settings != self.ports.settings().to_dict()
                or result.get("generation", self.state.inbox_generation) != self.state.inbox_generation):
            self.ports.set_inbox_state(scanning=False)
            return
        # A partial/inaccessible scan must never purge index entries.
        if result["error"]:
            self._update_interval(waiting=True)
            self.ports.set_inbox_state(scanning=False, blocked=result["error"])
            return
        if "directories" in result:
            desired = set(result["directories"])
            previous = set(self.state.watcher.directories())
            if previous - desired:
                self.state.watcher.removePaths(sorted(previous - desired))
            if desired - previous:
                self.state.watcher.addPaths(sorted(desired - previous))
        self._update_interval(bool(result["waiting"]))
        added, removed = self.state.catalog.reconcile_inbox(result["ready"], result["present"])
        ready = result["ready"]
        selected, blocked = [], []
        enabled = self.ports.settings().auto_prepare_media and not self.state.inbox_paused
        snapshot = self.ports.queue().snapshot() if removed or (enabled and ready) else []
        if enabled:
            for path in self.state.catalog.automatic & ready.keys():
                if result["documents"].get(path) == "ready":
                    continue
                same = [
                    j
                    for j in snapshot
                    if j["media_path"] == path
                    and j["settings"].get("preparation_workflow", "classic")
                    == self.ports.settings().preparation_workflow
                    and all(
                        j["settings"].get(k) == getattr(self.ports.settings(), k)
                        for k in ("source_language", "target_language", "level")
                    )
                    and j.get("fingerprint") == ready[path]
                ]
                # Existing manual/failed/cleared jobs retain their explicit controls.
                if same:
                    own = [
                        j
                        for j in same
                        if j.get("origin") == "media-inbox"
                        and j["status"] in ("queued", "paused")
                        and not j.get("auto_paused")
                    ]
                    for job in own:
                        previous = AppSettings(
                            **{
                                k: v
                                for k, v in job["settings"].items()
                                if k in {f.name for f in fields(AppSettings) if not f.name.startswith("_")}
                            }
                        )
                        # Configuration changes must not revive a frozen request automatically.
                        if preparation_identity_settings(previous.to_dict()) != preparation_identity_settings(
                            self.ports.settings().to_dict()
                        ):
                            continue
                        reason = result["blocked"].get(path, "")
                        if reason:
                            blocked.append(reason)
                        else:
                            selected.append(job["id"])
                    continue
                reason = result["blocked"].get(path, "")
                if reason:
                    blocked.append(reason)
                    continue
                try:
                    jobs = self.ports.queue().enqueue(
                        [Path(path)], replace(self.ports.settings()), automatic=True
                    )
                    for job in jobs:
                        if job["fingerprint"] == ready[path]:
                            selected.append(job["id"])
                        else:
                            self.ports.queue().remove(job["id"])
                except (OSError, ValueError):
                    # Removal/replacement after the snapshot will be reconsidered next scan.
                    continue
        if removed:
            for job in snapshot:
                if job.get("origin") == "media-inbox" and job["media_path"] in removed:
                    if job["status"] == "running":
                        self.ports.queue().pause()
                    elif job["status"] in ("queued", "paused"):
                        self.ports.queue().remove(job["id"])
        if added or removed:
            self.ports.refresh_library()
        waiting = len(result["waiting"])
        status = (
            f"等待 {waiting} 个文件复制完成"
            if waiting
            else "媒体文件夹自动准备已开启"
            if enabled
            else "媒体文件夹已同步"
        )
        if self.ports.settings().auto_prepare_media and self.state.inbox_paused:
            blocked.append("媒体文件夹自动准备已暂停。开始队列或重新打开自动准备即可继续。")
        self.ports.set_inbox_state(
            scanning=False, waiting=waiting, ready=len(ready), blocked=next(iter(blocked), ""), status=status
        )
        if selected and "queue" not in self.ports.task_keys():
            self.ports.start_job(
                "queue",
                lambda progress, cancelled: self.ports.queue().run(
                    progress,
                    cancelled,
                    on_error=lambda exc: self.ports.log.exception(exc, "queue_error"),
                    job_ids=selected,
                    allow_model_downloads=False,
                ),
                lambda result: self.ports.set_state(status="自动准备已结束；可在媒体库和队列查看结果"),
            )
        if selected or removed:
            self.ports.refresh_jobs()

    def _publish_library(self, paths, language, summaries=None):
        jobs = {j["media_path"]: j for j in self.ports.queue().snapshot()}
        rows = []
        for media in paths:
            summary = (
                (summaries or {}).get(media)
                or self.state.library_cache.peek(media, language)
                or {"status": "unprepared", "cueCount": 0}
            )
            job = jobs.get(media)
            status, count = summary["status"], summary["cueCount"]
            if job and job["status"] != "completed" and status != "missing":
                status = job["status"]
                count = job.get("total_cues", 0) or count
            rows.append(
                {
                    "path": media,
                    "name": Path(media).name,
                    "parent": str(Path(media).parent),
                    "status": status,
                    "progress": job.get("progress", "") if job else "",
                    "cueCount": count,
                    "position": self.state.library_positions.get(media, 0),
                }
            )
        if rows != self.state.library:
            self.state.library = rows
            self.ports.libraryChanged.emit()

    def _refresh_library(self):
        if self.ports.is_closed():
            return
        paths, language = tuple(self.state.catalog.paths), deepcopy(self.ports.settings().language)
        self.ports.publish_library(paths, language)
        self.state.library_generation += 1
        request = (self.state.library_generation, paths, language)
        if self.state.library_future is not None:
            self.state.library_pending = request
        else:
            self.ports.start_library_read(request)

    def _start_library_read(self, request):
        generation, paths, language = request

        def read():
            try:
                summaries = {}
                for media in paths:
                    if self.ports.is_closed():
                        return
                    summaries[media] = self.state.library_cache.get(media, language)
                result = {
                    "generation": generation,
                    "paths": paths,
                    "language": language,
                    "summaries": summaries,
                    "positions": self.ports.store.load_positions(paths),
                }
            except Exception as exc:
                self.ports.log.exception(exc, "library_read_error")
                result = {"generation": generation, "failure": True}
            if not self.ports.is_closed():
                self.ports.libraryCompleted.emit(result)

        self.state.library_future = self.state.library_executor.submit(read)

    def _library_completed(self, result):
        self.state.library_future = None
        if self.ports.is_closed():
            return
        if result["generation"] == self.state.library_generation and not result.get("failure"):
            self.state.library_positions = result["positions"]
            self.ports.publish_library(result["paths"], result["language"], result["summaries"])
        pending, self.state.library_pending = self.state.library_pending, None
        if pending is not None:
            self.ports.start_library_read(pending)

    def _set_inbox_state(self, **values):
        inbox = {**self.ports.mediaInbox(), **values}
        if inbox != self.ports.mediaInbox():
            self.ports.state_snapshot()["mediaInbox"] = inbox
            self.ports.mediaInboxChanged.emit()

    def command_openMedia(self, name, p):
        self.ports.open_media(p["path"])

    def command_addPaths(self, name, p):
        self.ports.add_paths(p.get("paths", []))

    def command_scanWorkspaceMedia(self, name, p):
        self.ports.scan_media_inbox()

    def command_removeMedia(self, name, p):
        self.state.catalog.remove(p.get("paths", []))
        self.ports.refresh_library()
