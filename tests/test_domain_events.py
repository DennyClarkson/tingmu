"""UI notifications, idle work, and cold-library reads have explicit boundaries."""
import threading

import pytest
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QSignalSpy

from tingmu.models import Cue, LanguagePair, StudyDocument
from tingmu.quick.bridge import Bridge
from tingmu.storage import Store


@pytest.fixture
def bridge(qapp, qtbot, tmp_path):
    result = Bridge(Store(tmp_path / "data"))
    result.inbox_timer.stop()
    qtbot.waitUntil(lambda: result._library_future is None and result._inbox_thread is not None
                    and not result._inbox_thread.is_alive() and not result.mediaInbox["scanning"])
    yield result
    result.shutdown()


def test_progress_notifies_only_its_domain_and_legacy_projection(bridge):
    signals = {name: QSignalSpy(getattr(bridge, name + "StateChanged")) for name in
               ("media", "queue", "chat", "download", "learning", "model", "workspace", "notification")}
    legacy = QSignalSpy(bridge.stateChanged)
    bridge._job_progress("model-download", "received 10 bytes")
    assert signals["download"].count() == signals["notification"].count() == legacy.count() == 1
    assert all(spy.count() == 0 for name, spy in signals.items() if name not in {"download", "notification"})
    assert bridge.state["downloadMessage"] == bridge.downloadState["downloadMessage"]
    bridge._set_state(chatBusy=True)
    assert signals["chat"].count() == 1 and signals["media"].count() == 0
    bridge._set_state(chatBusy=True)
    assert signals["chat"].count() == 1


def test_errors_are_local_and_command_routing_keeps_compatibility(bridge, monkeypatch):
    def fail(name, payload):
        raise ValueError(name)
    monkeypatch.setattr(bridge, "_command", fail)
    bridge.command("saveModel", {})
    assert bridge.modelState["error"] == "saveModel"
    assert bridge.chatState["error"] == bridge.learningState["error"] == bridge.workspaceState["error"] == ""
    bridge.command("sendChat", {})
    assert bridge.chatState["error"] == bridge.state["error"] == "sendChat"
    assert bridge.modelState["error"] == "saveModel"
    bridge._set_state(domain="chat", error="")
    assert bridge.chatState["error"] == "" and bridge.modelState["error"] == "saveModel"
    snapshot = bridge.modelState
    snapshot["error"] = "caller mutation"
    assert bridge.modelState["error"] == "saveModel"


def test_idle_queue_has_no_poll_and_position_timer_runs_only_while_playing(bridge, qtbot, monkeypatch):
    assert bridge.timer.isSingleShot() and not bridge.timer.isActive()
    assert not bridge.position_timer.isActive() and not bridge.diagnostic_timer.isActive()
    timer = QSignalSpy(bridge.timer.timeout)
    qtbot.wait(450)
    assert timer.count() == 0
    monkeypatch.setattr(bridge._player, "playbackState", lambda: QMediaPlayer.PlaybackState.PlayingState)
    bridge._update_playback()
    assert bridge.position_timer.isActive()
    monkeypatch.setattr(bridge._player, "playbackState", lambda: QMediaPlayer.PlaybackState.PausedState)
    bridge._update_playback()
    assert not bridge.position_timer.isActive()
    bridge.setting("developer_mode", True)
    assert bridge.diagnostic_timer.isActive()
    bridge.setting("developer_mode", False)
    assert not bridge.diagnostic_timer.isActive()


def test_inbox_reuses_worker_without_blocking_ui_or_marking_user_job(bridge, qtbot, monkeypatch):
    identities = []
    def scan(**kwargs):
        identities.append(threading.get_ident())
        return {"ready": {}, "present": [], "waiting": [], "error": ""}
    monkeypatch.setattr(bridge._inbox_scanner, "scan", scan)
    for _ in range(3):
        bridge._scan_media_inbox()
        qtbot.waitUntil(lambda: not bridge._inbox_thread.is_alive() and not bridge.mediaInbox["scanning"])
    assert len(identities) == 3 and len(set(identities)) == 1
    assert identities[0] != threading.get_ident()
    assert not bridge.state["busy"] and not bridge._workers


def test_cold_library_reads_are_off_ui_and_stale_language_is_not_published(bridge, qtbot, monkeypatch, tmp_path):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"synthetic")
    bridge.catalog.add([media])
    document = StudyDocument(str(media), LanguagePair(), [Cue("one", 0, 1, "source")])
    bridge.store.save_document(document)
    entered, release = threading.Event(), threading.Event()
    reads = []
    original = bridge.store.load_document
    def slow_load(path, language):
        reads.append((threading.get_ident(), language.target))
        if language.target == "yue":
            entered.set()
            assert release.wait(5)
        return original(path, language)
    monkeypatch.setattr(bridge.store, "load_document", slow_load)
    monkeypatch.setattr(bridge.store, "load_position", lambda *_: pytest.fail("must use bulk positions"))
    bridge._refresh_library()
    assert entered.wait(2)
    try:
        assert bridge.library[0]["path"] == str(media)
        bridge.set_languages("zh-CN", "en")
        bridge._set_state(status="UI is responsive")
        assert bridge.state["status"] == "UI is responsive"
    finally:
        release.set()
    qtbot.waitUntil(lambda: bridge._library_future is None and bridge._library_pending is None)
    assert bridge.library[0]["cueCount"] == 0
    assert [language for _, language in reads] == ["yue", "en"]
    assert all(ident != threading.get_ident() for ident, _ in reads)
    assert len({ident for ident, _ in reads}) == 1


def test_queue_notification_updates_without_a_periodic_timer(bridge, qtbot, tmp_path):
    media = tmp_path / "queue.mkv"
    media.write_bytes(b"synthetic")
    assert not bridge.timer.isActive()
    bridge.queue.enqueue([media], bridge._settings)
    qtbot.waitUntil(lambda: len(bridge.jobs) == 1)
    assert bridge.jobModel.rowCount() == 1 and not bridge.timer.isActive()
    bridge.queue.remove(bridge.jobs[0]["id"])
    qtbot.waitUntil(lambda: not bridge.jobs)
    assert bridge.jobModel.rowCount() == 0 and not bridge.timer.isActive()
