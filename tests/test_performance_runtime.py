"""Regressions for the scanner, Qt state bridge and transport hot paths."""
from pathlib import Path

import pytest
from PySide6.QtMultimedia import QMediaPlayer

from tingmu import media_inbox
from tingmu.models import AppSettings, Cue, StudyDocument
from tingmu.quick.bridge import Bridge
from tingmu.storage import Store


@pytest.fixture
def bridge(qapp, qtbot, tmp_path):
    result = Bridge(Store(tmp_path / "data"))
    qtbot.waitUntil(lambda: bool(result._library_state.watcher.directories()))
    try:
        yield result
    finally:
        result.shutdown()


def test_replaced_ready_file_and_sidecar_must_pass_quiet_and_writer_checks(tmp_path, monkeypatch):
    media = tmp_path / "sample.mp4"
    sidecar = media.with_suffix(".srt")
    media.write_bytes(b"video")
    sidecar.write_bytes(b"words")
    checked = []
    monkeypatch.setattr(media_inbox, "_not_being_written", lambda path: checked.append(path) or True)
    scanner = media_inbox.InboxScanner(tmp_path, quiet_seconds=2)
    assert scanner.scan(now=0)["waiting"] == [str(media)]
    assert str(media) in scanner.scan(now=2)["ready"]
    assert str(media) in scanner.scan(now=3)["ready"]
    assert checked == [media, sidecar]
    sidecar.write_bytes(b"new words")
    assert scanner.scan(now=4)["waiting"] == [str(media)]
    assert str(media) in scanner.scan(now=6)["ready"]
    assert checked == [media, sidecar] * 2
    media.rename(tmp_path / "original")
    media.write_bytes(b"other")
    assert scanner.scan(now=7)["waiting"] == [str(media)]


def test_ready_directory_replaced_by_junction_is_excluded(tmp_path):
    import os

    root = tmp_path / "inbox"
    folder = root / "season"
    folder.mkdir(parents=True)
    (folder / "sample.mp4").write_bytes(b"video")
    scanner = media_inbox.InboxScanner(root, quiet_seconds=0)
    assert len(scanner.scan()["ready"]) == 1
    outside = tmp_path / "moved"
    folder.rename(outside)
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(folder))
    else:
        folder.symlink_to(outside, target_is_directory=True)
    try:
        assert scanner.scan()["present"] == []
        assert str(folder) not in scanner.directories
    finally:
        if os.name == "nt":
            folder.rmdir()
        else:
            folder.unlink()


def test_preflight_reuses_model_and_path_results_and_observes_changes(tmp_path, monkeypatch):
    from tingmu.workspace_models import ModelReadiness

    paths = [tmp_path / (name + ".mp4") for name in ("one", "two")]
    for media in paths:
        media.write_bytes(b"video")
    scanner = media_inbox.InboxScanner(tmp_path, quiet_seconds=0)
    settings = AppSettings(asr_model="large-v3")
    lookups, probes = [], []
    find_subtitle = media_inbox.find_subtitle
    monkeypatch.setattr(media_inbox, "find_subtitle",
                        lambda media, lang: lookups.append(media) or find_subtitle(media, lang))
    installed = False

    def readiness(model):
        probes.append(model)
        return ModelReadiness(installed, "", "")

    monkeypatch.setattr("tingmu.workspace_models.model_readiness", readiness)

    def check(generation=0):
        ready = scanner.scan()["ready"]
        return scanner.preparation_reasons(settings, ready, generation, lambda: False)

    assert all(check().values()) and all(check().values())
    assert len(lookups) == 2 and len(probes) == 1
    paths[0].with_suffix(".srt").write_text("captions")
    assert check()[str(paths[0])] == ""
    assert len(probes) == 1
    installed = True
    assert not any(check(generation=1).values())
    assert len(probes) == 2
    settings.asr_backend = "external"
    assert "允许" in check()[str(paths[1])]
    paths[0].with_suffix(".srt").unlink()
    assert all(check().values())


def test_watcher_discovers_nested_changes_rearms_and_closes(bridge, qtbot):
    state = bridge._library_state
    assert bridge.inbox_timer.interval() >= 15000
    folder = bridge.catalog.media_dir / "season"
    folder.mkdir()
    qtbot.waitUntil(lambda: folder in map(Path, state.watcher.directories()), timeout=3000)
    media = folder / "new.mkv"
    media.write_bytes(b"synthetic")
    qtbot.waitUntil(lambda: bridge.mediaInbox["waiting"] == 1, timeout=3000)
    assert bridge.inbox_timer.interval() == 2000
    bridge._inbox_scanner.quiet_seconds = 0
    bridge.scan_workspace_media()
    qtbot.waitUntil(lambda: str(media) in bridge.catalog.automatic)
    assert bridge.inbox_timer.interval() >= 15000
    signals = []
    bridge.mediaInboxChanged.connect(lambda: signals.append(True))
    with qtbot.waitSignal(bridge._inboxCompleted):
        bridge.library_controller._scan_media_inbox(background=True)
    assert not signals
    bridge.shutdown()
    assert not state.watcher.directories() and not bridge.inbox_timer.isActive()
    assert not state.watch_timer.isActive()


def test_continuous_ticks_publish_last_position_with_realtime_cues_and_immediate_seek(bridge, qtbot, monkeypatch):
    bridge._set_document(StudyDocument("synthetic.mp4", bridge._settings.language,
                                       [Cue("a", 0, 1, "甲"), Cue("b", 1, 2, "乙")]))
    playing = QMediaPlayer.PlaybackState.PlayingState
    monkeypatch.setattr(bridge.player, "playbackState", lambda: playing)
    monkeypatch.setattr(bridge.player, "duration", lambda: 10000)
    monkeypatch.setattr(bridge.player, "isSeekable", lambda: True)
    bridge._update_playback()
    values, embedded = [], []
    bridge.playbackChanged.connect(lambda: values.append(bridge.playbackPosition))
    monkeypatch.setattr(bridge._embedded, "position", embedded.append)
    for position in (100, 150, 1100):
        bridge._position_changed(position)
    assert embedded == [100, 150, 1100]
    assert bridge.activeCue["id"] == "b" and values == [100]
    qtbot.waitUntil(lambda: values == [100, 1100])
    bridge._position_changed(1200)
    monkeypatch.setattr(bridge.player, "setPosition", bridge._position_changed)
    bridge._seek(400)
    assert values[-1] == 400 and bridge.activeCue["id"] == "a"
    bridge._position_changed(10000)
    assert values[-1] == 10000 and not bridge.playback_controller.notify_timer.isActive()
    bridge._position_changed(500)
    playing = QMediaPlayer.PlaybackState.PausedState
    monkeypatch.setattr(bridge.player, "position", lambda: 500)
    bridge._update_playback()
    assert values[-1] == 500 and not bridge.playback_controller.notify_timer.isActive()


def test_domain_qt_and_python_properties_isolate_nested_snapshot_revisions(bridge):
    source = {"bytes": [1, 2]}
    bridge._set_state(downloadProgress=source)
    old = bridge._domain_states.qt_view("download")
    source["bytes"].append(3)
    assert bridge._domain_states.qt_view("download") is old
    bridge.downloadState["downloadProgress"]["bytes"].clear()
    bridge.property("downloadState")["downloadProgress"]["bytes"].clear()
    assert old["downloadProgress"]["bytes"] == [1, 2]
    bridge._set_state(downloadProgress={"bytes": [8]})
    assert old["downloadProgress"]["bytes"] == [1, 2]
    assert bridge.downloadState["downloadProgress"]["bytes"] == [8]
