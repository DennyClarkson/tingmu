"""Display-only subtitle tracks and the playback audio output selector."""

from bisect import bisect_right
import hashlib
import json
import os
from pathlib import Path

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtMultimedia import QMediaDevices

from tingmu.media import load_subtitles
from tingmu.storage import atomic_json


class DisplayTrack:
    def __init__(self, name="", rows=()):
        if not isinstance(name, str):
            raise ValueError("字幕文件名无效。")
        self.name = name
        self.rows = sorted(rows, key=lambda row: row[0])
        self.starts = [row[0] for row in self.rows]
        self.last_ends = []
        last = 0
        for start, end, text in self.rows:
            if not isinstance(start, int) or not isinstance(end, int) or end <= start or not isinstance(text, str):
                raise ValueError("播放字幕中的时间或文本无效。")
            last = max(last, end)
            self.last_ends.append(last)

    def active(self, milliseconds):
        stop = bisect_right(self.starts, milliseconds)
        start = bisect_right(self.last_ends, milliseconds, 0, stop)
        return [row for row in self.rows[start:stop] if milliseconds < row[1]]

    def at(self, milliseconds):
        return "\n".join(row[2] for row in self.active(milliseconds))


class PlaybackSources(QObject):
    trackChanged = Signal()
    textChanged = Signal()
    outputsChanged = Signal()

    def __init__(self, store, player, output, parent):
        super().__init__(parent)
        self.store = store
        self.player = player
        self.output = output
        self.track = DisplayTrack()
        self._cue = {}
        self._media = ""
        self._choice = {"show": False, "index": 0}
        self.devices = QMediaDevices(self)
        self._outputs = []
        self._wanted = str(store.load_settings().audio_output_id or "")
        self.devices.audioOutputsChanged.connect(self.refresh_outputs)
        self.refresh_outputs()
        player.positionChanged.connect(self.update_position)

    def refresh_outputs(self):
        devices = self.devices.audioOutputs()
        default = self.devices.defaultAudioOutput()
        self._outputs = [{"id": "", "name": "跟随系统默认" + (f" · {default.description()}" if not default.isNull() else "")}]
        selected = default
        for device in devices:
            identity = bytes(device.id()).hex()
            self._outputs.append({"id": identity, "name": device.description()})
            if identity == self._wanted:
                selected = device
        if not selected.isNull() and self.output.device().id() != selected.id():
            self.output.setDevice(selected)
        self.outputsChanged.emit()

    @Property("QVariantList", notify=outputsChanged)
    def audioOutputs(self):
        return self._outputs

    @Property(int, notify=outputsChanged)
    def outputIndex(self):
        return next((i for i, row in enumerate(self._outputs) if row["id"] == self._wanted), 0)

    def choose_output(self, identity):
        if identity not in {row["id"] for row in self._outputs}:
            raise ValueError("该音频设备已断开，请重新选择。")
        self._wanted = identity
        self.refresh_outputs()

    def _file(self, media):
        root = self.store.data_dir
        path = Path(media).resolve()
        identity = path.relative_to(root) if path.is_relative_to(root) else path
        key = hashlib.sha256(os.path.normcase(str(identity)).encode("utf-8")).hexdigest()
        target = (root / "playback-subtitles" / f"{key}.json").resolve()
        if not target.is_relative_to(root):
            raise ValueError("字幕存储目录必须位于工作区内。")
        return target

    def open(self, media):
        self._media = media
        self._choice = {"show": False, "index": 0}
        try:
            saved = json.loads(self._choice_file().read_text("utf-8"))
            self._choice = {"show": saved["show"] is True, "index": max(0, int(saved["index"]))}
        except (OSError, ValueError, KeyError, TypeError):
            pass
        track = DisplayTrack()
        path = self._file(media)
        if path.is_file():
            try:
                saved = json.loads(path.read_text("utf-8"))
                track = DisplayTrack(saved["name"], saved["rows"])
            except (OSError, ValueError, TypeError, KeyError, IndexError):
                pass
        self.install(track)

    def import_file(self, media, path):
        path = Path(path)
        rows = [(round(cue.start * 1000), round(cue.end * 1000), cue.text) for cue in load_subtitles(path)]
        if not rows:
            raise ValueError("字幕文件中没有有效内容。")
        track = DisplayTrack(path.name, rows)
        atomic_json(self._file(media), {"name": track.name, "rows": track.rows})
        return track

    def install(self, track):
        self.track = track
        self.trackChanged.emit()
        self.update_position(self.player.position())

    def remove(self):
        if self._media:
            self._file(self._media).unlink(missing_ok=True)
        self.install(DisplayTrack())

    @Property(str, notify=trackChanged)
    def importedName(self):
        return self.track.name

    @Property("QVariantMap", notify=trackChanged)
    def embeddedChoice(self):
        return self._choice

    def _choice_file(self):
        return self._file(self._media).with_suffix(".track.json")

    @Slot(int)
    def rememberTrack(self, index):
        if self._media:
            choice = {"show": index >= 0, "index": index if index >= 0 else self._choice["index"]}
            atomic_json(self._choice_file(), choice)
            self._choice = choice
            self.trackChanged.emit()

    @Property(str, notify=textChanged)
    def importedText(self):
        return self._cue.get("text", "")

    @Property("QVariantMap", notify=textChanged)
    def importedCue(self):
        return self._cue

    @Slot(int)
    def update_position(self, value):
        rows = self.track.active(value)
        current = {}
        if rows:
            start = max(row[0] for row in rows)
            current = {"id": f"imported:{self.track.name}:{start}", "start": start / 1000,
                       "end": min(row[1] for row in rows) / 1000, "text": "\n".join(row[2] for row in rows),
                       "trackName": self.track.name}
        if current != self._cue:
            self._cue = current
            self.textChanged.emit()
