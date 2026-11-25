"""Playback domain state and explicit UI/service ports."""

from dataclasses import dataclass, field
from typing import Any, Callable
from dataclasses import asdict
from pathlib import Path
from datetime import datetime
from math import ceil
import time
from tingmu.export_paths import export_path
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtMultimedia import QMediaMetaData, QMediaPlayer, QVideoFrame
from tingmu.diagnostics import media_id
from tingmu.media_files import MEDIA_EXTENSIONS
from tingmu.lesson_material import lesson_counts
from tingmu.translation_review import translation_provided, translation_verified
from tingmu.timeline import SubtitleTimeline
from tingmu.models import StudyDocument


def cue_map(cue):
    if cue is None:
        return {}
    value = asdict(cue)
    value["reading"] = " · ".join(t.reading for t in cue.tokens if t.reading)
    return value


@dataclass
class PlaybackState:
    player: Any = None
    audio: Any = None
    sources: Any = None
    embedded: Any = None
    restore_embedded_pending: bool = False
    document: StudyDocument | None = None
    media_path: str = ""
    timeline: SubtitleTimeline = field(default_factory=lambda: SubtitleTimeline([]))
    cues: list[dict] = field(default_factory=list)
    source_cues: dict = field(default_factory=dict)
    cue_indices: dict = field(default_factory=dict)
    active_cue: Any = None
    active_index: int = -1
    playback_details: dict = field(default_factory=dict)
    playback_position: int = 0
    resume_position: int | None = None
    resume_seek_queued: bool = False
    loop_a: int = -1
    loop_b: int = -1
    position_writer: Any = None
    position_future: Any = None
    last_position_request: tuple[str, int] | None = None
    position_timer: Any = None


@dataclass(frozen=True)
class PlaybackPorts:
    task_keys: Callable
    set_audio_output: Callable
    navigate_cue: Callable
    activeCueChanged: Any
    adjacent_media: Callable
    catalog: Any
    cuesChanged: Any
    error: Callable
    finish_position_checkpoint: Callable
    is_closed: Callable
    layout: Callable
    load_document: Callable
    load_personal: Callable
    log: Any
    open_media: Callable
    playbackChanged: Any
    playbackDetailsChanged: Any
    present_cue: Callable
    refresh_library: Callable
    restore_embedded_choice: Callable
    save_position: Callable
    schedule_resume_seek: Callable
    seek: Callable
    set_document: Callable
    set_state: Callable
    settings: Callable
    settingsChanged: Any
    store: Any
    sync_cue: Callable
    tracks: Callable
    update_playback: Callable


