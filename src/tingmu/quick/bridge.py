"""Main-thread QML facade with independent, cancellable background jobs."""

from __future__ import annotations

from dataclasses import asdict, fields, replace
from datetime import datetime
import json
import math
from pathlib import Path
import platform
import re
import sys
import threading
import time

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer, QVideoFrame
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from tingmu import __version__
from tingmu.batch import PreparationQueue
from tingmu.diagnostics import AppLog, media_id, safe_fields
from tingmu.export_paths import export_path
from tingmu.library import MediaLibrary
from tingmu.lesson_material import lesson_counts, translated_subtitles
from tingmu.locale_catalog import display_language
from tingmu.translation_review import translation_verified
from tingmu.media_inbox import InboxScanner, preparation_block_reason
from tingmu.media import MEDIA_EXTENSIONS, load_subtitles, save_srt
from tingmu.models import MAX_LLM_CONCURRENCY, AppSettings, StudyDocument, preparation_identity_settings
from tingmu.storage import Store, atomic_json, canonical_path
from tingmu.timeline import SubtitleTimeline
from tingmu.workspaces import language_code
from .embedded import EmbeddedCaptions
from .features import FeatureMixin
from .playback_sources import PlaybackSources


def cue_map(cue):
    if cue is None:
        return {}
    value = asdict(cue)
    value["reading"] = " · ".join(t.reading for t in cue.tokens if t.reading)
    return value


