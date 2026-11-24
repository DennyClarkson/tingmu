"""Real facade entry points keep validation and task errors in their own domains."""

import pytest
from PySide6.QtCore import QObject, QMetaObject, Qt, Q_ARG
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QSignalSpy

from tingmu.quick.bridge import Bridge
from tingmu.quick.main import create_qml_app
from tingmu.storage import Store


LOCAL_STATES = ("chatState", "learningState", "modelState", "workspaceState", "queueState", "mediaState")


@pytest.fixture
def bridge(qapp, tmp_path):
    instance = Bridge(Store(tmp_path / "data"))
    instance.inbox_timer.stop()
    yield instance
    instance.shutdown()


def _assert_only_error(bridge, target):
    assert getattr(bridge, target)["error"]
    for name in LOCAL_STATES:
        if name != target:
            assert not getattr(bridge, name)["error"], name
    assert bridge.notificationState["error"] == getattr(bridge, target)["error"]


@pytest.mark.parametrize(("name", "invalid", "target"), [
    ("companion_reasoning_effort", "not-supported", "chatState"),
    ("teaching_preferences", "x" * 6001, "learningState"),
    ("model", "", "modelState"),
    ("asr_backend", "not-a-backend", "workspaceState"),
    ("overlay_opacity", float("nan"), "mediaState"),
])
def test_direct_setting_validation_routes_to_its_panel(bridge, name, invalid, target):
    previous = bridge.settings[name]
    bridge.setting(name, invalid)
    _assert_only_error(bridge, target)
    assert bridge.settings[name] == previous


@pytest.mark.parametrize(("name", "payload", "target"), [
    ("summarizeNotes", {}, "learningState"),
    ("enqueue", {"paths": []}, "queueState"),
    ("prepareCurrent", {}, "queueState"),
    ("prepareLessons", {}, "queueState"),
    ("updateEntryNote", {"id": 999, "note": "test", "revision": "missing"}, "learningState"),
])
def test_actual_commands_route_rejections_to_their_panels(bridge, name, payload, target):
    bridge._settings.text_models_enabled = False
    bridge.command(name, payload)
    _assert_only_error(bridge, target)


def test_successful_model_save_clears_only_model_error(bridge):
    bridge._error("Earlier chat problem", domain="chat")
    chat_changes = QSignalSpy(bridge.chatStateChanged)
    bridge.command("saveModel", {"profile": {"provider": "not-a-provider"}})
    assert bridge.modelState["error"]
    bridge.command("saveModel", {"profile": {"provider": "ollama", "model": "synthetic-local"}})
    assert len(bridge.modelProfiles) == 1
    assert bridge.modelProfiles[0]["model"] == "synthetic-local"
    assert bridge.modelState["error"] == ""
    assert bridge.chatState["error"] == "Earlier chat problem"
    assert chat_changes.count() == 0


def test_direct_speech_key_and_collection_errors_keep_local_domains(bridge, monkeypatch):
    def reject(*args, **kwargs):
        raise ValueError("Synthetic local failure")

    monkeypatch.setattr("tingmu.external_asr.save_audio_key", reject)
    assert not bridge.saveSpeechKey("synthetic-unused")
    _assert_only_error(bridge, "workspaceState")
    monkeypatch.setattr("tingmu.external_asr.save_audio_key", lambda *args, **kwargs: None)
    assert bridge.saveSpeechKey("")
    assert not bridge.workspaceState["error"]
    monkeypatch.setattr(bridge, "_collect_word", reject)
    assert bridge.collectWord({"tokenIndex": -1}) == ""
    _assert_only_error(bridge, "learningState")


def test_player_error_callback_routes_to_media(bridge):
    bridge._media_error(QMediaPlayer.Error.ResourceError, "Synthetic media decode failure")
    _assert_only_error(bridge, "mediaState")


@pytest.mark.parametrize("task", ["display-import", "import"])
def test_import_worker_failure_routes_to_media(bridge, qtbot, task):
    def fail(*args):
        raise ValueError("Synthetic subtitle import failure")

    bridge._start_job(task, fail, lambda result: None)
    qtbot.waitUntil(lambda: task not in bridge._workers)
    _assert_only_error(bridge, "mediaState")


def _invoke(item, name, *args):
    assert QMetaObject.invokeMethod(item, name, Qt.ConnectionType.DirectConnection,
                                    *[Q_ARG("QVariant", arg) for arg in args])


def test_invalid_teaching_preferences_stay_open_with_draft_and_local_error(qapp, qtbot, tmp_path):
    app, bridge, engine = create_qml_app(tmp_path / "data")
    window = engine.rootObjects()[0]
    try:
        _invoke(window, "openModule", "chat")
        chat = window.findChild(QObject, "chatModule").property("loadedItem")
        bridge.requestDialog("teaching", chat, {})
        dialog = window.findChild(QObject, "teachingPreferencesDialog")
        qtbot.waitUntil(lambda: dialog.property("opened"))
        editor = dialog.findChild(QObject, "teachingPreferencesInput")
        save = dialog.findChild(QObject, "saveTeachingPreferences")
        before = bridge.settings["teaching_preferences"]
        draft = "x" * 6001
        editor.setProperty("text", draft)
        _invoke(save, "clicked")
        assert dialog.property("opened")
        assert editor.property("text") == draft
        assert bridge.settings["teaching_preferences"] == before
        _assert_only_error(bridge, "learningState")
        bridge._error("Unrelated chat error", domain="chat")
        editor.setProperty("text", "Prefer short examples")
        _invoke(save, "clicked")
        qtbot.waitUntil(lambda: not dialog.property("opened"))
        assert bridge.settings["teaching_preferences"] == "Prefer short examples"
        assert bridge.store.load_settings().teaching_preferences == "Prefer short examples"
        assert bridge.learningState["error"] == ""
        assert bridge.chatState["error"] == "Unrelated chat error"
        assert engine._tingmu_warnings == []
    finally:
        bridge.shutdown()
        for child in app.topLevelWindows():
            child.hide()
        engine.deleteLater()
        qtbot.wait(20)
