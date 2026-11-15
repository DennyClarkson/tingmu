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
from PySide6.QtWidgets import QFileDialog

from tingmu import __version__
from tingmu.batch import PreparationQueue
from tingmu.diagnostics import AppLog, media_id, safe_fields
from tingmu.library import MediaLibrary
from tingmu.media import MEDIA_EXTENSIONS, load_subtitles, save_srt
from tingmu.models import AppSettings, StudyDocument
from tingmu.storage import Store, atomic_json, canonical_path
from tingmu.timeline import SubtitleTimeline
from .features import FeatureMixin


def cue_map(cue):
    if cue is None:
        return {}
    value = asdict(cue)
    value["reading"] = " · ".join(t.reading for t in cue.tokens if t.reading)
    return value


class Bridge(FeatureMixin, QObject):
    stateChanged = Signal()
    settingsChanged = Signal()
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

    def __init__(self, store: Store):
        super().__init__()
        QGuiApplication.instance().focusWindowChanged.connect(self._focus_window_changed)
        self.store = store
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
        self._subtitle_sink = None
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

    def _focus_window_changed(self, *_):
        self.focusWindowChanged.emit()

    @Property(QObject, notify=focusWindowChanged)
    def focusWindow(self):
        return QGuiApplication.focusWindow()

    @Property(QObject, constant=True)
    def player(self):
        return self._player

    @Property("QVariantMap", notify=stateChanged)
    def state(self):
        return self._state

    @Property("QVariantMap", notify=settingsChanged)
    def settings(self):
        return asdict(self._settings)

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
        counts = {}
        for job in self.queue.snapshot():
            counts[job["status"]] = counts.get(job["status"], 0) + 1
        value = {"version": __version__, "frontend": "Qt Quick/QML", "dataDir": str(self.store.data_dir),
                 "logDir": str(self.log.directory), "python": sys.version.split()[0], "platform": platform.platform(),
                 "media": {"loaded": bool(self.media_path), "id": media_id(self.media_path),
                           "cues": len(self._cues), "position": self._player.position(), "duration": self._player.duration()},
                 "queue": {"running": "queue" in self._workers, "counts": counts},
                 "models": {"asr": self._settings.asr_model, "review": self._settings.review_model,
                            "teaching": self._settings.model, "companion": self._settings.companion_model,
                            "device": self._settings.device},
                 "privacy": {"cloudText": self._settings.cloud_text_enabled, "logsIncludeContent": False},
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
            if self._subtitle_sink is not sink:
                if self._subtitle_sink is not None:
                    try:
                        self._subtitle_sink.subtitleTextChanged.disconnect(self._update_playback)
                    except (RuntimeError, TypeError):
                        pass
                self._subtitle_sink = sink
                sink.subtitleTextChanged.connect(self._update_playback)

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
                else:
                    value = value[:200]
            limits = {"subtitle_gap_ms": (0, 1500), "subtitle_offset_ms": (-600000, 600000),
                      "overlay_opacity": (0.15, 1.0), "overlay_font_size": (14, 48),
                      "audio_track": (0, 31)}
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
            self.settingsChanged.emit()
            self._set_state(cloudEnabled=self._settings.cloud_text_enabled)
            self.log.event("setting_changed", "设置已更新", "DEBUG", setting=name)
            if name == "developer_mode":
                self.log.set_developer(value)
                self.log.event("developer_mode", "开发者模式已开启" if value else "开发者模式已关闭")
                self.refresh_diagnostics()
            if name in ("source_language", "target_language", "level", "audio_track"):
                self._load_document()
                self._load_personal()
                self._refresh_library()
            elif name == "subtitle_gap_ms":
                self._set_document(self.document)
            elif name == "subtitle_offset_ms":
                self._sync_cue(self._player.position())
        except (ValueError, TypeError) as exc:
            self._error(exc)

    def add_paths(self, paths):
        normalized = [QUrl(p).toLocalFile() if str(p).startswith("file:") else p for p in paths]
        added = self.catalog.add(normalized)
        self._refresh_library()
        self._set_state(status=f"已添加 {len(added)} 个视频", error="")
        self.log.event("library_add", "媒体索引已更新", count=len(added))
        return added

    def _refresh_library(self):
        jobs = {j["media_path"]: j for j in self.queue.snapshot()}
        rows = []
        for media in self.catalog.paths:
            document = self.store.load_document(media, self._settings.language)
            job = jobs.get(media)
            stage = document.metadata.get("stage", "transcribed") if document else "unprepared"
            status = "ready" if stage == "ready" else ("partial" if document else "unprepared")
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
        self.log.event("media_open", "打开媒体", media_id=media_id(self.media_path), extension=path.suffix.lower())
        self._loop_a = self._loop_b = -1
        self.catalog.add([path])
        self._resume_position = self.store.load_position(self.media_path)
        self._set_state(mediaPath=self.media_path, mediaName=path.name, error="", status="")
        self._load_document()
        self._load_personal()
        self._player.setSource(QUrl.fromLocalFile(self.media_path))
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
        self.timeline = SubtitleTimeline(document.cues if document else [], gap_fill_ms=self._settings.subtitle_gap_ms)
        self._cues = [self._present_cue(cue) for cue in self.timeline.cues]
        self._cue_indices = {cue.id: i for i, cue in enumerate(self.timeline.cues)}
        self.cuesChanged.emit()
        self._active_cue = None
        self._active_index = -1
        self._sync_cue(self._player.position(), force=True)
        metadata = document.metadata if document else {}
        review = metadata.get("review_spec", {})
        active_review = bool(metadata.get("annotation_spec", {}).get("review"))
        self._set_state(subtitleSource="学习字幕" if document else "",
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
        value["sourceIds"] = list(ids)
        originals = [self._source_cues[i] for i in ids if i in self._source_cues]
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
        self._schedule_resume_seek()
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._loop_b > self._loop_a >= 0:
                self._player.setPosition(self._loop_a)
                self._player.play()
            elif self._layout.get("autoNext", False):
                self._adjacent_media(1)
        self._update_playback()

    def _media_error(self, code, text):
        if code != QMediaPlayer.Error.NoError:
            self.log.event("player_error", "播放器发生错误", "ERROR", code=code.name)
            self._error(text or "无法播放此文件")

    def _tracks(self, items):
        result = []
        for index, metadata in enumerate(items):
            title = metadata.stringValue(QMediaMetaData.Key.Title)
            language = metadata.stringValue(QMediaMetaData.Key.Language)
            result.append({"index": index, "name": title or language or f"轨道 {index + 1}"})
        return result

    def _update_playback(self, *_):
        value = {"position": self._player.position(), "duration": self._player.duration(),
                 "playing": self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                 "volume": self.audio.volume(), "muted": self.audio.isMuted(), "rate": self._player.playbackRate(),
                 "audioTracks": self._tracks(self._player.audioTracks()),
                 "subtitleTracks": self._tracks(self._player.subtitleTracks()),
                 "audioTrack": self._player.activeAudioTrack(), "subtitleTrack": self._player.activeSubtitleTrack(),
                 "embeddedText": self._player.videoSink().subtitleText() if self._player.videoSink() else "",
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
            old_completed = {j["id"] for j in self._jobs if j["status"] == "completed"}
            self._jobs = rows
            self.jobsChanged.emit()
            self._refresh_library()
            if any(j["path"] == self.media_path and j["status"] == "completed" and j["id"] not in old_completed for j in rows):
                self._load_document()

    @staticmethod
    def _job_stage(job):
        text = job.get("progress", "")
        if job["status"] == "completed":
            return "完成"
        if "复核" in text:
            return "文本复核"
        if any(word in text for word in ("讲解", "教学", "分析", "标注")):
            return "学习材料"
        if any(word in text for word in ("识别", "音频", "模型", "转写", "Qwen", "Whisper")):
            return "本地识别"
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
        callback = self._callbacks.pop(key, None)
        self._set_state(busy=bool(self._workers), queueRunning="queue" in self._workers,
                        chatBusy=bool({"chat", "summary"} & self._workers.keys()),
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
        self._start_job("queue", lambda progress, cancelled: self.queue.run(
                            progress, cancelled, on_error=lambda exc: self.log.exception(exc, "queue_error")),
                        lambda result: self._set_state(status="队列已暂停" if self.queue.pending_count else "队列处理结束"))

    def _find_cue(self, cue_id):
        return next((c for c in self.timeline.cues if c.id == cue_id), self._active_cue if not cue_id else None)

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
            self.store.save_document(doc)
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
        if name in ("openFiles", "addFiles"):
            selected, _ = QFileDialog.getOpenFileNames(None, "选择视频（可多选）", self._settings.library_path,
                "音视频 (" + " ".join("*" + ext for ext in sorted(MEDIA_EXTENSIONS)) + ");;所有文件 (*)")
            if selected:
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
        elif name == "removeMedia":
            self.catalog.remove(p.get("paths", []))
            self._refresh_library()
        elif name == "enqueue":
            self._enqueue(p.get("paths", []))
        elif name == "prepareCurrent":
            self._enqueue([self.media_path] if self.media_path else [])
            self._start_queue()
        elif name == "startQueue":
            self._start_queue()
        elif name == "pauseQueue":
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
            self._player.setActiveAudioTrack(int(p["index"]))
        elif name == "setSubtitleTrack":
            self._player.setActiveSubtitleTrack(int(p["index"]))
        elif name == "importSubtitle":
            path = p.get("path")
            if not path:
                path, _ = QFileDialog.getOpenFileName(None, "导入字幕", str(Path(self.media_path).parent), "字幕 (*.srt *.ass *.ssa *.vtt)")
            if path:
                self._import_subtitle(path)
        elif name == "exportSubtitle":
            if not self._cues:
                raise ValueError("当前没有字幕")
            path = p.get("path")
            if not path:
                path, _ = QFileDialog.getSaveFileName(None, "导出字幕", str(self.store.data_dir / "exports" / "字幕.srt"), "SRT (*.srt)")
            if path:
                Path(path).parent.mkdir(parents=True, exist_ok=True)
                save_srt(self.timeline.cues, Path(path))
                self._set_state(status="字幕已导出")
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
            path = p.get("path")
            if not path:
                path, _ = QFileDialog.getSaveFileName(None, "导出学习记录", str(self.store.data_dir / "exports/学习记录.md"), "Markdown (*.md);;CSV (*.csv)")
            if path:
                method = self.store.export_csv if Path(path).suffix.lower() == ".csv" else self.store.export_markdown
                method(Path(path), self.media_path or None)
                self._set_state(status="学习记录已导出")
        elif name == "saveFrame":
            sink = self._player.videoSink()
            frame = sink.videoFrame() if sink else None
            if not frame or not frame.isValid():
                raise ValueError("当前没有可保存的画面")
            image = frame.toImage()
            destination = self.store.data_dir / "exports" / (datetime.now().strftime("画面-%Y%m%d-%H%M%S") + ".png")
            destination.parent.mkdir(parents=True, exist_ok=True)
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
            self._set_state(status=f"听幕 {__version__} · 本地播放与语言学习 · 学习资料保存在应用 data 目录")
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
        self.timer.stop()
        self.position_timer.stop()
        self.diagnostic_timer.stop()
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