class PlaybackController:
    def __init__(self, state: PlaybackState, ports: PlaybackPorts):
        self.state = state
        self.ports = ports
        self.notify_timer = QTimer(state.player)
        self.notify_timer.setSingleShot(True)
        self.notify_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.notify_timer.timeout.connect(self._emit_playback)
        self._last_tick = None

    def attachVideo(self, sink):
        if sink is not None:
            self.state.player.setVideoSink(sink)

    def refreshVideo(self):
        """Re-upload a paused frame after moving its output to a new scene graph."""
        sink = self.state.player.videoSink()
        if sink is None or self.state.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            return
        frame = sink.videoFrame()
        if frame.isValid():
            image = frame.toImage()
            if not image.isNull():
                # GPU textures belong to the previous QQuickWindow; a CPU image
                # can be uploaded by the new window without playing/seeking.
                sink.setVideoFrame(QVideoFrame(image))

    def open_media(self, raw_path):
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError("视频文件不存在")
        if path.suffix.lower() not in MEDIA_EXTENSIONS:
            raise ValueError("请选择支持的音视频文件")
        self.ports.save_position()
        self.state.player.stop()
        self.notify_timer.stop()
        self._last_tick = None
        self.state.media_path = str(path)
        self.state.embedded.select(self.state.media_path, -1, 0)
        self.state.sources.open(self.state.media_path)
        self.state.restore_embedded_pending = self.state.sources.embeddedChoice["show"]
        self.ports.log.event(
            "media_open", "打开媒体", media_id=media_id(self.state.media_path), extension=path.suffix.lower()
        )
        self.state.loop_a = self.state.loop_b = -1
        self.ports.catalog.add([path])
        with self.ports.store.database.session():
            self.state.resume_position = self.ports.store.load_position(self.state.media_path)
            self.ports.set_state(mediaPath=self.state.media_path, mediaName=path.name, error="", status="")
            self.ports.load_document()
            self.ports.load_personal()
        self.state.player.setSource(QUrl.fromLocalFile(self.state.media_path))
        self.ports.restore_embedded_choice()
        self.state.player.play()
        self.ports.refresh_library()

    def _load_document(self):
        doc = (
            self.ports.store.load_document(self.state.media_path, self.ports.settings().language)
            if self.state.media_path
            else None
        )
        # A different recognized audio track must not reuse the wrong transcript.
        spec = doc.metadata.get("transcript_spec", {}) if doc else {}
        if (
            doc
            and not spec.get("subtitle")
            and "audio_track" in spec
            and spec["audio_track"] != self.ports.settings().audio_track
        ):
            doc = None
        self.ports.set_document(doc)

    def _set_document(self, document):
        self.state.document = document
        self.state.source_cues = {cue.id: cue for cue in document.cues} if document else {}
        review = document.metadata.get("translation_review", {}) if document else {}
        self.state.timeline = SubtitleTimeline(
            document.cues if document else [],
            gap_fill_ms=self.ports.settings().subtitle_gap_ms,
            translation_review=review,
        )
        self.state.cues = [self.ports.present_cue(segment) for segment in self.state.timeline.segments]
        self.state.cue_indices = {cue.id: i for i, cue in enumerate(self.state.timeline.cues)}
        self.state.active_cue = None
        self.state.active_index = -1
        self.ports.cuesChanged.emit()
        self.ports.sync_cue(self.state.player.position(), force=True)
        metadata = document.metadata if document else {}
        review = metadata.get("review_spec", {})
        active_review = bool(metadata.get("annotation_spec", {}).get("review"))
        self.ports.set_state(
            subtitleSource="学习字幕" if document else "",
            lessonCounts=lesson_counts(document),
            reviewModel=review.get("model", "") if active_review else "",
            reviewSummary="文本已复核" if active_review and metadata.get("review_stage") == "ready" else "",
        )

    def _present_cue(self, segment):
        cue = segment.cue
        value = cue_map(cue)
        ids = segment.source_ids
        metadata = self.state.document.metadata if self.state.document else {}
        records = metadata.get("review_records", {})
        active_review = bool(metadata.get("annotation_spec", {}).get("review"))
        value["reviewReason"] = (
            "\n".join(records.get(i, {}).get("reason", "") for i in ids) if active_review else ""
        )
        if any(i in metadata.get("cue_corrections", {}) for i in ids):
            value["reviewReason"] = "本句已按用户提供的意思逐句修订；仍保留原始识别文本与时间。"
        value["sourceIds"] = list(ids)
        originals = [self.state.source_cues[i] for i in ids if i in self.state.source_cues]
        value["sourceText"] = originals[0].text if originals else cue.text
        value["translationVerified"] = bool(originals) and all(
            translation_verified(c, metadata.get("translation_review", {})) for c in originals
        )
        value["translationProvided"] = bool(originals) and all(
            translation_provided(c, metadata.get("translation_review", {})) for c in originals
        )
        value["sourceStart"] = min((c.start for c in originals), default=cue.start)
        value["sourceEnd"] = max((c.end for c in originals), default=cue.end)
        value["timingBasis"] = segment.timing_basis
        return value

    def _sync_cue(self, position, force=False):
        cue = self.state.timeline.subtitle_at(int(position) - self.ports.settings().subtitle_offset_ms)
        if force or getattr(cue, "id", None) != getattr(self.state.active_cue, "id", None):
            self.state.active_cue = cue
            self.state.active_index = self.state.cue_indices.get(cue.id, -1) if cue else -1
            self.ports.activeCueChanged.emit()

    def _position_changed(self, position):
        if self.state.loop_b > self.state.loop_a >= 0 and position >= self.state.loop_b:
            self._set_player_position(self.state.loop_a)
            return
        self.ports.sync_cue(position)
        self.state.embedded.position(position)
        if position != self.state.playback_position:
            self.state.playback_position = position
            deadline = self._last_tick + 0.07 if self._last_tick is not None else 0
            remaining = deadline - time.monotonic()
            details = self.state.playback_details
            duration = details.get("duration", 0)
            if remaining <= 0 or not details.get("playing") or position == 0 or (duration > 0 and position >= duration):
                self._emit_playback()
            elif not self.notify_timer.isActive():
                self.notify_timer.start(ceil(remaining * 1000))

    def _emit_playback(self):
        self.notify_timer.stop()
        self._last_tick = time.monotonic()
        self.ports.playbackChanged.emit()

    def _set_player_position(self, position):
        self._last_tick = None
        self.state.player.setPosition(position)
        if self.notify_timer.isActive():
            self._emit_playback()

    def _duration_changed(self, duration):
        self.ports.schedule_resume_seek()
        self.ports.update_playback()

    def _schedule_resume_seek(self):
        # FFmpeg reports duration and seekability while still LoadingMedia. A
        # synchronous seek at that point is discarded by the rest of its load.
        if self.state.resume_position is None or self.state.resume_seek_queued:
            return
        if self.state.player.mediaStatus() in (
            QMediaPlayer.MediaStatus.NoMedia,
            QMediaPlayer.MediaStatus.LoadingMedia,
            QMediaPlayer.MediaStatus.InvalidMedia,
        ):
            return
        if not self.state.player.isSeekable() or self.state.player.duration() <= 0:
            return
        self.state.resume_seek_queued = True

        def apply_after_load():
            self.state.resume_seek_queued = False
            if self.ports.is_closed() or self.state.resume_position is None:
                return
            # A newer media selection may have started before this callback.
            if self.state.player.mediaStatus() in (
                QMediaPlayer.MediaStatus.NoMedia,
                QMediaPlayer.MediaStatus.LoadingMedia,
                QMediaPlayer.MediaStatus.InvalidMedia,
            ):
                return
            if not self.state.player.isSeekable() or self.state.player.duration() <= 0:
                return
            position = max(0, min(self.state.resume_position, self.state.player.duration() - 100))
            self.state.resume_position = None
            self._set_player_position(position)
            self.ports.sync_cue(position)

        QTimer.singleShot(0, apply_after_load)

    def _media_status(self, status):
        self.ports.restore_embedded_choice()
        self.ports.schedule_resume_seek()
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self.state.loop_b > self.state.loop_a >= 0:
                self._set_player_position(self.state.loop_a)
                self.state.player.play()
            elif self.ports.layout().get("autoNext", False):
                self.ports.adjacent_media(1)
        self.ports.update_playback()
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.notify_timer.isActive():
            self._emit_playback()

    def _restore_embedded_choice(self):
        if not self.state.restore_embedded_pending or self.state.player.source() != QUrl.fromLocalFile(
            self.state.media_path
        ):
            return
        if self.state.player.mediaStatus() not in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferingMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        ):
            return
        tracks = self.state.player.subtitleTracks()
        if tracks:
            self.state.restore_embedded_pending = False
            index = self.state.sources.embeddedChoice["index"]
            if index < len(tracks):
                self.state.embedded.select(self.state.media_path, index, self.state.player.position())

    def _media_error(self, code, text):
        if code != QMediaPlayer.Error.NoError:
            self.ports.log.event("player_error", "播放器发生错误", "ERROR", code=code.name)
            self.ports.error(text or "无法播放此文件", domain="media")

    def _set_audio_output(self, identity):
        self.state.sources.choose_output(identity)
        self.ports.settings().audio_output_id = identity
        self.ports.store.save_settings(self.ports.settings())
        self.ports.settingsChanged.emit()

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
        value = {
            "duration": self.state.player.duration(),
            "playing": self.state.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
            "volume": self.state.audio.volume(),
            "muted": self.state.audio.isMuted(),
            "rate": self.state.player.playbackRate(),
            "audioTracks": self.ports.tracks(self.state.player.audioTracks()),
            "subtitleTracks": self.ports.tracks(self.state.player.subtitleTracks(), numbered=True),
            "audioTrack": self.state.player.activeAudioTrack(),
            "subtitleTrack": self.state.embedded.track,
            "embeddedText": self.state.embedded.text,
            "embeddedCue": self.state.embedded.cue,
            "embeddedImage": self.state.embedded.image,
            "embeddedBounds": self.state.embedded.bounds,
            "embeddedStatus": self.state.embedded.status,
            "loopA": self.state.loop_a,
            "loopB": self.state.loop_b,
        }
        if value["playing"] and not self.ports.is_closed() and not self.state.position_timer.isActive():
            self.state.position_timer.start()
        elif not value["playing"]:
            self.state.position_timer.stop()
        position = self.state.player.position()
        details_changed = value != self.state.playback_details
        position_changed = position != self.state.playback_position
        self.state.playback_position = position
        was_playing = self.state.playback_details.get("playing", False)
        if details_changed:
            self.state.playback_details = value
            self.ports.playbackDetailsChanged.emit()
        if details_changed or position_changed or (self.notify_timer.isActive() and not value["playing"]):
            self._emit_playback()
        if details_changed and value["playing"] and not was_playing:
            self._last_tick = None

    def _finish_position_checkpoint(self):
        if self.state.position_future is not None:
            try:
                self.state.position_future.result()
            except Exception as exc:
                self.state.last_position_request = None
                self.ports.log.exception(exc, "position_checkpoint_failed")
            self.state.position_future = None

    def _checkpoint_position(self):
        if not self.state.media_path or self.state.resume_position is not None:
            return
        if self.state.position_future is not None and not self.state.position_future.done():
            return
        self.ports.finish_position_checkpoint()
        position = (self.state.media_path, self.state.player.position())
        if position != self.state.last_position_request:
            self.state.position_future = self.state.position_writer.submit(
                self.ports.store.save_position, *position
            )
            self.state.last_position_request = position

    def save_position(self):
        # Explicit pause, media switch and exit must persist the latest position
        # after any earlier checkpoint, even if a preparation job held the writer lock.
        self.ports.finish_position_checkpoint()
        if self.state.media_path and self.state.resume_position is None:
            position = (self.state.media_path, self.state.player.position())
            if position != self.state.last_position_request:
                self.ports.store.save_position(*position)
                self.state.last_position_request = position

    def _seek(self, position):
        if (
            not self.state.player.isSeekable()
            or self.state.player.duration() <= 0
            or self.state.player.mediaStatus() == QMediaPlayer.MediaStatus.LoadingMedia
        ):
            self.state.resume_position = max(0, int(position))
            self.ports.sync_cue(self.state.resume_position)
            self.ports.schedule_resume_seek()
            return
        self.state.resume_position = None
        position = max(0, min(int(position), self.state.player.duration() or int(position)))
        self._set_player_position(position)
        self.ports.sync_cue(position)

    def _adjacent_media(self, delta):
        if self.state.media_path in self.ports.catalog.paths:
            index = self.ports.catalog.paths.index(self.state.media_path) + delta
            if 0 <= index < len(self.ports.catalog.paths):
                self.ports.open_media(self.ports.catalog.paths[index])

    def _navigate_cue(self, delta):
        if not self.state.timeline.cues:
            return
        index = self.state.active_index
        if index < 0:
            now = (self.state.player.position() - self.ports.settings().subtitle_offset_ms) / 1000
            if delta > 0:
                index = next(
                    (i for i, c in enumerate(self.state.timeline.cues) if c.start > now),
                    len(self.state.timeline.cues) - 1,
                )
            else:
                index = max((i for i, c in enumerate(self.state.timeline.cues) if c.start < now), default=0)
        else:
            index = max(0, min(index + delta, len(self.state.timeline.cues) - 1))
        self.ports.seek(
            round(self.state.timeline.cues[index].start * 1000) + self.ports.settings().subtitle_offset_ms
        )

    def command_playPause(self, name, p):
        if self.state.playback_details["playing"]:
            self.state.player.pause()
            self.ports.save_position()
        elif self.state.media_path:
            self.state.player.play()

    def command_seek(self, name, p):
        self.ports.seek(p["position"])

    def command_seekRelative(self, name, p):
        self.ports.seek(self.state.player.position() + p["delta"])

    def command_previousCue(self, name, p):
        self.ports.navigate_cue(-1)

    def command_nextCue(self, name, p):
        self.ports.navigate_cue(1)

    def command_repeatCue(self, name, p):
        if self.state.active_cue:
            start = round(self.state.active_cue.start * 1000) + self.ports.settings().subtitle_offset_ms
            if name == "loopCue":
                self.state.loop_a = start
                self.state.loop_b = max(
                    start + 100,
                    round(self.state.active_cue.end * 1000) + self.ports.settings().subtitle_offset_ms,
                )
                self.ports.update_playback()
            self.ports.seek(start)
            self.state.player.play()

    def command_previousMedia(self, name, p):
        self.ports.adjacent_media(-1)

    def command_nextMedia(self, name, p):
        self.ports.adjacent_media(1)

    def command_loopA(self, name, p):
        self.state.loop_a, self.state.loop_b = self.state.player.position(), -1
        self.ports.update_playback()

    def command_loopB(self, name, p):
        if self.state.loop_a < 0 or self.state.player.position() <= self.state.loop_a:
            raise ValueError("请先设置较早的 A 点")
        self.state.loop_b = self.state.player.position()
        self.ports.update_playback()

    def command_clearLoop(self, name, p):
        self.state.loop_a = self.state.loop_b = -1
        self.ports.update_playback()

    def command_setVolume(self, name, p):
        self.state.audio.setVolume(max(0, min(1, float(p["value"]))))

    def command_setMuted(self, name, p):
        self.state.audio.setMuted(bool(p["value"]))

    def command_setRate(self, name, p):
        self.state.player.setPlaybackRate(max(0.25, min(3, float(p["value"]))))

    def command_setAudioTrack(self, name, p):
        index = int(p["index"])
        if not 0 <= index < len(self.state.player.audioTracks()):
            raise ValueError("请选择可用的音轨。")
        self.state.player.setActiveAudioTrack(index)

    def command_setAudioOutput(self, name, p):
        self.ports.set_audio_output(str(p["id"]))

    def command_setSubtitleTrack(self, name, p):
        index = int(p["index"])
        if not -1 <= index < len(self.state.player.subtitleTracks()):
            raise ValueError("请选择可用的内嵌字幕轨。")
        self.state.restore_embedded_pending = False
        self.state.sources.rememberTrack(index)
        self.state.embedded.select(self.state.media_path, index, self.state.player.position())

    def command_removeDisplaySubtitle(self, name, p):
        if "display-import" in self.ports.task_keys():
            raise ValueError("正在导入播放字幕，请稍后再移除。")
        self.state.sources.remove()

    def command_saveFrame(self, name, p):
        sink = self.state.player.videoSink()
        frame = sink.videoFrame() if sink else None
        if not frame or not frame.isValid():
            raise ValueError("当前没有可保存的画面")
        image = frame.toImage()
        destination = export_path(
            self.ports.store.data_dir, datetime.now().strftime("画面-%Y%m%d-%H%M%S-%f.png"), (".png",)
        )
        if not image.save(str(destination)):
            raise ValueError("保存画面失败")
        self.ports.set_state(status=f"画面已保存：{destination}")
