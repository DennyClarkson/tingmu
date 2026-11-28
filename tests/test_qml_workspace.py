"""Real QML interaction checks, not string snapshots of the implementation."""

import os
from threading import Event

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
    bridge.setting("developer_mode", True)
    window = engine.rootObjects()[0]
    qtbot.wait(100)
    yield app, bridge, engine, window
    window.setProperty("closingSession", True)
    window.close()
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


def test_student_companion_and_chat_share_sidebar_above_live_subtitles(quick, qtbot):
    _, bridge, engine, window = quick
    bridge.setting("developer_mode", False)
    window.setProperty("studying", True)
    bridge._set_document(StudyDocument("lesson.mkv", LanguagePair(), [
        Cue("one", 0, 2, "第一句"), Cue("two", 3, 6, "第二句")]))
    bridge._sync_cue(3500)
    qtbot.wait(100)

    def click(name):
        item = find(window, name)
        assert item.isVisible() and item.isEnabled()
        invoke(item, "click")
        qtbot.wait(50)

    companion = find(window, "companionSurface")
    subtitles = find(window, "subtitlesSurface")
    chat = find(window, "chatSurface")
    subtitle_list = find(window, "subtitleList")
    panel = find(window, "subtitleModule").property("loadedItem")
    assert companion.isVisible() and subtitles.isVisible() and not chat.isVisible()
    assert companion.mapToScene(QPointF(0, companion.height())).y() < subtitles.mapToScene(QPointF()).y()
    assert panel.property("follow") and panel.property("activeId") == "two"
    assert subtitle_list.property("count") == 2

    click("learningViewButton")
    assert find(window, "learningWorkspace").isVisible() and not subtitles.isVisible()
    assert not find(window, "prepareViewButton").isVisible()
    for tab in ("notesTab", "reviewTab", "collectionsTab"):
        click(tab)
    click("watchViewButton")
    assert subtitles.isVisible() and not find(window, "learningWorkspace").isVisible()

    click("studentChatTab")
    assert chat.isVisible() and subtitles.isVisible() and not companion.isVisible()
    assert find(window, "newChatButton").isVisible()
    find(window, "subtitleSearch").setProperty("text", "第一句")
    qtbot.waitUntil(lambda: subtitle_list.property("count") == 1)
    click("studentCueTab_chat")
    assert companion.isVisible() and subtitle_list.property("count") == 1
    dock = find(window, "subtitleModule")
    host = subtitles.parentItem()
    click("subtitlesDetach")
    assert dock.property("floatingPanel") and subtitles.parentItem() != host
    click("subtitlesDetach")
    assert subtitles.parentItem() == host and subtitles.isVisible()
    assert not dock.property("detached")
    click("subtitlesSettings")
    popup = find(panel, "moduleSettingsPopup")
    assert popup.property("opened")
    invoke(popup, "close")
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
    assert fullscreen.contentItem().isAncestorOf(video)
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
    assert window.contentItem().isAncestorOf(video)
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
        popup = find(panel, "playerSettingsPopup" if name == "playerModule" else "moduleSettingsPopup")
        assert popup.property("opened"), name
        invoke(popup, "close")
        qtbot.waitUntil(lambda: not popup.property("opened"))
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


def test_manual_model_size_confirmation_and_live_transfer(quick, qtbot, monkeypatch):
    from tingmu.task_progress import Progress
    _, bridge, engine, window = quick
    setup = find(window, "workspaceSetup")
    confirm = find(window, "manualModelDownload")
    release = Event()
    calls = []
    monkeypatch.setattr("tingmu.quick.workspace_controller.model_inventory",
                        lambda _: {"installed": False, "download_bytes": 2**31, "estimated_download_bytes": 2**32})

    def download(model, progress, cancel):
        calls.append(model)
        progress(Progress("下载测试文件", component="测试文件", completed=float(2**31), total=float(2**32),
                          received=65536., rate=32768.))
        release.wait(4)
        return model

    monkeypatch.setattr("tingmu.quick.workspace_controller.download_model", download)
    try:
        invoke(setup, "open")
        qtbot.waitUntil(lambda: setup.property("opened"))
        invoke(find(window, "workspaceModelsTab"), "click")
        invoke(find(window, "workspaceDownload"), "click")
        qtbot.waitUntil(lambda: confirm.property("opened"))
        labels = [obj.property("text") for obj in confirm.findChildren(QObject)
                  if isinstance(obj.property("text"), str)]
        assert any("总量约 4.0 GiB" in text for text in labels)
        assert any("下载 2.0 GiB" in text for text in labels)
        assert not calls
        invoke(find(window, "approveManualDownload"), "click")
        qtbot.waitUntil(lambda: bool(bridge.state.get("downloadProgress")))
        bars = setup.findChildren(QObject, "downloadProgressBar")
        assert any(bar.isVisible() and bar.property("value") == .5 for bar in bars)
        assert bridge.state["downloadProgress"]["rate"] == 32768 and len(calls) == 1
        assert not engine._tingmu_warnings
    finally:
        release.set()
        qtbot.waitUntil(lambda: not bridge.state.get("modelDownloading"))
