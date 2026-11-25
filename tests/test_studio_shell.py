"""Cross-window navigation and live transfer indicators in the real QML shell."""
from PySide6.QtCore import QObject

from test_qml_workspace import find, invoke, quick as quick
from test_ui_polish_media import click


def test_empty_player_guide_routes_to_workspace_speech_and_back(quick, qtbot):
    _, _, engine, window = quick
    setup = find(window, "workspaceSetup")
    invoke(setup, "close")
    guide = find(window, "helpDialog")
    click(find(window, "playerGuideButton"))
    qtbot.waitUntil(lambda: guide.property("opened"))
    guide.navigate.emit("speech")
    qtbot.waitUntil(lambda: setup.property("opened"))
    assert setup.property("section") == "models"
    setup.guideRequested.emit()
    qtbot.waitUntil(lambda: guide.property("opened"))
    guide.navigate.emit("prepare")
    qtbot.waitUntil(lambda: window.property("viewMode") == "prepare")
    assert not setup.property("opened") and not guide.property("opened")
    assert not engine._tingmu_warnings


def test_both_background_transfers_have_independent_live_actions(quick, qtbot):
    _, bridge, engine, window = quick
    invoke(find(window, "workspaceSetup"), "close")
    m = 1024 * 1024
    sample = dict(component="small", stage="download", completed=64*m, received=32*m,
                  total=100*m, rate=2*m, eta=18)
    bridge._set_state(modelDownloading=True, downloadProgress=sample)
    runtime = engine.rootContext().contextProperty("runtimeSetup")
    runtime.phase = "install"
    runtime._show_transfer({**sample, "component": "ffmpeg", "completed": 12*m})
    runtime.changed.emit()

    def button(name):
        return find(find(window, name), "compactDownloadProgress")

    model = button("modelDownloadFooter")
    tools = button("runtimeDownloadFooter")
    assert "64%" in model.property("text") and "2.0 MiB/s" in model.property("text")
    assert "12%" in tools.property("text") and "2.0 MiB/s" in tools.property("text")
    bridge._set_state(downloadProgress={**sample, "completed": 89*m})
    qtbot.waitUntil(lambda: "89%" in model.property("text"))
    qtbot.wait(35)
    assert model.isVisible() and model.width() > 16 and model.height() > 8, (model.width(), model.height())
    click(model)
    qtbot.waitUntil(lambda: find(window, "activeModelDownload").property("opened"))
    invoke(find(window, "activeModelDownload"), "close")
    click(tools)
    qtbot.waitUntil(lambda: find(window, "runtimeSetupDialog").property("opened"))
    assert runtime.phase == "install"
    invoke(find(window, "runtimeSetupDialog"), "close")
    assert runtime.phase == "install"
    runtime.phase = ""
    runtime.changed.emit()
    bridge._set_state(modelDownloading=False)
    qtbot.waitUntil(lambda: window.findChild(QObject, "modelDownloadFooter") is None)
    assert window.findChild(QObject, "runtimeDownloadFooter") is None
    assert not engine._tingmu_warnings
