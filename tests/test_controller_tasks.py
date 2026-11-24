"""Composed domains share a single task lifetime and injectable native dialogs."""

import threading
from dataclasses import fields
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tingmu.quick.bridge import Bridge
from tingmu.quick.settings_controller import SettingsController, SettingsPorts, SettingsState
from tingmu.models import AppSettings
from tingmu.batch import PreparationQueue
from tingmu.storage import Store
from test_domain_events import bridge as bridge


def test_task_manager_enforces_duplicate_and_material_exclusion_and_cancellation(bridge, qtbot):
    entered, release = threading.Event(), threading.Event()
    results = []

    def work(progress, cancelled):
        entered.set()
        assert release.wait(5)
        return cancelled()

    bridge.start_task("import", work, results.append)
    try:
        assert entered.wait(5)
        with pytest.raises(ValueError):
            bridge.start_task("import", work, results.append)
        with pytest.raises(ValueError):
            bridge.start_task("queue", work, results.append)
        assert bridge.cancel_task("import")
        assert not bridge.cancel_task("missing")
    finally:
        release.set()
    qtbot.waitUntil(lambda: results == [True])
    assert "import" not in bridge.task_keys()


def test_shutdown_cancels_pending_pool_work_before_closing_store(bridge, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    pool = bridge.task_manager.pool("shutdown-test")

    def running():
        entered.set()
        assert release.wait(5)
        return "drained"

    active = pool.submit(running)
    assert entered.wait(5)
    pending = pool.submit(lambda: pytest.fail("queued work ran during shutdown"))
    pending.add_done_callback(lambda _: release.set())
    assert bridge.task_manager.background_count >= 2
    closed = []
    close = bridge.store.close

    def close_after_workers():
        assert active.done() and pending.cancelled()
        closed.append(True)
        close()

    monkeypatch.setattr(bridge.store, "close", close_after_workers)
    try:
        bridge.shutdown()
        assert closed == [True]
        assert active.result() == "drained"
        assert bridge.task_manager.background_count == 0
        with pytest.raises(ValueError, match="关闭"):
            pool.submit(lambda: None)
        with pytest.raises(ValueError, match="关闭"):
            bridge.start_task("chat", lambda *_: None, lambda _: None)
    finally:
        release.set()


def test_native_file_dialog_is_injected_and_updates_only_selected_library(qapp, tmp_path):
    media = tmp_path / "synthetic.mkv"
    media.touch()
    calls = []

    def select(*args):
        calls.append(args)
        return [str(media)], ""

    bridge = Bridge(Store(tmp_path / "study"), dialogs=SimpleNamespace(getOpenFileNames=select))
    try:
        bridge.command("addFiles", {})
        assert len(calls) == 1
        assert bridge.catalog.paths == [str(media.resolve())]
        assert bridge.media_path == ""
        assert bridge.settings_snapshot().library_path == str(tmp_path)
    finally:
        bridge.shutdown()


def test_controller_state_aliases_refer_to_owned_domain_data(bridge):
    assert bridge._workers is bridge.task_manager.state.workers
    assert bridge._jobs is bridge.queue_controller.state.jobs
    assert bridge._library is bridge.library_controller.state.library
    assert bridge._player is bridge.playback_controller.state.player
    bridge._loop_a = 1200
    assert bridge.playback_controller.state.loop_a == 1200
    bridge.playback_controller.state.loop_b = 2400
    assert bridge._loop_b == 2400


def test_library_observes_replaced_queue_alias_without_reading_old_instance(bridge, tmp_path, monkeypatch):
    media = tmp_path / "queued.mkv"
    media.touch()
    old_queue = bridge.queue
    replacement = PreparationQueue(bridge.store)
    replacement.enqueue([media], bridge.settings_snapshot())
    monkeypatch.setattr(old_queue, "snapshot", lambda: pytest.fail("read stale queue instance"))
    bridge.queue = replacement
    bridge._publish_library([str(media)], bridge.settings_snapshot().language,
                            {str(media): {"status": "unprepared", "cueCount": 0}})
    assert bridge.queue_controller.state.queue is replacement
    assert bridge.library_controller.ports.queue() is replacement
    assert bridge.library[0]["path"] == str(media)
    assert bridge.library[0]["status"] == "queued"


def test_settings_controller_validates_before_persistence_and_revokes_changed_audio_endpoint():
    settings = AppSettings(
        asr_backend="external", asr_endpoint="https://old.example/v1", allow_audio_upload=True
    )
    ports = {field.name: Mock() for field in fields(SettingsPorts)}
    ports["settings"] = lambda: settings
    controller = SettingsController(SettingsState(), SettingsPorts(**ports))
    before = settings.teaching_preferences
    controller.setting("teaching_preferences", "invalid\x00value")
    assert settings.teaching_preferences == before
    ports["store"].save_settings.assert_not_called()
    assert ports["error"].call_args.kwargs == {"domain": "learning"}
    controller.setting("asr_endpoint", "https://new.example/v1")
    assert settings.asr_endpoint == "https://new.example/v1"
    assert not settings.allow_audio_upload
    ports["store"].save_settings.assert_called_once_with(settings)
    ports["settingsChanged"].emit.assert_called_once_with()


def test_speech_discovery_owns_settings_snapshot_and_ignores_changed_endpoint(monkeypatch, tmp_path):
    settings = AppSettings(asr_backend="external", asr_endpoint="https://old.example/v1")
    ports = {field.name: Mock() for field in fields(SettingsPorts)}
    ports.update(
        settings=lambda: settings, task_keys=lambda: frozenset(), store=SimpleNamespace(data_dir=tmp_path)
    )
    seen = []
    monkeypatch.setattr(
        "tingmu.external_asr.list_models",
        lambda frozen, root: seen.append(frozen.asr_endpoint) or ["synthetic"],
    )
    controller = SettingsController(SettingsState(), SettingsPorts(**ports))
    controller.checkSpeechModels()
    key, work, complete = ports["start_job"].call_args.args
    assert key == "speech-models"
    settings.asr_endpoint = "https://new.example/v1"
    result = work(lambda _: None, lambda: False)
    assert seen == ["https://old.example/v1"]
    ports["set_state"].reset_mock()
    complete(result)
    ports["set_state"].assert_not_called()
