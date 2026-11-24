"""Main-thread QML facade with independent, cancellable background jobs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
import platform
import re
import sqlite3
import sys
import threading

from PySide6.QtCore import QObject, Property, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from tingmu import __version__
from tingmu.batch import PreparationQueue
from tingmu.diagnostics import AppLog, media_id, safe_fields
from tingmu.drafts import DraftStorage
from tingmu.export_paths import export_path
from tingmu.library import MediaLibrary
from tingmu.media_inbox import InboxScanner
from tingmu.media import load_subtitles
from tingmu.models import MAX_LLM_CONCURRENCY, StudyDocument
from tingmu.storage import Store, atomic_json, canonical_path, word_key
from tingmu.timeline import SubtitleTimeline
from .embedded import EmbeddedCaptions
from .feature_services import initialize_feature_state, install_feature_facade
from .controller_state import state_field
from .settings_controller import SettingsController, SettingsState, SettingsPorts
from .collections_controller import CollectionsController, CollectionsState, CollectionsPorts
from .dialogs import QtDialogs, DialogService, DialogPorts
from .playback_controller import PlaybackController, PlaybackState, PlaybackPorts
from .library_controller import LibraryController, LibraryState, LibraryPorts
from .queue_controller import QueueController, QueueState, QueuePorts
from .tasks import BackgroundTaskManager, TaskState, TaskPorts
from .localization import ui_text
from .playback_sources import PlaybackSources
from .state import DomainStates, command_domain
from .list_models import StableListModel
from .library_summary import LibrarySummaryCache


def cue_map(cue):
    if cue is None:
        return {}
    value = asdict(cue)
    value["reading"] = " · ".join(t.reading for t in cue.tokens if t.reading)
    return value


class _FutureActivity:
    """Legacy scanner activity view; execution uses one reusable pool thread."""
    def __init__(self, future):
        self.future = future

    def is_alive(self):
        return not self.future.done()


class Bridge(QObject):
    stateChanged = Signal()
    mediaStateChanged = Signal()
    queueStateChanged = Signal()
    chatStateChanged = Signal()
    downloadStateChanged = Signal()
    learningStateChanged = Signal()
    modelStateChanged = Signal()
    workspaceStateChanged = Signal()
    applicationStateChanged = Signal()
    notificationStateChanged = Signal()
    workersChanged = Signal()
    _queueChanged = Signal()
    settingsChanged = Signal()
    lessonModelChanged = Signal()
    playbackChanged = Signal()
    playbackDetailsChanged = Signal()
    cuesChanged = Signal()
    activeCueChanged = Signal()
    libraryChanged = Signal()
    jobsChanged = Signal()
    messagesChanged = Signal()
    entriesChanged = Signal()
    collectionFeedback = Signal(bool, str)
    layoutChanged = Signal()
    dialogRequested = Signal(str, QObject, "QVariantMap")
    diagnosticsChanged = Signal()
    logsChanged = Signal()
    modelsChanged = Signal()
    notebooksChanged = Signal()
    chatSessionsChanged = Signal()
    _completed = Signal(str, object, str)
    _progress = Signal(str, object)
    _chatDelta = Signal(str, str, str)
    _chatPresentation = Signal(str, str, object)
    _learningUpdated = Signal()
    _appActionRequested = Signal(object)
    _chatActionProgress = Signal(str, str, object)
    appAction = Signal(str, "QVariantMap")
    focusWindowChanged = Signal()
    _inboxCompleted = Signal(object)
    _libraryCompleted = Signal(object)
    mediaInboxChanged = Signal()

    _player = state_field("_playback_state", "player")
    audio = state_field("_playback_state", "audio")
    _sources = state_field("_playback_state", "sources")
    _embedded = state_field("_playback_state", "embedded")
    _restore_embedded_pending = state_field("_playback_state", "restore_embedded_pending")
    document = state_field("_playback_state", "document")
    media_path = state_field("_playback_state", "media_path")
    timeline = state_field("_playback_state", "timeline")
    _cues = state_field("_playback_state", "cues")
    _source_cues = state_field("_playback_state", "source_cues")
    _cue_indices = state_field("_playback_state", "cue_indices")
    _active_cue = state_field("_playback_state", "active_cue")
    _active_index = state_field("_playback_state", "active_index")
    _playback_details = state_field("_playback_state", "playback_details")
    _playback_position = state_field("_playback_state", "playback_position")
    _resume_position = state_field("_playback_state", "resume_position")
    _resume_seek_queued = state_field("_playback_state", "resume_seek_queued")
    _loop_a = state_field("_playback_state", "loop_a")
    _loop_b = state_field("_playback_state", "loop_b")
    _position_writer = state_field("_playback_state", "position_writer")
    _position_future = state_field("_playback_state", "position_future")
    _last_position_request = state_field("_playback_state", "last_position_request")
    position_timer = state_field("_playback_state", "position_timer")
    catalog = state_field("_library_state", "catalog")
    _library = state_field("_library_state", "library")
    _library_cache = state_field("_library_state", "library_cache")
    _library_positions = state_field("_library_state", "library_positions")
    _library_generation = state_field("_library_state", "library_generation")
    _library_future = state_field("_library_state", "library_future")
    _library_pending = state_field("_library_state", "library_pending")
    _library_executor = state_field("_library_state", "library_executor")
    _inbox_scanner = state_field("_library_state", "inbox_scanner")
    _inbox_thread = state_field("_library_state", "inbox_thread")
    _inbox_executor = state_field("_library_state", "inbox_executor")
    _inbox_stop = state_field("_library_state", "inbox_stop")
    _inbox_paused = state_field("_library_state", "inbox_paused")
    _media_inbox = state_field("_library_state", "media_inbox")
    inbox_timer = state_field("_library_state", "inbox_timer")
    queue = state_field("_queue_state", "queue")
    _jobs = state_field("_queue_state", "jobs")
    _last_queue_states = state_field("_queue_state", "last_queue_states")
    timer = state_field("_queue_state", "timer")
    _unsubscribe_queue = state_field("_queue_state", "unsubscribe_queue")
    _workers = state_field("_tasks_state", "workers")
    _finishing_workers = state_field("_tasks_state", "finishing_workers")
    _callbacks = state_field("_tasks_state", "callbacks")
    _closed = state_field("_tasks_state", "closed")

    _lesson_model_status = state_field("_settings_state", "lesson_model_status")
    _entries = state_field("_collections_state", "entries")
    _entries_revision = state_field("_collections_state", "entries_revision")
    _note_media_path = state_field("_collections_state", "note_media_path")
    _cue_notes = state_field("_collections_state", "cue_notes")
    _saved_sentences = state_field("_collections_state", "saved_sentences")
    _word_states_scope = state_field("_collections_state", "word_states_scope")
    _word_states = state_field("_collections_state", "word_states")

    def __init__(self, store: Store, *, dialogs=None):
        super().__init__()
        initialize_feature_state(self)
        self._settings_state = SettingsState()
        self._collections_state = CollectionsState()
        self._playback_state = PlaybackState()
        self._library_state = LibraryState()
        self._queue_state = QueueState()
        self._tasks_state = TaskState()
        QGuiApplication.instance().focusWindowChanged.connect(self._focus_window_changed)
        self.store = store
        self.native_dialogs = dialogs or QtDialogs(QFileDialog, QInputDialog, QMessageBox)
        self._lesson_model_status = None
        self.settingsChanged.connect(self._invalidate_lesson_model)
        self.modelsChanged.connect(self._invalidate_lesson_model)
        self._settings = store.load_settings()
        self.log = AppLog(store.data_dir, self._settings.developer_mode)
        self.log.event("app_start", "应用启动", version=__version__, frontend="Qt Quick/QML")
        self._diagnostics = {}
        self._logs = []
        self._last_queue_states = {}
        self._layout_path = store.data_dir / "quick-layout.json"
        try:
            self._layout = json.loads(self._layout_path.read_text("utf-8"))
        except (OSError, ValueError):
            self._layout = {}
        if not isinstance(self._layout, dict):
            self._layout = {}
        self.drafts = DraftStorage(store)
        draft_migration_error = None
        try:
            migrated = self.drafts.migrate_layout(self._layout)
            if migrated != self._layout:
                atomic_json(self._layout_path, migrated)
                self._layout = migrated
        except (OSError, ValueError, sqlite3.Error) as error:
            self.log.exception(error, "draft_migration_error")
            draft_migration_error = "未能迁移旧草稿；原布局与现有草稿已保留。"
        # v2's factory 180 ms setting left visible blank flashes in dialogue.
        if not self._layout and self._settings.subtitle_gap_ms == 180:
            self._settings.subtitle_gap_ms = 500
        self.catalog = MediaLibrary(store)
        self.queue = PreparationQueue(store)
        self._player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.7)
        self._player.setAudioOutput(self.audio)
        self._sources = PlaybackSources(store, self._player, self.audio, self)
        self._restore_embedded_pending = False
        self._embedded = EmbeddedCaptions(self)
        self._embedded.changed.connect(self._update_playback)
        self.document = None
        self.media_path = ""
        self.timeline = SubtitleTimeline([])
        self._cues = []
        self._active_cue = None
        self._active_index = -1
        self._library = []
        self._library_cache = LibrarySummaryCache(store)
        self._library_positions = {}
        self._library_generation = 0
        self._library_future = None
        self._library_pending = None
        self._library_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tingmu-library")
        self._libraryCompleted.connect(self._library_completed)
        self._jobs = []
        self._messages = store.load_conversation("", self._settings.language)
        self._entries = []
        self._entries_revision = 0
        self._note_media_path = ""
        self._cue_notes = {}
        self._saved_sentences = set()
        self._word_states_scope = None
        self._word_states = {}
        self._playback_details = {}
        self._playback_position = 0
        self._state = {"mediaPath": "", "mediaName": "", "status": "", "error": "",
                       "busy": False, "queueRunning": False, "chatBusy": False,
                       "totalCount": 0, "jobCount": 0, "hasPending": False,
                       "failedCount": 0, "runningMediaPaths": [], "activePhase": "", "activeStatus": "",
                       "cloudEnabled": self._settings.cloud_text_enabled,
                       "dataDir": str(store.data_dir), "version": __version__,
                       "subtitleSource": "", "reviewModel": "", "reviewSummary": ""}
        self._domain_states = DomainStates(self._state)
        self._operation_domain = "notification"
        self._message_model = StableListModel(self)
        self._job_model = StableListModel(self)
        self.messagesChanged.connect(self._sync_message_view)
        self.jobsChanged.connect(self._sync_job_view)
        self._workers = {}
        self._finishing_workers = []
        self._callbacks = {}
        self._closed = False
        self._resume_position = None
        self._loop_a = -1
        self._loop_b = -1
        self._completed.connect(self._job_completed)
        self._progress.connect(self._job_progress)
        self._player.positionChanged.connect(self._position_changed)
        self._player.durationChanged.connect(self._duration_changed)
        self._player.seekableChanged.connect(lambda ready: self._duration_changed(self._player.duration()) if ready else None)
        self._player.playbackStateChanged.connect(self._update_playback)
        self._player.playbackRateChanged.connect(self._update_playback)
        self.audio.volumeChanged.connect(self._update_playback)
        self.audio.mutedChanged.connect(self._update_playback)
        self._player.tracksChanged.connect(self._update_playback)
        self._player.tracksChanged.connect(self._restore_embedded_choice)
        self._player.activeTracksChanged.connect(self._update_playback)
        self._player.mediaStatusChanged.connect(self._media_status)
        self._player.errorOccurred.connect(self._media_error)
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self._refresh_jobs)
        self.timer.setSingleShot(True)
        self._queueChanged.connect(self._schedule_queue_refresh, Qt.ConnectionType.QueuedConnection)
        self._unsubscribe_queue = self.queue.subscribe(self._queueChanged.emit)
        self._position_writer = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tingmu-position")
        self._position_future = None
        self._last_position_request = None
        self.position_timer = QTimer(self)
        self.position_timer.setInterval(3000)
        self.position_timer.timeout.connect(self._checkpoint_position)
        self.diagnostic_timer = QTimer(self)
        self.diagnostic_timer.setInterval(2000)
        self.diagnostic_timer.timeout.connect(self._refresh_diagnostics_if_enabled)
        if self._settings.developer_mode:
            self.diagnostic_timer.start()
        self._playback_controller = PlaybackController(
            self._playback_state, PlaybackPorts(
                task_keys=lambda *args, **kwargs: self.task_keys(*args, **kwargs),
                set_audio_output=lambda *args, **kwargs: self._set_audio_output(*args, **kwargs),
                    navigate_cue=lambda *args, **kwargs: self._navigate_cue(*args, **kwargs),
                activeCueChanged=self.activeCueChanged,
                adjacent_media=lambda *args, **kwargs: self._adjacent_media(*args, **kwargs),
                catalog=self.catalog,
                cuesChanged=self.cuesChanged,
                error=lambda *args, **kwargs: self._error(*args, **kwargs),
                finish_position_checkpoint=lambda *args, **kwargs: self._finish_position_checkpoint(*args, **kwargs),
                is_closed=lambda: self._closed,
                layout=lambda: self._layout,
                load_document=lambda *args, **kwargs: self._load_document(*args, **kwargs),
                load_personal=lambda *args, **kwargs: self._load_personal(*args, **kwargs),
                log=self.log,
                open_media=lambda *args, **kwargs: self.open_media(*args, **kwargs),
                playbackChanged=self.playbackChanged,
                playbackDetailsChanged=self.playbackDetailsChanged,
                present_cue=lambda *args, **kwargs: self._present_cue(*args, **kwargs),
                refresh_library=lambda *args, **kwargs: self._refresh_library(*args, **kwargs),
                restore_embedded_choice=lambda *args, **kwargs: self._restore_embedded_choice(*args, **kwargs),
                save_position=lambda *args, **kwargs: self.save_position(*args, **kwargs),
                schedule_resume_seek=lambda *args, **kwargs: self._schedule_resume_seek(*args, **kwargs),
                seek=lambda *args, **kwargs: self._seek(*args, **kwargs),
                set_document=lambda *args, **kwargs: self._set_document(*args, **kwargs),
                set_state=lambda *args, **kwargs: self._set_state(*args, **kwargs),
                settings=lambda: self._settings,
                settingsChanged=self.settingsChanged,
                store=self.store,
                sync_cue=lambda *args, **kwargs: self._sync_cue(*args, **kwargs),
                tracks=lambda *args, **kwargs: self._tracks(*args, **kwargs),
                update_playback=lambda *args, **kwargs: self._update_playback(*args, **kwargs),
            ))
        self._library_controller = LibraryController(
            self._library_state, LibraryPorts(
                scan_media_inbox=lambda *args, **kwargs: self._scan_media_inbox(*args, **kwargs),
                open_media=lambda *args, **kwargs: self.open_media(*args, **kwargs),
                add_paths=lambda *args, **kwargs: self.add_paths(*args, **kwargs),
                inboxCompleted=self._inboxCompleted,
                is_closed=lambda: self._closed,
                libraryChanged=self.libraryChanged,
                libraryCompleted=self._libraryCompleted,
                log=self.log,
                mediaInbox=lambda: self.mediaInbox,
                mediaInboxChanged=self.mediaInboxChanged,
                publish_library=lambda *args, **kwargs: self._publish_library(*args, **kwargs),
                queue=lambda: self.queue,
                refresh_jobs=lambda *args, **kwargs: self._refresh_jobs(*args, **kwargs),
                refresh_library=lambda *args, **kwargs: self._refresh_library(*args, **kwargs),
                set_inbox_state=lambda *args, **kwargs: self._set_inbox_state(*args, **kwargs),
                set_state=lambda *args, **kwargs: self._set_state(*args, **kwargs),
                settings=lambda: self._settings,
                start_job=lambda *args, **kwargs: self._start_job(*args, **kwargs),
                start_library_read=lambda *args, **kwargs: self._start_library_read(*args, **kwargs),
                state_snapshot=lambda: self._state,
                store=self.store,
                task_keys=lambda: self._workers,
            ))
        self._queue_controller = QueueController(
            self._queue_state, QueuePorts(
                start_queue=lambda *args, **kwargs: self._start_queue(*args, **kwargs),
                read_lesson_model_status=lambda *args, **kwargs: self._read_lesson_model_status(*args, **kwargs),
                enqueue=lambda *args, **kwargs: self._enqueue(*args, **kwargs),
                document=lambda: self.document,
                cancel_task=lambda *args, **kwargs: self.cancel_task(*args, **kwargs),
                appAction=self.appAction,
                add_paths=lambda *args, **kwargs: self.add_paths(*args, **kwargs),
                inbox_state=self._library_state,
                is_closed=lambda: self._closed,
                job_stage=lambda *args, **kwargs: self._job_stage(*args, **kwargs),
                jobsChanged=self.jobsChanged,
                library_cache=self._library_cache,
                library_rows=lambda: self._library,
                load_document=lambda *args, **kwargs: self._load_document(*args, **kwargs),
                log=self.log,
                media_path=lambda: self.media_path,
                publish_library_rows=self._publish_library_rows,
                refresh_jobs=lambda *args, **kwargs: self._refresh_jobs(*args, **kwargs),
                refresh_library=lambda *args, **kwargs: self._refresh_library(*args, **kwargs),
                resume_inbox=lambda *args, **kwargs: self._resume_inbox(*args, **kwargs),
                set_state=lambda *args, **kwargs: self._set_state(*args, **kwargs),
                settings=lambda: self._settings,
                start_job=lambda *args, **kwargs: self._start_job(*args, **kwargs),
                store=self.store,
                task_keys=lambda: self._workers,
            ))
        self._tasks_controller = BackgroundTaskManager(
            self._tasks_state, TaskPorts(
                clear_chat_worker=lambda: setattr(self, "_chat_worker_session_id", ""),
                completed=self._completed,
                error=lambda *args, **kwargs: self._error(*args, **kwargs),
                log=self.log,
                operation_domain=lambda: self._operation_domain,
                progress=self._progress,
                refresh_jobs=lambda *args, **kwargs: self._refresh_jobs(*args, **kwargs),
                set_operation_domain=lambda domain: setattr(self, "_operation_domain", domain),
                set_state=lambda *args, **kwargs: self._set_state(*args, **kwargs),
                state_snapshot=lambda: self._state,
                workersChanged=self.workersChanged,
            ))
        self.task_manager = self._tasks_controller
        self.playback_controller = self._playback_controller
        self.library_controller = self._library_controller
        self.queue_controller = self._queue_controller
        self._library_executor = self.task_manager.register_pool("library", self._library_executor)
        self._position_writer = self.task_manager.register_pool("position", self._position_writer)
        self.dialogs = DialogService(DialogPorts(
            add_paths=lambda *args, **kwargs: self.add_paths(*args, **kwargs),
            copy_media=lambda *args, **kwargs: self._copy_media(*args, **kwargs),
            cues=lambda: self._cues,
            dialogs=self.native_dialogs,
            document=lambda: self.document,
            enqueue=lambda *args, **kwargs: self._enqueue(*args, **kwargs),
            export_destination=lambda *args, **kwargs: self._export_destination(*args, **kwargs),
            import_subtitle=lambda *args, **kwargs: self._import_subtitle(*args, **kwargs),
            media_path=lambda: self.media_path,
            open_media=lambda *args, **kwargs: self.open_media(*args, **kwargs),
            set_state=lambda *args, **kwargs: self._set_state(*args, **kwargs),
            settings=lambda: self._settings,
            sources=self.sources,
            start_job=lambda *args, **kwargs: self._start_job(*args, **kwargs),
            store=self.store,
            timeline=lambda: self.timeline,
        ))
        self.settings_controller = SettingsController(self._settings_state, SettingsPorts(
            dialogs=self.native_dialogs, document=lambda: self.document,
            error=lambda *a, **kw: self._error(*a, **kw), lessonModelChanged=self.lessonModelChanged,
            load_document=lambda: self._load_document(), load_personal=lambda: self._load_personal(), log=self.log,
            model_registry=lambda: self.model_registry, player_position=lambda: self._player.position(),
            refresh_diagnostics=lambda: self.refresh_diagnostics(), refresh_library=lambda: self._refresh_library(),
            replace_settings=lambda value: setattr(self, "_settings", value), resume_inbox=lambda: self._resume_inbox(),
            set_audio_output=lambda value: self._set_audio_output(value),
            set_diagnostic_timer=lambda enabled: self.diagnostic_timer.start() if enabled else self.diagnostic_timer.stop(),
            set_document=lambda doc: self._set_document(doc), set_languages=lambda *a: self.set_languages(*a),
            set_state=lambda **kw: self._set_state(**kw), setting=lambda *a: self.setting(*a),
            settings=lambda: self._settings, settingsChanged=self.settingsChanged,
            start_job=lambda *a: self._start_job(*a), stop_model_jobs=lambda: self._stop_model_jobs(),
            store=self.store, sync_cue=lambda pos: self._sync_cue(pos), task_keys=self.task_keys))
        self.collections_controller = CollectionsController(self._collections_state, CollectionsPorts(
            settings=lambda: self._settings, media_path=lambda: self.media_path, store=lambda: self.store,
            notebook_manager=lambda: self.notebook_manager, find_cue=lambda ident: self._find_cue(ident),
            source_ids=lambda ident: self.timeline.source_ids(ident), source_cue=lambda ident: self._source_cues.get(ident),
            document=lambda: self.document, imported_cue=lambda: self.sources.importedCue,
            embedded_cue=lambda: self._embedded.cue, conversations=lambda: self.conversations,
            active_session_id=lambda: self._active_session_id, messages=lambda: self._messages,
            collect_word=lambda *a: self._collect_word(*a), refresh_notebooks=lambda: self._refresh_notebooks(),
            activate_context_session=lambda: self._activate_context_session(),
            send_chat=lambda *a, **kw: self._v4_send_chat(*a, **kw), command=lambda *a: self._command(*a),
            error=lambda *a, **kw: self._error(*a, **kw), error_text=lambda: self._state["error"],
            log_exception=self.log.exception, set_state=lambda **kw: self._set_state(**kw),
            entries_changed=self.entriesChanged.emit, collection_feedback=self.collectionFeedback.emit,
            word_key=lambda text, language: word_key(text, language)))
        self._feature_init()
        self._refresh_library()
        self._refresh_jobs()
        self._update_playback()
        self._inbox_scanner = InboxScanner(self.catalog.media_dir)
        self._inbox_thread = None
        self._inbox_executor = self.task_manager.pool("media-inbox")
        self._inbox_stop = threading.Event()
        self._inbox_paused = False
        self._inboxCompleted.connect(self._inbox_completed)
        self._set_state(mediaDir=str(self.catalog.media_dir), mediaInbox={
            "scanning": False, "waiting": 0, "ready": 0, "blocked": "", "status": "正在检查媒体文件夹"})
        self.inbox_timer = QTimer(self)
        self.inbox_timer.setInterval(2000)
        self.inbox_timer.timeout.connect(self._scan_media_inbox)
        self.inbox_timer.start()
        QTimer.singleShot(0, self._scan_media_inbox)
        if draft_migration_error:
            self._error(draft_migration_error, domain="learning")

    def _publish_library_rows(self, rows):
        self._library = rows
        self.libraryChanged.emit()

    @property
    def is_closed(self):
        return self._closed

    @property
    def task_progress(self):
        return self._progress

    def settings_snapshot(self):
        return deepcopy(self._settings)

    def apply_settings_snapshot(self, settings):
        self._settings = deepcopy(settings)
        self.settingsChanged.emit()

    def task_keys(self):
        return frozenset(self._workers)

    def start_task(self, key, function, completed):
        return self._start_job(key, function, completed)

    def cancel_task(self, key):
        return self.task_manager.cancel(key)

    def reload_document(self):
        return self._load_document()

    def refresh_library(self):
        return self._refresh_library()

    def resume_media_inbox(self):
        return self._resume_inbox()

    def scan_workspace_media(self):
        return self._scan_media_inbox()

    def publish_state(self, *, domain="workspace", **values):
        return self._set_state(domain=domain, **values)

    def report_error(self, message, *, domain="notification"):
        return self._error(message, domain=domain)

    def _sync_message_view(self):
        self._message_model.sync(self._message_markdown.display_view(self._messages))
        pending = next((item for item in reversed(self._messages)
                        if item.get("role") == "assistant"
                        and item.get("status") in ("pending", "streaming")), {})
        self._set_state(activePhase=pending.get("phase", ""),
                        activeStatus=pending.get("status", ""))

    def _sync_job_view(self):
        self._job_model.sync(self._jobs)
        self._set_state(totalCount=len(self._jobs), jobCount=len(self._jobs),
                        hasPending=any(j["status"] in ("queued", "paused") for j in self._jobs),
                        failedCount=sum(j["status"] == "failed" for j in self._jobs),
                        runningMediaPaths=list(dict.fromkeys(j.get("path") or j.get("media_path") for j in self._jobs
                                                            if j.get("status") == "running"
                                                            and (j.get("path") or j.get("media_path")))))

    def _focus_window_changed(self, *_):
        self.focusWindowChanged.emit()

    @Property(QObject, notify=focusWindowChanged)
    def focusWindow(self):
        return QGuiApplication.focusWindow()

    @Property(QObject, constant=True)
    def player(self):
        return self._player

    @Property(QObject, constant=True)
    def sources(self):
        return self._sources

    @Property("QVariantMap", notify=stateChanged)
    def state(self):
        return self._state

    @Property(QObject, constant=True)
    def messageModel(self):
        return self._message_model

    @Property(QObject, constant=True)
    def jobModel(self):
        return self._job_model

    @Property("QVariantMap", notify=mediaStateChanged)
    def mediaState(self):
        return self._domain_states.snapshot("media")

    @Property("QVariantMap", notify=queueStateChanged)
    def queueState(self):
        return self._domain_states.snapshot("queue")

    @Property("QVariantMap", notify=chatStateChanged)
    def chatState(self):
        return self._domain_states.snapshot("chat")

    @Property("QVariantMap", notify=downloadStateChanged)
    def downloadState(self):
        return self._domain_states.snapshot("download")

    @Property("QVariantMap", notify=learningStateChanged)
    def learningState(self):
        return self._domain_states.snapshot("learning")

    @Property("QVariantMap", notify=modelStateChanged)
    def modelState(self):
        return self._domain_states.snapshot("models")

    @Property("QVariantMap", notify=workspaceStateChanged)
    def workspaceState(self):
        return self._domain_states.snapshot("workspace")

    @Property("QVariantMap", notify=applicationStateChanged)
    def applicationState(self):
        return self._domain_states.snapshot("application")

    @Property("QVariantMap", notify=notificationStateChanged)
    def notificationState(self):
        return self._domain_states.snapshot("notification")

    @Property("QVariantMap", notify=mediaInboxChanged)
    def mediaInbox(self):
        return self._state.get("mediaInbox", {})

    def _set_inbox_state(self, **values):
        return self._library_controller._set_inbox_state(**values)

    @Property("QVariantMap", notify=settingsChanged)
    def settings(self):
        return self._settings.to_dict()

    @Property(int, constant=True)
    def llmConcurrencyLimit(self):
        return MAX_LLM_CONCURRENCY

    @Property("QVariantMap", notify=lessonModelChanged)
    def lessonModel(self):
        if self._lesson_model_status is None:
            self._lesson_model_status = self._read_lesson_model_status()
        return self._lesson_model_status

    def _invalidate_lesson_model(self):
        return self.settings_controller._invalidate_lesson_model()

    def _read_lesson_model_status(self):
        return self.settings_controller._read_lesson_model_status()

    @Property("QVariantMap", notify=settingsChanged)
    def speechModels(self):
        from tingmu.runtime import cache_root
        from tingmu.speech import speech_model_status
        return speech_model_status(cache_root())

    @Slot(str, result=bool)
    def saveSpeechKey(self, secret):
        return self.settings_controller.saveSpeechKey(secret)

    @Slot()
    def chooseWhisperFolder(self):
        return self.settings_controller.chooseWhisperFolder()

    @Slot()
    def checkSpeechModels(self):
        return self.settings_controller.checkSpeechModels()

    def _export_destination(self, provided, default_name, suffixes):
        if provided:
            return export_path(self.store.data_dir, provided, suffixes)
        name, ok = self.native_dialogs.getText(None, ui_text("导出到当前工作区"),
                                        ui_text("文件名（%1）").replace("%1", ", ".join(suffixes)), text=default_name)
        if not ok or not name.strip():
            return None
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("请输入文件名，文件将保存到当前工作区的 exports 文件夹。")
        destination = export_path(self.store.data_dir, name.strip(), suffixes)
        if destination.exists() and self.native_dialogs.question(None, ui_text("覆盖导出文件"),
                ui_text("覆盖 %1？").replace("%1", destination.name)) != QMessageBox.Yes:
            return None
        return destination

    @Property("QVariantMap", notify=playbackChanged)
    def playback(self):
        return {**self._playback_details, "position": self._playback_position}

    @Property("qlonglong", notify=playbackChanged)
    def playbackPosition(self):
        return self._playback_position

    @Property("QVariantMap", notify=playbackDetailsChanged)
    def playbackDetails(self):
        return self._playback_details

    @Property("QVariantList", notify=cuesChanged)
    def cues(self):
        return self._cues

    @Property(int, notify=cuesChanged)
    def cueCount(self):
        return len(self._cues)

    @Property("QVariantMap", notify=activeCueChanged)
    def activeCue(self):
        return self._cues[self._active_index] if self._active_index >= 0 else {}

    @Property(int, notify=activeCueChanged)
    def activeIndex(self):
        return self._active_index

    @Property("QVariantList", notify=libraryChanged)
    def library(self):
        return self._library

    @Property("QVariantList", notify=jobsChanged)
    def jobs(self):
        return self._jobs

    @Property("QVariantList", notify=messagesChanged)
    def messages(self):
        return self._display_messages()

    @Property("QVariantList", notify=modelsChanged)
    def modelProfiles(self):
        return self._model_profiles

    @Property("QVariantList", notify=modelsChanged)
    def modelPresets(self):
        return self._model_presets

    @Property("QVariantList", notify=modelsChanged)
    def discoveredModels(self):
        return self._discovered_models

    @Property(str, notify=modelsChanged)
    def modelStatus(self):
        return self._model_status

    @Property("QVariantMap", notify=notebooksChanged)
    def notebooks(self):
        return self._notebooks

    @Property("QVariantList", notify=chatSessionsChanged)
    def chatSessions(self):
        return self._chat_sessions

    @Property(str, notify=chatSessionsChanged)
    def activeSessionId(self):
        return self._active_session_id

    @Property("QVariantList", notify=entriesChanged)
    def entries(self):
        return self._entries

    @Property(int, notify=entriesChanged)
    def entryCount(self):
        return len(self._entries)

    @Property(int, notify=entriesChanged)
    def entriesRevision(self):
        return self._entries_revision

    def _refresh_entries(self):
        return self.collections_controller._refresh_entries()

    @Slot(str, result=str)
    def cueNote(self, cue_id):
        return self.collections_controller.cueNote(cue_id)

    @Slot(str, str, result=bool)
    def sentenceCollected(self, cue_id, sentence):
        return self.collections_controller.sentenceCollected(cue_id, sentence)

    @Property("QVariantMap", notify=layoutChanged)
    def layout(self):
        return self._layout

    @Property("QVariantMap", notify=diagnosticsChanged)
    def diagnostics(self):
        return self._diagnostics

    @Property("QVariantList", notify=logsChanged)
    def logs(self):
        return self._logs

    def _refresh_diagnostics_if_enabled(self):
        if self._settings.developer_mode:
            self.refresh_diagnostics()

    def refresh_diagnostics(self):
        from tingmu.external_asr import api_options
        counts = {}
        for job in self.queue.snapshot():
            counts[job["status"]] = counts.get(job["status"], 0) + 1
        value = {"version": __version__, "frontend": "Qt Quick/QML", "dataDir": str(self.store.data_dir),
                 "logDir": str(self.log.directory), "python": sys.version.split()[0], "platform": platform.platform(),
                 "media": {"loaded": bool(self.media_path), "id": media_id(self.media_path),
                           "cues": len(self._cues), "position": self._player.position(), "duration": self._player.duration()},
                 "queue": {"running": "queue" in self._workers, "counts": counts},
                 "models": {"asr": (self._settings.asr_model if self._settings.asr_backend == "local"
                                    else api_options(self._settings)[1]), "review": self._settings.review_model,
                            "teaching": self._settings.model, "companion": self._settings.companion_model,
                            "device": self._settings.device},
                 "privacy": {"cloudText": self._settings.text_models_enabled and self._settings.cloud_text_enabled,
                             "asrBackend": self._settings.asr_backend, "audioUpload": self._settings.allow_audio_upload,
                             "logsIncludeContent": False},
                 "logLevel": "DEBUG" if self._settings.developer_mode else "INFO"}
        value = safe_fields(value)
        if value != self._diagnostics:
            self._diagnostics = value
            self.diagnosticsChanged.emit()
        rows = self.log.read()
        if rows != self._logs:
            self._logs = rows
            self.logsChanged.emit()

    def export_diagnostics(self):
        self.refresh_diagnostics()
        path = self.store.data_dir / "diagnostics" / (datetime.now().strftime("diagnostics-%Y%m%d-%H%M%S") + ".json")
        atomic_json(path, {"runtime": self._diagnostics, "events": self._logs})
        self.log.event("diagnostics_export", "诊断文件已导出")
        self._set_state(status=f"诊断已导出：{path}")
        return path

    def _set_state(self, *, domain=None, **values):
        changed = self._domain_states.update(values, domain or self._operation_domain)
        if any(self._state.get(k) != v for k, v in values.items()):
            self._state.update(deepcopy(values))
            self.stateChanged.emit()
        for name in changed:
            getattr(self, ("model" if name == "models" else name) + "StateChanged").emit()

    def _error(self, message, *, domain=None):
        # Never forward provider credentials accidentally included in transport errors.
        text = re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", str(message))[:700]
        self._set_state(domain=domain, error=text, status=text)
        self.log.event("action_error", "操作失败，详情显示在界面", "ERROR")

    @Slot(QObject)
    def attachVideo(self, sink):
        return self._playback_controller.attachVideo(sink)

    @Slot()
    def refreshVideo(self):
        return self._playback_controller.refreshVideo()

    @Slot(str, QObject, "QVariantMap")
    def requestDialog(self, name, origin, payload):
        """Route trusted UI requests to the session's single dialog host."""
        if name in {"models", "teaching", "correction"}:
            self.dialogRequested.emit(name, origin, dict(payload))

    @Slot(str, result="QVariantMap")
    def loadDrafts(self, kind):
        try:
            return self.drafts.load(kind)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.log.exception(error, "draft_read_error")
            self._error("未能读取草稿；现有草稿已保留，请稍后重试。", domain="learning")
            return {}

    @Slot(str, str, "QVariant", result=bool)
    def writeDraft(self, kind, key, value):
        try:
            if hasattr(value, "toVariant"):
                value = value.toVariant()
            self.drafts.write(kind, key, value)
            return True
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self._error(error, domain="learning")
            return False

    @Slot(str, str, result=bool)
    def deleteDraft(self, kind, key):
        try:
            self.drafts.delete(kind, key)
            return True
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
            self._error(error, domain="learning")
            return False

    @Slot("QVariantMap", result=bool)
    def saveLayout(self, value):
        # Layout is local visual state, never a code or file execution channel.
        try:
            clean = json.loads(json.dumps(value, allow_nan=False, ensure_ascii=False))
            clean = self.drafts.migrate_layout(clean)
            if len(json.dumps(clean, ensure_ascii=False).encode("utf-8")) > 64000:
                self._error("布局与偏好超过 64 KB，未保存；笔记草稿仍独立保留。")
                return False
            atomic_json(self._layout_path, clean)
        except (OSError, ValueError, sqlite3.Error) as error:
            self.log.exception(error, "layout_save_error")
            self._error("未能保存布局或迁移草稿；原布局与现有草稿已保留。", domain="learning")
            return False
        self._layout = clean
        self.layoutChanged.emit()
        return True

    @Slot(str, "QVariant")
    def setting(self, name, value):
        return self.settings_controller.setting(name, value)

    def set_languages(self, source, target):
        return self.settings_controller.set_languages(source, target)

    def add_paths(self, paths):
        return self._library_controller.add_paths(paths)

    def _copy_media(self, selected):
        return self._library_controller._copy_media(selected)

    def _scan_media_inbox(self):
        return self._library_controller._scan_media_inbox()

    @Slot(object)
    def _inbox_completed(self, result):
        return self._library_controller._inbox_completed(result)

    def _publish_library(self, paths, language, summaries=None):
        return self._library_controller._publish_library(paths, language, summaries)

    def _refresh_library(self):
        return self._library_controller._refresh_library()

    def _start_library_read(self, request):
        return self._library_controller._start_library_read(request)

    @Slot(object)
    def _library_completed(self, result):
        return self._library_controller._library_completed(result)

    def open_media(self, raw_path):
        return self._playback_controller.open_media(raw_path)

    def _load_document(self):
        return self._playback_controller._load_document()

    def _set_document(self, document):
        return self._playback_controller._set_document(document)

    def _present_cue(self, segment):
        return self._playback_controller._present_cue(segment)

    def _load_personal(self):
        self._activate_context_session()
        self._notebook_language = self._settings.target_language
        self._refresh_notebooks()
        self.messagesChanged.emit()

    def _sync_cue(self, position, force=False):
        return self._playback_controller._sync_cue(position, force)

    def _position_changed(self, position):
        return self._playback_controller._position_changed(position)

    def _duration_changed(self, duration):
        return self._playback_controller._duration_changed(duration)

    def _schedule_resume_seek(self):
        # FFmpeg reports duration and seekability while still LoadingMedia. A
        # synchronous seek at that point is discarded by the rest of its load.
        return self._playback_controller._schedule_resume_seek()

    def _media_status(self, status):
        return self._playback_controller._media_status(status)

    def _restore_embedded_choice(self):
        return self._playback_controller._restore_embedded_choice()

    def _media_error(self, code, text):
        return self._playback_controller._media_error(code, text)

    def _set_audio_output(self, identity):
        return self._playback_controller._set_audio_output(identity)

    def _tracks(self, items, *, numbered=False):
        return self._playback_controller._tracks(items, numbered=numbered)

    def _update_playback(self, *_):
        return self._playback_controller._update_playback(*_)

    def _finish_position_checkpoint(self):
        return self._playback_controller._finish_position_checkpoint()

    def _checkpoint_position(self):
        return self._playback_controller._checkpoint_position()

    def save_position(self):
        # Explicit pause, media switch and exit must persist the latest position
        # after any earlier checkpoint, even if a preparation job held the writer lock.
        return self._playback_controller.save_position()

    def _seek(self, position):
        return self._playback_controller._seek(position)

    def _adjacent_media(self, delta):
        return self._playback_controller._adjacent_media(delta)

    def _navigate_cue(self, delta):
        return self._playback_controller._navigate_cue(delta)

    @Slot()
    def _schedule_queue_refresh(self):
        return self._queue_controller._schedule_queue_refresh()

    def _refresh_jobs(self):
        return self._queue_controller._refresh_jobs()

    @staticmethod
    def _job_stage(job):
        return QueueController._job_stage(job)

    def _start_job(self, key, function, completed):
        return self._tasks_controller._start_job(key, function, completed)

    @Slot(str, object)
    def _job_progress(self, key, text):
        return self._tasks_controller._job_progress(key, text)

    @Slot(str, object, str)
    def _job_completed(self, key, value, error):
        return self._tasks_controller._job_completed(key, value, error)

    def _enqueue(self, paths, subtitle_paths=None):
        return self._queue_controller._enqueue(paths, subtitle_paths)

    def _start_queue(self):
        return self._queue_controller._start_queue()

    def _resume_inbox(self):
        return self._queue_controller._resume_inbox()

    def _find_cue(self, cue_id):
        return next((c for c in self.timeline.cues if c.id == cue_id), self._active_cue if not cue_id else None)

    @Slot(str, "QVariantList", result="QVariantMap")
    def wordCollectionStates(self, cue_id, texts):
        return self.collections_controller.wordCollectionStates(cue_id, texts)

    @Slot("QVariantMap", result=str)
    def collectWord(self, payload):
        return self.collections_controller.collectWord(payload)

    def _collect_word(self, name, payload):
        return self.collections_controller._collect_word(name, payload)

    def _collect_sentence(self, payload):
        return self.collections_controller._collect_sentence(payload)

    def _collection_saved(self, message):
        return self.collections_controller._collection_saved(message)

    def _frozen_subtitle(self, cue_id):
        return self.collections_controller._frozen_subtitle(cue_id)

    @Slot(str, result="QVariantMap")
    def captureSubtitle(self, cue_id):
        return self.collections_controller.captureSubtitle(cue_id)

    @Slot("QVariantMap", str, result="QVariantMap")
    def submitSubtitleCorrection(self, captured, guidance):
        return self.collections_controller.submitSubtitleCorrection(captured, guidance)

    def _send_chat(self, text):
        self._v4_send_chat(text)

    def _import_subtitle(self, path):
        if not self.media_path:
            raise ValueError("请先打开字幕对应的视频")
        if "queue" in self._workers:
            raise ValueError("请先暂停处理队列，再导入字幕")
        from tingmu.agent import annotate_local
        media, language = self.media_path, replace(self._settings.language)

        def work(progress, cancelled):
            cues = load_subtitles(Path(path))
            if not cues:
                raise ValueError("字幕文件中没有有效内容")
            doc = StudyDocument(media, language, annotate_local(cues, language),
                                metadata={"subtitle_path": canonical_path(path), "stage": "transcribed"})
            self.store.save_document(doc, replace_existing=True)
            return doc

        def completed(doc):
            self._library_cache.invalidate(media)
            if self.media_path == media and self._settings.language == language:
                self._set_document(doc)
            self._refresh_library()
            self._set_state(status=f"已导入 {len(doc.cues)} 条字幕")
        self._start_job("import", work, completed)

    def _summarize(self):
        self._organize_notebook()

    @Slot(str, "QVariantMap")
    def command(self, name, payload):
        previous_domain = self._operation_domain
        self._operation_domain = command_domain(name)
        from .commands import clears_status
        self._set_state(error="", **({"status": ""} if clears_status(name) else {}))
        try:
            self.log.event("ui_action", "界面操作", "DEBUG", action=name)
            self._command(name, payload or {})
        except Exception as exc:
            self.log.exception(exc, "command_error")
            self._error(exc)
            if name in ("saveSentence", "saveSelection", "saveWord"):
                self.collectionFeedback.emit(False, self._state["error"])
        finally:
            self._operation_domain = previous_domain

    @Slot(QObject, str, str)
    def formatMarkdown(self, document, accent, border):
        from .typography import format_document
        format_document(document, accent, border)

    @Slot(str, str, result=bool)
    def saveCueNote(self, cue_id, note):
        return self.collections_controller.saveCueNote(cue_id, note)

    def _command(self, name, p):
        from .commands import dispatch
        return dispatch(self, name, p)


    @Slot()
    def shutdown(self):
        if self._closed:
            return
        self.save_position()
        self.store.save_settings(self._settings)
        self.queue.pause()
        self._closed = True
        self._embedded.close()
        self.timer.stop()
        self._unsubscribe_queue()
        self.position_timer.stop()
        self.diagnostic_timer.stop()
        self.inbox_timer.stop()
        self._inbox_stop.set()
        self._player.stop()
        pending = self.task_manager.shutdown()
        self.task_manager.close_when_finished(self.store.close)
        self.log.event("app_stop", "应用关闭", pending_tasks=pending)
        self.log.close()


install_feature_facade(Bridge)
