"""State boundaries exercised against the actual QML facade and root window."""
import json

import pytest
from PySide6.QtCore import QObject, QMetaObject, Qt, Q_ARG

from tingmu.quick.main import create_qml_app


def invoke(item, name, *args):
    assert QMetaObject.invokeMethod(item, name, Qt.ConnectionType.DirectConnection,
                                    *[Q_ARG("QVariant", arg) for arg in args])


@pytest.fixture
def quick(qapp, qtbot, tmp_path):
    app, bridge, engine = create_qml_app(tmp_path / "data")
    window = engine.rootObjects()[0]
    qtbot.wait(30)
    yield bridge, engine, window
    bridge.shutdown()
    for child in app.topLevelWindows():
        child.hide()
    engine.deleteLater()
    qtbot.wait(20)


def test_single_dialog_host_routes_from_detached_and_fullscreen_windows(quick, qtbot):
    bridge, engine, window = quick
    for name in ("modelManagerWindow", "teachingPreferencesDialog", "subtitleCorrectionDialog"):
        assert len(window.findChildren(QObject, name)) == 1
    invoke(window, "openModule", "chat")
    chat = window.findChild(QObject, "chatModule").property("loadedItem")
    bridge.requestDialog("models", chat, {})
    manager = window.findChild(QObject, "modelManagerWindow")
    assert manager.transientParent() == chat.window()
    manager.hide()
    bridge.requestDialog("teaching", chat, {})
    dialog = window.findChild(QObject, "teachingPreferencesDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    assert dialog.property("parent").window() == chat.window()
    invoke(dialog, "close")
    player = window.findChild(QObject, "playerModule").property("loadedItem")
    invoke(player, "toggleFullscreen")
    overlay = window.findChild(QObject, "companionOverlay")
    bridge.requestDialog("teaching", overlay, {})
    qtbot.waitUntil(lambda: dialog.property("opened"))
    assert dialog.property("parent").window() == overlay.window()
    invoke(dialog, "close")
    invoke(player, "toggleFullscreen")
    assert engine._tingmu_warnings == []


def test_saved_layout_remains_bound_after_local_edits(quick, qtbot):
    bridge, engine, window = quick
    invoke(window, "savePreference", "local", "first")
    assert bridge.saveLayout({"preferences": {"external": "更新"}})
    value = window.property("savedLayout")
    if hasattr(value, "toVariant"):
        value = value.toVariant()
    assert value == {"preferences": {"external": "更新"}}
    invoke(window, "savePreference", "second", "保留外部更新")
    invoke(window, "saveWorkspace")
    assert bridge.layout["preferences"] == {"external": "更新", "second": "保留外部更新"}
    assert engine._tingmu_warnings == []


def test_layout_limit_reports_error_and_keeps_large_draft(quick):
    bridge, _, _ = quick
    text = "中文草稿，不应丢失。" * 20000
    assert bridge.writeDraft("notebookDrafts", "yue", {"text": text, "revision": "base"})
    assert bridge.saveLayout({"preferences": {"small": "中文"}})
    previous = bridge.layout.copy()
    assert not bridge.saveLayout({"preferences": {"oversized": text}})
    assert "64 KB" in bridge.state["error"]
    assert bridge.layout == previous
    assert bridge.loadDrafts("notebookDrafts")["yue"] == {"text": text, "revision": "base"}
    stored = json.loads((bridge.store.data_dir / "quick-layout.json").read_text("utf-8"))
    assert stored == previous


def test_stale_bridge_language_and_preference_edits_keep_external_roles(quick):
    from tingmu.storage import Store

    bridge, _, _ = quick
    other = Store(bridge.store.data_dir)
    registry = other.model_registry
    old = registry.save_profile({"provider": "ollama", "model": "old"})
    new = registry.save_profile({"provider": "ollama", "model": "new"})
    registry.assign(["annotation", "review", "companion"], old["id"])
    bridge._settings = bridge.store.load_settings()
    registry.assign(["annotation", "review", "companion"], new["id"])
    bridge.set_languages("zh-CN", "en")
    assert other.load_settings().annotation_profile_id == new["id"]
    assert bridge.settings["companion_profile_id"] == new["id"]
    registry.assign(["annotation", "review", "companion"], old["id"])
    bridge.setting("teaching_preferences", "更详细的例句")
    assert other.load_settings().companion_profile_id == old["id"]
    assert bridge.settings["review_profile_id"] == old["id"]
    assert "_loaded_roles" not in bridge.settings
