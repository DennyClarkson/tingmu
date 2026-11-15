"""Real QML interaction checks, not string snapshots of the implementation."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPointF, QMetaObject, Qt, Q_ARG
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest

from tingmu.models import Cue, LanguagePair, StudyDocument
from tingmu.quick.main import create_qml_app


def invoke(item, method, *args):
    assert QMetaObject.invokeMethod(item, method, Qt.ConnectionType.DirectConnection,
                                    *[Q_ARG("QVariant", value) for value in args])


@pytest.fixture
def quick(qapp, tmp_path, qtbot):
    for font in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/consola.ttf"):
        if os.path.isfile(font):
            QFontDatabase.addApplicationFont(font)
    app, bridge, engine = create_qml_app(tmp_path / "data")
    window = engine.rootObjects()[0]
    qtbot.wait(100)
    yield app, bridge, engine, window
    bridge.shutdown()
    for child in app.topLevelWindows():
        child.hide()
    engine.deleteLater()
    qtbot.wait(20)


def find(window, name):
    item = window.findChild(QObject, name)
    assert item is not None, name
    return item


def test_watch_prepare_and_independent_chat_share_existing_modules(quick, qtbot):
    app, bridge, engine, window = quick
    assert window.isVisible()
    assert window.property("viewMode") == "watch"
    player = find(window, "playerModule").property("loadedItem")
    sink = bridge.player.videoSink()
    assert sink is not None
    invoke(window, "changeView", "prepare")
    qtbot.wait(80)
    assert window.property("viewMode") == "prepare"
    invoke(window, "openModule", "chat")
    qtbot.wait(80)
    assert find(window, "chatModule").property("detached")
    invoke(window, "changeView", "watch")
    qtbot.wait(80)
    assert find(window, "playerModule").property("loadedItem") is player
    assert bridge.player.videoSink() is sink
    assert engine._tingmu_warnings == []


def test_overlay_stays_fixed_and_bottom_right_drag_resizes_then_persists(quick, qtbot):
    app, bridge, engine, window = quick
    bridge._set_document(StudyDocument("video.mkv", LanguagePair(), [
        Cue("a", 1, 2, "短句", explanation="很短"),
        Cue("b", 3, 5, "較長的一句" * 12, explanation="这是多行讲解。" * 50)]))
    player = find(window, "playerModule").property("loadedItem")
    video = find(window, "playerVideo")
    sink = bridge.player.videoSink()
    invoke(player, "toggleFullscreen")
    qtbot.wait(100)
    fullscreen = find(window, "fullscreenWindow")
    overlay = find(window, "companionOverlay")
    assert fullscreen.isVisible()
    assert video.window() is fullscreen
    old = (overlay.width(), overlay.height())
    for position in (1100, 3500, 2200, 1100):
        bridge._sync_cue(position)
        qtbot.wait(20)
        assert (overlay.width(), overlay.height()) == old
    grip = find(window, "overlayResizeGrip")
    start = grip.mapToScene(QPointF(grip.width()/2, grip.height()/2)).toPoint()
    QTest.mousePress(fullscreen, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(fullscreen, start + QPointF(70, 50).toPoint(), delay=20)
    QTest.mouseRelease(fullscreen, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       start + QPointF(70, 50).toPoint())
    qtbot.wait(450)
    assert overlay.width() > old[0] and overlay.height() > old[1]
    assert bridge.layout["overlay"]["width"] == overlay.width()
    assert bridge.layout["overlay"]["height"] == overlay.height()
    invoke(player, "toggleFullscreen")
    qtbot.wait(50)
    assert not fullscreen.isVisible()
    assert video.window() is window
    assert bridge.player.videoSink() is sink
    assert engine._tingmu_warnings == []


def test_modules_swap_without_recreating_player_and_restore_geometry(quick, qtbot):
    app, bridge, engine, window = quick
    player_dock = find(window, "playerModule")
    library_dock = find(window, "libraryModule")
    player = player_dock.property("loadedItem")
    point = library_dock.mapToGlobal(QPointF(library_dock.width()/2, library_dock.height()/2))
    invoke(window, "finishDrop", "player", point)
    qtbot.wait(400)
    slots = window.property("moduleSlots").toVariant()
    assert slots["player"] == "library"
    assert slots["library"] == "player"
    assert player_dock.property("loadedItem") is player
    assert bridge.layout["viewLayouts"]["watch"]["player"] == "library"
    invoke(player_dock, "detach")
    qtbot.wait(40)
    assert player_dock.property("detached")
    invoke(player_dock, "detach")
    assert not player_dock.property("detached")
    assert engine._tingmu_warnings == []


def test_all_module_settings_open_without_runtime_errors(quick, qtbot):
    app, bridge, engine, window = quick
    for name in ("libraryModule", "queueModule", "playerModule", "subtitleModule", "companionModule", "chatModule"):
        dock = find(window, name)
        invoke(dock, "detach")
        panel = dock.property("loadedItem")
        invoke(panel, "openSettings")
        qtbot.wait(70)
        opened = [obj for obj in panel.findChildren(QObject)
                  if obj.metaObject().indexOfProperty("opened") >= 0 and obj.property("opened")]
        assert opened, name
        for popup in opened:
            invoke(popup, "close")
        invoke(dock, "detach")
    assert engine._tingmu_warnings == []


def test_developer_window_lists_local_events_and_hides_when_disabled(quick, qtbot):
    app, bridge, engine, window = quick
    dev = find(window, "developerWindow")
    assert not dev.isVisible()
    bridge.setting("developer_mode", True)
    invoke(dev, "open")
    qtbot.wait(100)
    assert dev.isVisible()
    assert bridge.diagnostics["logLevel"] == "DEBUG"
    assert find(window, "developerLogList").property("count") > 0
    bridge.setting("developer_mode", False)
    qtbot.wait(50)
    assert not dev.isVisible()
    assert engine._tingmu_warnings == []