class Bridge(FeatureMixin, QObject):
    stateChanged = Signal()
    settingsChanged = Signal()
    lessonModelChanged = Signal()
    playbackChanged = Signal()
    cuesChanged = Signal()
    activeCueChanged = Signal()
    libraryChanged = Signal()
    jobsChanged = Signal()
    messagesChanged = Signal()
    entriesChanged = Signal()
    layoutChanged = Signal()
    diagnosticsChanged = Signal()
    logsChanged = Signal()
    modelsChanged = Signal()
    notebooksChanged = Signal()
    chatSessionsChanged = Signal()
    _completed = Signal(str, object, str)
    _progress = Signal(str, str)
    _chatDelta = Signal(str, str, str)
    _chatPresentation = Signal(str, str, object)
    _learningUpdated = Signal()
    _appActionRequested = Signal(object)
    _chatActionProgress = Signal(str, str, object)
    appAction = Signal(str, "QVariantMap")
    focusWindowChanged = Signal()
    _inboxCompleted = Signal(object)

    def __init__(self, store: Store):
        super().__init__()
        QGuiApplication.instance().focusWindowChanged.connect(self._focus_window_changed)
        self.store = store
        self.settingsChanged.connect(self.lessonModelChanged)
        self.modelsChanged.connect(self.lessonModelChanged)
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
        self._jobs = []
        self._messages = store.load_conversation("", self._settings.language)
        self._entries = []
        self._playback = {}
        self._state = {"mediaPath": "", "mediaName": "", "status": "", "error": "",
                       "busy": False, "queueRunning": False, "chatBusy": False,
                       "cloudEnabled": self._settings.cloud_text_enabled,
                       "dataDir": str(store.data_dir), "version": __version__,
                       "subtitleSource": "", "reviewModel": "", "reviewSummary": ""}
        self._workers = {}
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
        self._player.tracksChanged.connect(self._update_playback)
        self._player.tracksChanged.connect(self._restore_embedded_choice)
        self._player.activeTracksChanged.connect(self._update_playback)
        self._player.mediaStatusChanged.connect(self._media_status)
        self._player.errorOccurred.connect(self._media_error)
        self.timer = QTimer(self)
        self.timer.setInterval(400)
        self.timer.timeout.connect(self._refresh_jobs)
        self.timer.start()
        self.position_timer = QTimer(self)
        self.position_timer.setInterval(3000)
        self.position_timer.timeout.connect(self.save_position)
        self.position_timer.start()
        self.diagnostic_timer = QTimer(self)
        self.diagnostic_timer.setInterval(2000)
        self.diagnostic_timer.timeout.connect(self._refresh_diagnostics_if_enabled)
        self.diagnostic_timer.start()
        self._feature_init()
        self._refresh_library()
        self._refresh_jobs()
        self._update_playback()
        self._inbox_scanner = InboxScanner(self.catalog.media_dir)
        self._inbox_thread = None
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

    @Property("QVariantMap", notify=settingsChanged)
    def settings(self):
        return asdict(self._settings)

    @Property(int, constant=True)
    def llmConcurrencyLimit(self):
        return MAX_LLM_CONCURRENCY

    @Property("QVariantMap", notify=lessonModelChanged)
    def lessonModel(self):
        from tingmu.agent import provider_status
        from tingmu.providers import ProviderError
        settings = self._settings
        try:
            enabled, profile = self.model_registry.text_role(settings, "annotation")
            usable = enabled and ((profile.get("key_source") == "none" or profile.get("has_key", False))
                                  if profile else provider_status().get(settings.provider, {}).get("available", False))
        except ProviderError:
            usable = False
        return {"configured": usable, "language": display_language(settings.source_language)}

    @Property("QVariantMap", notify=settingsChanged)
    def speechModels(self):
        from tingmu.runtime import cache_root
        from tingmu.speech import speech_model_status
        return speech_model_status(cache_root())

    @Slot(str, result=bool)
    def saveSpeechKey(self, secret):
        from tingmu.external_asr import api_options, save_audio_key
        try:
            endpoint = api_options(self._settings)[0]
            save_audio_key(self.store.data_dir, endpoint, secret.strip(), local=self._settings.asr_backend == "local_api")
            self._set_state(status="已保存工作区语音密钥" if secret else "已清除工作区语音密钥", error="")
            return True
        except (OSError, RuntimeError, ValueError) as exc:
            self._error(exc)
            return False

    @Slot()
    def chooseWhisperFolder(self):
        from tingmu.local_whisper import model_reference
        from tingmu.runtime import cache_root, confined_path
        try:
            base = confined_path(cache_root(), "models")
            base.mkdir(exist_ok=True)
            folder = QFileDialog.getExistingDirectory(None, "选择共享缓存内的 CTranslate2 Whisper 模型", str(base))
            if folder:
                self.setting("asr_model", model_reference(folder))
        except (OSError, ValueError) as exc:
            self._error(exc)

    @Slot()
    def checkSpeechModels(self):
        from tingmu.external_asr import api_options, list_models
        settings = replace(self._settings)
        if settings.asr_backend not in ("local_api", "external") or "speech-models" in self._workers:
            return
        def complete(items):
            if (self._settings.asr_backend == settings.asr_backend
                    and api_options(settings)[0] == api_options(self._settings)[0]):
                self._set_state(speechModelChoices=items, status=f"服务返回 {len(items)} 个模型，可选择或手填 ID。")
        self._set_state(speechModelChoices=[], status="正在查询语音服务的模型列表…", error="")
        self._start_job("speech-models", lambda progress, cancelled: list_models(settings, self.store.data_dir), complete)

    def _export_destination(self, provided, default_name, suffixes):
        if provided:
            return export_path(self.store.data_dir, provided, suffixes)
        name, ok = QInputDialog.getText(None, "导出到当前工作区", f"文件名（{'、'.join(suffixes)}）", text=default_name)
        if not ok or not name.strip():
            return None
        if Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError("请输入文件名，文件将保存到当前工作区的 exports 文件夹。")
        destination = export_path(self.store.data_dir, name.strip(), suffixes)
        if destination.exists() and QMessageBox.question(None, "覆盖导出文件", f"覆盖 {destination.name}？") != QMessageBox.Yes:
            return None
        return destination

    @Property("QVariantMap", notify=playbackChanged)
    def playback(self):
        return self._playback

    @Property("QVariantList", notify=cuesChanged)
    def cues(self):
        return self._cues

    @Property("QVariantMap", notify=activeCueChanged)
    def activeCue(self):
        return self._present_cue(self._active_cue)

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

    def _set_state(self, **values):
        if any(self._state.get(k) != v for k, v in values.items()):
            self._state.update(values)
            self.stateChanged.emit()

    def _error(self, message):
        # Never forward provider credentials accidentally included in transport errors.
        text = re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", str(message))[:700]
        self._set_state(error=text, status=text)
        self.log.event("action_error", "操作失败，详情显示在界面", "ERROR")

    @Slot(QObject)
    def attachVideo(self, sink):
        if sink is not None:
            self._player.setVideoSink(sink)

    @Slot()
    def refreshVideo(self):
        """Re-upload a paused frame after moving its output to a new scene graph."""
        sink = self._player.videoSink()
        if sink is None or self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            return
        frame = sink.videoFrame()
        if frame.isValid():
            image = frame.toImage()
            if not image.isNull():
                # GPU textures belong to the previous QQuickWindow; a CPU image
                # can be uploaded by the new window without playing/seeking.
                sink.setVideoFrame(QVideoFrame(image))

    @Slot("QVariantMap")
    def saveLayout(self, value):
        # Layout is local visual state, never a code or file execution channel.
        clean = json.loads(json.dumps(value, allow_nan=False))
        if len(json.dumps(clean)) > 64000:
            return
        self._layout = clean
        atomic_json(self._layout_path, clean)
        self.layoutChanged.emit()

    @Slot(str, "QVariant")
    def setting(self, name, value):
        allowed = {f.name: f for f in fields(AppSettings)}
        if name not in allowed:
            return
        try:
            before = getattr(self._settings, name)
            if name == "audio_output_id":
                self._set_audio_output(str(value))
                return
            if name in ("source_language", "target_language"):
                source = value if name == "source_language" else self._settings.source_language
                target = value if name == "target_language" else self._settings.target_language
                self.set_languages(source, target)
                return
            if isinstance(before, bool):
                value = value if isinstance(value, bool) else str(value).lower() == "true"
            elif isinstance(before, int):
                value = int(value)
            elif isinstance(before, float):
                value = float(value)
                if not math.isfinite(value):
                    raise ValueError("请输入有效数字")
            else:
                value = str(value).strip()
                if name == "teaching_preferences":
                    if len(value) > 6000 or "\x00" in value:
                        raise ValueError("讲解偏好最多 6000 字符，不能包含空字符")
                elif name not in ("asr_model", "asr_endpoint", "external_asr_model", "asr_key_env",
                                   "local_asr_endpoint", "local_asr_api_model", "local_asr_key_env"):
                    value = value[:200]
            if name in ("asr_endpoint", "external_asr_model", "asr_key_env", "asr_backend", "asr_response_format",
                        "local_asr_endpoint", "local_asr_api_model", "local_asr_key_env", "local_asr_response_format"):
                from tingmu.external_asr import validate_speech_setting
                value = validate_speech_setting(name, value)
                if name in ("asr_backend", "asr_endpoint") and value != before:
                    self._settings.allow_audio_upload = False
                if name in ("asr_backend", "asr_endpoint", "local_asr_endpoint") and value != before:
                    self._set_state(speechModelChoices=[])
            limits = {"subtitle_gap_ms": (0, 1500), "subtitle_offset_ms": (-600000, 600000),
                      "overlay_opacity": (0.15, 1.0), "overlay_font_size": (14, 48),
                      "audio_track": (0, 31), "llm_max_concurrency": (1, MAX_LLM_CONCURRENCY)}
            if name in limits:
                value = max(limits[name][0], min(limits[name][1], value))
            if name == "theme" and value not in ("forest", "paper", "mist", "graphite"):
                raise ValueError("请选择可用主题")
            if name == "companion_reasoning_effort" and value not in (
                    "default", "none", "minimal", "low", "medium", "high", "xhigh"):
                raise ValueError("请选择可用的思考强度")
            if name in ("review_model", "companion_model", "model") and (not value.strip() or len(value) > 200):
                raise ValueError("请输入有效的模型 ID")
            setattr(self._settings, name, value)
            self.store.save_settings(self._settings)
            if name == "auto_prepare_media" and value:
                self._resume_inbox()
            self.settingsChanged.emit()
            self._set_state(cloudEnabled=self._settings.cloud_text_enabled)
            if name in ("cloud_text_enabled", "text_models_enabled") and before and not value:
                self._stop_model_jobs()
            self.log.event("setting_changed", "设置已更新", "DEBUG", setting=name)
            if name == "developer_mode":
                self.log.set_developer(value)
                self.log.event("developer_mode", "开发者模式已开启" if value else "开发者模式已关闭")
                self.refresh_diagnostics()
            if name in ("level", "audio_track"):
                self._load_document()
                self._load_personal()
                self._refresh_library()
            elif name == "subtitle_gap_ms":
                self._set_document(self.document)
            elif name == "subtitle_offset_ms":
                self._sync_cue(self._player.position())
        except (ValueError, TypeError) as exc:
            self._error(exc)

    def set_languages(self, source, target):
        source, target = language_code(source), language_code(target)
        if (source, target) == (self._settings.source_language, self._settings.target_language):
            return
        updated = replace(self._settings, source_language=source, target_language=target)
        self.store.save_settings(updated)
        self._settings = updated
        self.settingsChanged.emit()
        self._load_document()
        self._load_personal()
        self._refresh_library()

    def add_paths(self, paths):
        normalized = [QUrl(p).toLocalFile() if str(p).startswith("file:") else p for p in paths]
        added = self.catalog.add(normalized)
        self._refresh_library()
        self._set_state(status=f"已添加 {len(added)} 个视频", error="")
        self.log.event("library_add", "媒体索引已更新", count=len(added))
        return added

    def _copy_media(self, selected):
        self._start_job("media-copy",
                        lambda progress, cancel: self.catalog.copy_files(selected, progress, cancel),
                        self.add_paths)

    def _scan_media_inbox(self):
        if self._closed or (self._inbox_thread and self._inbox_thread.is_alive()):
            return
        settings = replace(self._settings)
        scanner, store, stop = self._inbox_scanner, self.store, self._inbox_stop
        self._set_state(mediaInbox={**self._state.get("mediaInbox", {}), "scanning": True})

        def scan():
            try:
                if scanner.folder.resolve() == scanner.folder:
                    scanner.folder.mkdir(exist_ok=True)
                result = scanner.scan(cancelled=stop.is_set)
                if result is None or stop.is_set():
                    return
                documents = {}
                for path in result["ready"]:
                    if stop.is_set():
                        return
                    doc = store.load_document(path, settings.language)
                    documents[path] = doc.metadata.get("stage") if doc else ""
                candidates = [p for p, stage in documents.items() if stage != "ready"]
                # A ready sidecar needs no speech model; each path gets its own gate.
                result["blocked"] = {p: preparation_block_reason(settings, [p]) for p in candidates}
                result["documents"] = documents
                result["settings"] = asdict(settings)
            except Exception as exc:
                result = {"failure": type(exc).__name__}
            if not stop.is_set():
                self._inboxCompleted.emit(result)

        self._inbox_thread = threading.Thread(target=scan, name="tingmu-media-inbox", daemon=True)
        self._inbox_thread.start()

    @Slot(object)
    def _inbox_completed(self, result):
        if self._closed:
            return
        if result.get("failure"):
            self._set_state(mediaInbox={**self._state.get("mediaInbox", {}), "scanning": False,
                                       "blocked": "暂时无法检查媒体文件夹，稍后自动重试"})
            return
        if result["settings"] != asdict(self._settings):
            self._set_state(mediaInbox={**self._state.get("mediaInbox", {}), "scanning": False})
            return
        # A partial/inaccessible scan must never purge index entries.
        if result["error"]:
            self._set_state(mediaInbox={**self._state.get("mediaInbox", {}), "scanning": False,
                                       "blocked": result["error"]})
            return
        added, removed = self.catalog.reconcile_inbox(result["ready"], result["present"])
        snapshot = self.queue.snapshot()
        ready = result["ready"]
        selected, blocked = [], []
        enabled = self._settings.auto_prepare_media and not self._inbox_paused
        if enabled:
            for path in self.catalog.automatic & ready.keys():
                if result["documents"].get(path) == "ready":
                    continue
                same = [j for j in snapshot if j["media_path"] == path
                        and all(j["settings"].get(k) == getattr(self._settings, k)
                                for k in ("source_language", "target_language", "level"))
                        and j.get("fingerprint") == ready[path]]
                # Existing manual/failed/cleared jobs retain their explicit controls.
                if same:
                    own = [j for j in same if j.get("origin") == "media-inbox"
                           and j["status"] in ("queued", "paused") and not j.get("auto_paused")]
                    for job in own:
                        previous = AppSettings(**{k: v for k, v in job["settings"].items()
                                                  if k in {f.name for f in fields(AppSettings)}})
                        # Configuration changes must not revive a frozen request automatically.
                        if (preparation_identity_settings(asdict(previous)) !=
                                preparation_identity_settings(asdict(self._settings))):
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
                    jobs = self.queue.enqueue([Path(path)], replace(self._settings), automatic=True)
                    for job in jobs:
                        if job["fingerprint"] == ready[path]:
                            selected.append(job["id"])
                        else:
                            self.queue.remove(job["id"])
                except (OSError, ValueError):
                    # Removal/replacement after the snapshot will be reconsidered next scan.
                    continue
        if removed:
            for job in snapshot:
                if job.get("origin") == "media-inbox" and job["media_path"] in removed:
                    if job["status"] == "running":
                        self.queue.pause()
                    elif job["status"] in ("queued", "paused"):
                        self.queue.remove(job["id"])
        if added or removed:
            self._refresh_library()
        waiting = len(result["waiting"])
        status = (f"等待 {waiting} 个文件复制完成" if waiting else
                  "媒体文件夹自动准备已开启" if enabled else "媒体文件夹已同步")
        if self._settings.auto_prepare_media and self._inbox_paused:
            blocked.append("媒体文件夹自动准备已暂停。开始队列或重新打开自动准备即可继续。")
        self._set_state(mediaInbox={"scanning": False, "waiting": waiting, "ready": len(ready),
                                   "blocked": next(iter(blocked), ""), "status": status})
        if selected and "queue" not in self._workers:
            self._start_job("queue", lambda progress, cancelled: self.queue.run(
                progress, cancelled, on_error=lambda exc: self.log.exception(exc, "queue_error"),
                job_ids=selected, allow_model_downloads=False),
                lambda result: self._set_state(status="自动准备已结束；可在媒体库和队列查看结果"))
        self._refresh_jobs()

    def _refresh_library(self):
        jobs = {j["media_path"]: j for j in self.queue.snapshot()}
        rows = []
        for media in self.catalog.paths:
            document = self.store.load_document(media, self._settings.language)
            job = jobs.get(media)
            stage = document.metadata.get("stage", "transcribed") if document else "unprepared"
            status = "ready" if stage == "ready" else ("partial" if document else "unprepared")
            if document and status == "ready":
                counts = lesson_counts(document)
                if counts["verified"] < counts["total"]:
                    status = "lessonDraft" if counts["taught"] else "subtitleOnly"
            if job and job["status"] != "completed":
                status = job["status"]
            if not Path(media).is_file():
                status = "missing"
            rows.append({"path": media, "name": Path(media).name, "parent": str(Path(media).parent),
                         "status": status, "progress": job.get("progress", "") if job else "",
                         "cueCount": len(document.cues) if document else 0,
                         "position": self.store.load_position(media)})
        if rows != self._library:
            self._library = rows
            self.libraryChanged.emit()

    def open_media(self, raw_path):
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError("视频文件不存在")
        if path.suffix.lower() not in MEDIA_EXTENSIONS:
            raise ValueError("请选择支持的音视频文件")
        self.save_position()
        self._player.stop()
        self.media_path = str(path)
        self._embedded.select(self.media_path, -1, 0)
        self.sources.open(self.media_path)
        self._restore_embedded_pending = self.sources.embeddedChoice["show"]
        self.log.event("media_open", "打开媒体", media_id=media_id(self.media_path), extension=path.suffix.lower())
        self._loop_a = self._loop_b = -1
        self.catalog.add([path])
        self._resume_position = self.store.load_position(self.media_path)
        self._set_state(mediaPath=self.media_path, mediaName=path.name, error="", status="")
        self._load_document()
        self._load_personal()
        self._player.setSource(QUrl.fromLocalFile(self.media_path))
        self._restore_embedded_choice()
        self._player.play()
        self._refresh_library()

    def _load_document(self):
        doc = self.store.load_document(self.media_path, self._settings.language) if self.media_path else None
        # A different recognized audio track must not reuse the wrong transcript.
        spec = doc.metadata.get("transcript_spec", {}) if doc else {}
        if doc and not spec.get("subtitle") and "audio_track" in spec and spec["audio_track"] != self._settings.audio_track:
            doc = None
        self._set_document(doc)

    def _set_document(self, document):
        self.document = document
        self._source_cues = {cue.id: cue for cue in document.cues} if document else {}
        review = document.metadata.get("translation_review", {}) if document else {}
        self.timeline = SubtitleTimeline(document.cues if document else [], gap_fill_ms=self._settings.subtitle_gap_ms,
                                         translation_review=review)
        self._cues = [self._present_cue(cue) for cue in self.timeline.cues]
        self._cue_indices = {cue.id: i for i, cue in enumerate(self.timeline.cues)}
        self.cuesChanged.emit()
        self._active_cue = None
        self._active_index = -1
        self._sync_cue(self._player.position(), force=True)
        metadata = document.metadata if document else {}
        review = metadata.get("review_spec", {})
        active_review = bool(metadata.get("annotation_spec", {}).get("review"))
        self._set_state(subtitleSource="学习字幕" if document else "", lessonCounts=lesson_counts(document),
                        reviewModel=review.get("model", "") if active_review else "",
                        reviewSummary="文本已复核" if active_review and metadata.get("review_stage") == "ready" else "")

    def _present_cue(self, cue):
        value = cue_map(cue)
        if not cue:
            return value
        ids = self.timeline.source_ids(cue.id)
        metadata = self.document.metadata if self.document else {}
        records = metadata.get("review_records", {})
        active_review = bool(metadata.get("annotation_spec", {}).get("review"))
        value["reviewReason"] = "\n".join(records.get(i, {}).get("reason", "") for i in ids) if active_review else ""
        if any(i in metadata.get("cue_corrections", {}) for i in ids):
            value["reviewReason"] = "本句已按用户提供的意思逐句修订；仍保留原始识别文本与时间。"
        value["sourceIds"] = list(ids)
        originals = [self._source_cues[i] for i in ids if i in self._source_cues]
        value["translationVerified"] = bool(originals) and all(
            translation_verified(c, metadata.get("translation_review", {})) for c in originals)
        value["sourceStart"] = min((c.start for c in originals), default=cue.start)
        value["sourceEnd"] = max((c.end for c in originals), default=cue.end)
        value["timingBasis"] = next((s.timing_basis for s in self.timeline.segments if s.cue.id == cue.id), "source")
        return value

    def _load_personal(self):
        self._activate_context_session()
        self._notebook_language = self._settings.target_language
        self._refresh_notebooks()
        self._entries = self.store.entries(self.media_path) if self.media_path else self.store.entries()
        self.messagesChanged.emit()
        self.entriesChanged.emit()

    def _sync_cue(self, position, force=False):
        cue = self.timeline.subtitle_at(int(position) - self._settings.subtitle_offset_ms)
        if force or getattr(cue, "id", None) != getattr(self._active_cue, "id", None):
            self._active_cue = cue
            self._active_index = self._cue_indices.get(cue.id, -1) if cue else -1
            self.activeCueChanged.emit()

    def _position_changed(self, position):
        if self._loop_b > self._loop_a >= 0 and position >= self._loop_b:
            self._player.setPosition(self._loop_a)
            return
        self._sync_cue(position)
        self._embedded.position(position)
        self._update_playback()

    def _duration_changed(self, duration):
        self._schedule_resume_seek()
        self._update_playback()

    def _schedule_resume_seek(self):
        # FFmpeg reports duration and seekability while still LoadingMedia. A
        # synchronous seek at that point is discarded by the rest of its load.
        if self._resume_position is None or getattr(self, "_resume_seek_queued", False):
            return
        if self._player.mediaStatus() in (QMediaPlayer.MediaStatus.NoMedia,
                                           QMediaPlayer.MediaStatus.LoadingMedia,
                                           QMediaPlayer.MediaStatus.InvalidMedia):
            return
        if not self._player.isSeekable() or self._player.duration() <= 0:
            return
        self._resume_seek_queued = True

        def apply_after_load():
            self._resume_seek_queued = False
            if self._closed or self._resume_position is None:
                return
            # A newer media selection may have started before this callback.
            if self._player.mediaStatus() in (QMediaPlayer.MediaStatus.NoMedia,
                                               QMediaPlayer.MediaStatus.LoadingMedia,
                                               QMediaPlayer.MediaStatus.InvalidMedia):
                return
            if not self._player.isSeekable() or self._player.duration() <= 0:
                return
            position = max(0, min(self._resume_position, self._player.duration() - 100))
            self._resume_position = None
            self._player.setPosition(position)
            self._sync_cue(position)

        QTimer.singleShot(0, apply_after_load)

    def _media_status(self, status):
        self._restore_embedded_choice()
        self._schedule_resume_seek()
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._loop_b > self._loop_a >= 0:
                self._player.setPosition(self._loop_a)
                self._player.play()
            elif self._layout.get("autoNext", False):
                self._adjacent_media(1)
        self._update_playback()

    def _restore_embedded_choice(self):
        if not self._restore_embedded_pending or self._player.source() != QUrl.fromLocalFile(self.media_path):
            return
        if self._player.mediaStatus() not in (QMediaPlayer.MediaStatus.LoadedMedia,
                                              QMediaPlayer.MediaStatus.BufferingMedia,
                                              QMediaPlayer.MediaStatus.BufferedMedia):
            return
        tracks = self._player.subtitleTracks()
        if tracks:
            self._restore_embedded_pending = False
            index = self.sources.embeddedChoice["index"]
            if index < len(tracks):
                self._embedded.select(self.media_path, index, self._player.position())

    def _media_error(self, code, text):
        if code != QMediaPlayer.Error.NoError:
            self.log.event("player_error", "播放器发生错误", "ERROR", code=code.name)
            self._error(text or "无法播放此文件")

    def _set_audio_output(self, identity):
        self.sources.choose_output(identity)
        self._settings.audio_output_id = identity
        self.store.save_settings(self._settings)
        self.settingsChanged.emit()

    def _tracks(self, items, *, numbered=False):
        result = []
        for index, metadata in enumerate(items):
            title = metadata.stringValue(QMediaMetaData.Key.Title)
            language = metadata.stringValue(QMediaMetaData.Key.Language)
            name = title or language
            if numbered and name:
                name = f"{index + 1} · {name}"
            result.append({"index": index, "name": name or f"轨道 {index + 1}"})
        return result

    def _update_playback(self, *_):
        value = {"position": self._player.position(), "duration": self._player.duration(),
                 "playing": self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                 "volume": self.audio.volume(), "muted": self.audio.isMuted(), "rate": self._player.playbackRate(),
                 "audioTracks": self._tracks(self._player.audioTracks()),
                 "subtitleTracks": self._tracks(self._player.subtitleTracks(), numbered=True),
                 "audioTrack": self._player.activeAudioTrack(), "subtitleTrack": self._embedded.track,
                 "embeddedText": self._embedded.text, "embeddedImage": self._embedded.image,
                 "embeddedBounds": self._embedded.bounds, "embeddedStatus": self._embedded.status,
                 "loopA": self._loop_a, "loopB": self._loop_b}
        if value != self._playback:
            self._playback = value
            self.playbackChanged.emit()

    def save_position(self):
        if self.media_path and self._resume_position is None:
            self.store.save_position(self.media_path, self._player.position())

    def _seek(self, position):
        if (not self._player.isSeekable() or self._player.duration() <= 0
                or self._player.mediaStatus() == QMediaPlayer.MediaStatus.LoadingMedia):
            self._resume_position = max(0, int(position))
            self._sync_cue(self._resume_position)
            self._schedule_resume_seek()
            return
        self._resume_position = None
        position = max(0, min(int(position), self._player.duration() or int(position)))
        self._player.setPosition(position)
        self._sync_cue(position)

    def _adjacent_media(self, delta):
        if self.media_path in self.catalog.paths:
            index = self.catalog.paths.index(self.media_path) + delta
            if 0 <= index < len(self.catalog.paths):
                self.open_media(self.catalog.paths[index])

    def _navigate_cue(self, delta):
        if not self.timeline.cues:
            return
        index = self._active_index
        if index < 0:
            now = (self._player.position() - self._settings.subtitle_offset_ms) / 1000
            if delta > 0:
                index = next((i for i, c in enumerate(self.timeline.cues) if c.start > now), len(self.timeline.cues)-1)
            else:
                index = max((i for i, c in enumerate(self.timeline.cues) if c.start < now), default=0)
        else:
            index = max(0, min(index + delta, len(self.timeline.cues)-1))
        self._seek(round(self.timeline.cues[index].start * 1000) + self._settings.subtitle_offset_ms)

    def _refresh_jobs(self):
        snapshot = self.queue.snapshot()
        rows = []
        for job in snapshot:
            done, total = job.get("completed_cues", 0), job.get("total_cues", 0)
            rows.append({**job, "path": job["media_path"], "message": job.get("progress", ""),
                         "error": job.get("error_message") or (
                             "模型返回的字幕未通过校验；已完成步骤已保留，可重试。"
                             if job.get("error") == "AnnotationValidationError" else job.get("error", "")),
                         "progress": (done / total) if total else (1 if job["status"] == "completed" else 0),
                         "stage": self._job_stage(job), "done": done, "total": total})
            phase = (job["status"], self._job_stage(job))
            if self._last_queue_states.get(job["id"]) != phase:
                self._last_queue_states[job["id"]] = phase
                self.log.event("queue_stage", "视频处理阶段更新", job_id=job["id"], status=phase[0],
                               stage=phase[1], done=done, total=total)
        if rows != self._jobs:
            prior = {j["id"]: j["status"] for j in self._jobs}
            self._jobs = rows
            self.jobsChanged.emit()
            if prior != {j["id"]: j["status"] for j in rows}:
                self._refresh_library()
            else:
                # The queue already read this checkpoint. Avoid deserializing every
                # film and hashing every lesson on the UI thread for each batch.
                latest = {j["path"]: j for j in rows}
                updated = []
                for item in self._library:
                    job = latest.get(item["path"])
                    if job and job["status"] == "running":
                        item = {**item, "progress": job["message"], "cueCount": job["total"] or item["cueCount"]}
                    updated.append(item)
                if updated != self._library:
                    self._library = updated
                    self.libraryChanged.emit()
            if any(j["path"] == self.media_path and j["status"] in ("completed", "paused", "failed")
                   and j["status"] != prior.get(j["id"]) for j in rows):
                self._load_document()

    @staticmethod
    def _job_stage(job):
        text = job.get("progress", "")
        if job["status"] == "completed":
            return "完成"
        if "译文核验" in text:
            return "译稿核验"
        if "复核" in text:
            return "文本复核"
        if any(word in text for word in ("讲解", "教学", "分析", "标注")):
            return "学习材料"
        if any(word in text for word in ("识别", "音频", "模型", "转写", "Qwen", "Whisper")):
            return "外部识别" if job.get("settings", {}).get("asr_backend") == "external" else "本地识别"
        return {"queued": "等待", "paused": "已暂停", "failed": "失败"}.get(job["status"], "准备")

    def _start_job(self, key, function, completed):
        if key in self._workers:
            raise ValueError("该任务正在进行")
        cancelled = threading.Event()
        self._callbacks[key] = completed
        started = time.monotonic()
        self.log.event("worker_start", "后台任务开始", task=key)

        def work():
            try:
                result = function(lambda text: self._progress.emit(key, str(text)), cancelled.is_set)
                self.log.event("worker_done", "后台任务结束", task=key,
                               elapsed_ms=round((time.monotonic()-started)*1000), cancelled=cancelled.is_set())
                if not self._closed:
                    self._completed.emit(key, result, "")
            except Exception as exc:
                self.log.exception(exc, "worker_error")
                if not self._closed:
                    self._completed.emit(key, None, f"{type(exc).__name__}: {exc}")

        thread = threading.Thread(target=work, name=f"tingmu-{key}", daemon=True)
        self._workers[key] = (thread, cancelled)
        self._set_state(busy=True, queueRunning="queue" in self._workers,
                        modelDownloading="model-download" in self._workers,
                        chatBusy=bool({"chat", "summary"} & self._workers.keys()),
                        learningBusy="notebook" in self._workers, modelBusy="models" in self._workers, error="")
        thread.start()

    @Slot(str, str)
    def _job_progress(self, key, text):
        if not self._closed:
            self._set_state(status=text)

    @Slot(str, object, str)
    def _job_completed(self, key, value, error):
        if self._closed:
            return
        self._workers.pop(key, None)
        if key == "chat":
            self._chat_worker_session_id = ""
        callback = self._callbacks.pop(key, None)
        self._set_state(busy=bool(self._workers), queueRunning="queue" in self._workers,
                        modelDownloading="model-download" in self._workers,
                        chatBusy=bool({"chat", "summary"} & self._workers.keys()),
                        chatStopping=False if key == "chat" else self._state.get("chatStopping", False),
                        learningBusy="notebook" in self._workers, modelBusy="models" in self._workers,
                        streaming="chat" in self._workers)
        if error:
            self._error(error)
        elif callback:
            try:
                callback(value)
            except Exception as exc:
                self._error(exc)
        self._refresh_jobs()

    def _enqueue(self, paths):
        if not paths:
            raise ValueError("请先勾选要处理的视频")
        self.add_paths(paths)
        subtitle_paths = {}
        for media in paths:
            doc = self.store.load_document(str(media), self._settings.language)
            subtitle = doc.metadata.get("subtitle_path") if doc else None
            if subtitle:
                subtitle_paths[canonical_path(media)] = subtitle
        added = self.queue.enqueue([Path(p) for p in paths], replace(self._settings), subtitle_paths)
        self._refresh_jobs()
        self._set_state(status=f"{len(added)} 个视频已加入处理队列", error="")

    def _start_queue(self):
        if "queue" in self._workers:
            return
        if not self.queue.pending_count:
            self.queue.retry_failed()
        if not self.queue.pending_count:
            raise ValueError("队列中没有待处理视频")
        self._resume_inbox()
        self._start_job("queue", lambda progress, cancelled: self.queue.run(
                            progress, cancelled, on_error=lambda exc: self.log.exception(exc, "queue_error")),
                        lambda result: self._set_state(status="队列已暂停" if self.queue.pending_count else "队列处理结束"))

    def _resume_inbox(self):
        self._inbox_paused = False
        self.queue.resume_automatic()

    def _find_cue(self, cue_id):
        return next((c for c in self.timeline.cues if c.id == cue_id), self._active_cue if not cue_id else None)

    def _frozen_subtitle(self, cue_id):
        from tingmu.corrections import freeze_target
        ids = self.timeline.source_ids(cue_id)
        if self.document and len(ids) == 1 and ids[0] in self._source_cues:
            return freeze_target(self.document, self._source_cues[ids[0]], cue_id)
        return None

    @Slot(str, result="QVariantMap")
    def captureSubtitle(self, cue_id):
        cue = self._find_cue(cue_id) if cue_id else None
        frozen = self._frozen_subtitle(cue_id)
        if not cue or not frozen:
            return {}
        document, srt = self.store.material_paths(self.media_path, self._settings.language)
        return {"target": frozen, "text": cue.text, "start": cue.start,
                "media": self.media_path, "language": asdict(self._settings.language),
                "documentPath": str(document), "srtPath": str(srt)}

    @Slot("QVariantMap", str, result="QVariantMap")
    def submitSubtitleCorrection(self, captured, guidance):
        try:
            target = captured.get("target") or {}
            cue_id = target.get("display_id", "")
            if not target or captured != self.captureSubtitle(cue_id):
                return {"error": "字幕或学习材料已变化，请重新选择该句。"}
            if not guidance.strip():
                return {"error": "请填写你确认的说法或意思。"}
            session = self.conversations.load(self._active_session_id)
            if (session.get("media_path") != self.media_path or
                    session.get("source_language") != self._settings.source_language or
                    session.get("target_language") != self._settings.target_language):
                self._activate_context_session()
            cue = self._find_cue(cue_id)
            self._v4_send_chat("请按我确认的意思复核并保存本句的字幕、译文和讲解。\n" + guidance.strip(),
                               request_context={"media_path": self.media_path, "language": asdict(self._settings.language),
                                                "cue": asdict(cue), "correction": target, "subtitle_editor": True})
            return {"messageId": self._messages[-1]["id"]}
        except Exception as exc:
            self.log.exception(exc, "subtitle_correction_error")
            return {"error": str(exc) if isinstance(exc, ValueError) else "未能提交修订，请检查模型连接。"}

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
            if self.media_path == media and self._settings.language == language:
                self._set_document(doc)
            self._refresh_library()
            self._set_state(status=f"已导入 {len(doc.cues)} 条字幕")
        self._start_job("import", work, completed)

    def _summarize(self):
        self._organize_notebook()

    @Slot(str, "QVariantMap")
    def command(self, name, payload):
        try:
            self.log.event("ui_action", "界面操作", "DEBUG", action=name)
            self._command(name, payload or {})
        except Exception as exc:
            self.log.exception(exc, "command_error")
            self._error(exc)

    @Slot(QObject, str, str)
    def formatMarkdown(self, document, accent, border):
        from .typography import format_document
        format_document(document, accent, border)

    def _command(self, name, p):
        if self._feature_command(name, p):
            return
        if name in ("openFiles", "addFiles", "copyFiles"):
            selected, _ = QFileDialog.getOpenFileNames(None, "选择视频（可多选）", self._settings.library_path,
                "音视频 (" + " ".join("*" + ext for ext in sorted(MEDIA_EXTENSIONS)) + ");;所有文件 (*)")
            if selected:
                if name == "copyFiles":
                    self._copy_media(selected)
                else:
                    self.add_paths(selected)
                self._settings.library_path = str(Path(selected[0]).parent)
                self.store.save_settings(self._settings)
                if name == "openFiles" and len(selected) == 1:
                    self.open_media(selected[0])
        elif name == "openFolder":
            selected = QFileDialog.getExistingDirectory(None, "添加视频文件夹", self._settings.library_path)
            if selected:
                self.add_paths([selected])
                self._settings.library_path = selected
                self.store.save_settings(self._settings)
        elif name == "openMedia":
            self.open_media(p["path"])
        elif name == "addPaths":
            self.add_paths(p.get("paths", []))
        elif name == "scanWorkspaceMedia":
            self._scan_media_inbox()
        elif name == "removeMedia":
            self.catalog.remove(p.get("paths", []))
            self._refresh_library()
        elif name == "enqueue":
            self._enqueue(p.get("paths", []))
        elif name == "prepareCurrent":
            self._enqueue([self.media_path] if self.media_path else [])
            self._start_queue()
        elif name == "openLessonSettings":
            self.appAction.emit("lessonModels", {})
        elif name == "prepareLessons":
            if not self.document or not self.document.cues:
                raise ValueError("请先为这个视频准备学习字幕。")
            if not self.lessonModel["configured"]:
                raise ValueError("请先在工作区的文本模型设置中分配逐句讲解模型。")
            if "queue" in self._workers:
                raise ValueError("正在准备视频，请等待当前任务完成。")
            ident = self.queue.enqueue_lessons(Path(self.media_path), replace(self._settings))
            self._start_job("queue", lambda progress, cancel: self.queue.run(
                progress, cancel, on_error=lambda exc: self.log.exception(exc, "queue_error"), job_ids=[ident]),
                lambda _: self._load_document())
        elif name == "startQueue":
            self._start_queue()
        elif name == "pauseQueue":
            self._inbox_paused = True
            self.queue.pause_automatic()
            self.queue.pause()
            if "queue" in self._workers:
                self._workers["queue"][1].set()
            self._set_state(status="正在暂停；已完成步骤会保留")
        elif name == "retryJob":
            self.queue.retry(p["id"])
            self._refresh_jobs()
            self._start_queue()
        elif name == "retryFailed":
            self.queue.retry_failed()
            self._refresh_jobs()
        elif name == "removeJob":
            self.queue.remove(p["id"])
            self._refresh_jobs()
        elif name == "clearCompleted":
            self.queue.clear_completed()
            self._refresh_jobs()
        elif name == "playPause":
            if self.playback["playing"]:
                self._player.pause()
                self.save_position()
            elif self.media_path:
                self._player.play()
        elif name == "seek":
            self._seek(p["position"])
        elif name == "seekRelative":
            self._seek(self._player.position() + p["delta"])
        elif name == "previousCue":
            self._navigate_cue(-1)
        elif name == "nextCue":
            self._navigate_cue(1)
        elif name in ("repeatCue", "loopCue"):
            if self._active_cue:
                start = round(self._active_cue.start * 1000) + self._settings.subtitle_offset_ms
                if name == "loopCue":
                    self._loop_a = start
                    self._loop_b = max(start + 100, round(self._active_cue.end*1000) + self._settings.subtitle_offset_ms)
                self._seek(start)
                self._player.play()
        elif name == "previousMedia":
            self._adjacent_media(-1)
        elif name == "nextMedia":
            self._adjacent_media(1)
        elif name == "loopA":
            self._loop_a, self._loop_b = self._player.position(), -1
            self._update_playback()
        elif name == "loopB":
            if self._loop_a < 0 or self._player.position() <= self._loop_a:
                raise ValueError("请先设置较早的 A 点")
            self._loop_b = self._player.position()
            self._update_playback()
        elif name == "clearLoop":
            self._loop_a = self._loop_b = -1
            self._update_playback()
        elif name == "setVolume":
            self.audio.setVolume(max(0, min(1, float(p["value"]))))
            self._update_playback()
        elif name == "setMuted":
            self.audio.setMuted(bool(p["value"]))
            self._update_playback()
        elif name == "setRate":
            self._player.setPlaybackRate(max(0.25, min(3, float(p["value"]))))
            self._update_playback()
        elif name == "setAudioTrack":
            index = int(p["index"])
            if not 0 <= index < len(self._player.audioTracks()):
                raise ValueError("请选择可用的音轨。")
            self._player.setActiveAudioTrack(index)
        elif name == "setAudioOutput":
            self._set_audio_output(str(p["id"]))
        elif name == "setSubtitleTrack":
            index = int(p["index"])
            if not -1 <= index < len(self._player.subtitleTracks()):
                raise ValueError("请选择可用的内嵌字幕轨。")
            self._restore_embedded_pending = False
            self.sources.rememberTrack(index)
            self._embedded.select(self.media_path, index, self._player.position())
        elif name in ("importSubtitle", "importStudySubtitle"):
            if not self.media_path:
                raise ValueError("请先打开字幕对应的视频")
            path = p.get("path")
            if not path:
                title = "导入播放字幕" if name == "importSubtitle" else "从字幕建立学习文档"
                path, _ = QFileDialog.getOpenFileName(None, title, str(Path(self.media_path).parent), "字幕 (*.srt *.ass *.ssa *.vtt)")
            if path:
                if name == "importStudySubtitle":
                    self._import_subtitle(path)
                else:
                    media = self.media_path

                    def imported(track):
                        if self.media_path == media:
                            self.sources.install(track)
                            self._set_state(status=f"已导入播放字幕：{track.name}")

                    self._start_job("display-import", lambda *_: self.sources.import_file(media, path), imported)
        elif name == "removeDisplaySubtitle":
            if "display-import" in self._workers:
                raise ValueError("正在导入播放字幕，请稍后再移除。")
            self.sources.remove()
        elif name in ("exportSubtitle", "exportTranslation"):
            if not self._cues:
                raise ValueError("当前没有字幕")
            translation = name == "exportTranslation"
            cues = translated_subtitles(self.document) if translation else self.timeline.cues
            if not cues:
                raise ValueError("请先补全逐句讲解与译稿，完成复核后可导出整片译稿。")
            filename = f"{Path(self.media_path).stem}.{self._settings.source_language}.srt" if translation else "字幕.srt"
            path = self._export_destination(p.get("path"), filename, (".srt",))
            if path:
                save_srt(cues, path)
                self._set_state(status=f"字幕已导出：{path}")
        elif name == "saveWord":
            cue = self._find_cue(p.get("cueId", ""))
            if cue and 0 <= int(p["tokenIndex"]) < len(cue.tokens):
                self.store.save_word(self.media_path, cue, cue.tokens[int(p["tokenIndex"])], self._settings.language)
                self._entries = self.store.entries(self.media_path)
                self.entriesChanged.emit()
                self.notebook_manager.sync(self._settings.target_language)
                self._refresh_notebooks()
                self._set_state(status="生词已收藏")
        elif name == "saveNote":
            cue = self._find_cue(p.get("cueId", ""))
            if cue:
                self.store.save_note(self.media_path, cue, str(p.get("note", "")), self._settings.language)
                self._entries = self.store.entries(self.media_path)
                self.entriesChanged.emit()
                self.notebook_manager.sync(self._settings.target_language)
                self._refresh_notebooks()
                self._set_state(status="句子笔记已保存")
        elif name == "deleteEntry":
            self.store.delete_entry(int(p["id"]))
            self._entries = self.store.entries(self.media_path) if self.media_path else self.store.entries()
            self.entriesChanged.emit()
            self.notebook_manager.sync(self._notebook_language)
            self._refresh_notebooks()
        elif name == "openEntry":
            entry = next((item for item in self.store.entries() if item["id"] == int(p["id"])), None)
            if entry is None:
                entry = self.notebook_manager.source_entry(int(p["id"]))
            if entry:
                changed = (self._settings.source_language != entry["source_language"]
                           or self._settings.target_language != entry["target_language"])
                if changed:
                    self._settings.source_language = entry["source_language"]
                    self._settings.target_language = entry["target_language"]
                    self.store.save_settings(self._settings)
                    self.settingsChanged.emit()
                if entry["media_path"] != self.media_path:
                    self.open_media(entry["media_path"])
                elif changed:
                    self._load_document()
                    self._load_personal()
                self._seek(round(entry["start"] * 1000))
        elif name == "exportNotes":
            path = self._export_destination(p.get("path"), "学习记录.md", (".md", ".csv"))
            if path:
                method = self.store.export_csv if Path(path).suffix.lower() == ".csv" else self.store.export_markdown
                method(Path(path), self.media_path or None)
                self._set_state(status=f"学习记录已导出：{path}")
        elif name == "saveFrame":
            sink = self._player.videoSink()
            frame = sink.videoFrame() if sink else None
            if not frame or not frame.isValid():
                raise ValueError("当前没有可保存的画面")
            image = frame.toImage()
            destination = export_path(self.store.data_dir, datetime.now().strftime("画面-%Y%m%d-%H%M%S-%f.png"), (".png",))
            if not image.save(str(destination)):
                raise ValueError("保存画面失败")
            self._set_state(status=f"画面已保存：{destination}")
        elif name == "summarizeNotes":
            self._summarize()
        elif name == "sendChat":
            self._send_chat(str(p.get("text", "")))
        elif name == "clearChat":
            if {"chat", "summary"} & self._workers.keys():
                raise ValueError("请等待当前回复完成")
            self._messages = []
            self._persist_chat(self.conversations.load(self._active_session_id), [])
            self.messagesChanged.emit()
        elif name == "openDataFolder":
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.data_dir)))
        elif name == "openLogFolder":
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.log.directory)))
        elif name == "refreshDiagnostics":
            self.refresh_diagnostics()
        elif name == "exportDiagnostics":
            self.export_diagnostics()
        elif name == "dismissError":
            self._set_state(error="", status="")
        elif name == "openAbout":
            self._set_state(status=f"听幕 {__version__} · 当前工作区：{self.store.data_dir}")
        else:
            raise ValueError(f"未知操作：{name}")

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
        self.position_timer.stop()
        self.diagnostic_timer.stop()
        self.inbox_timer.stop()
        self._inbox_stop.set()
        workers = list(self._workers.values())
        for thread, event in workers:
            event.set()
        self._player.stop()
        # Let the local ASR worker observe cancellation and reap its own child.
        # Cloud transports may still be waiting on a socket; do not freeze exit.
        deadline = time.monotonic() + 3.5
        for thread, event in workers:
            thread.join(max(0, deadline - time.monotonic()))
        self.log.event("app_stop", "应用关闭", pending_tasks=sum(thread.is_alive() for thread, _ in workers))
        self.log.close()
