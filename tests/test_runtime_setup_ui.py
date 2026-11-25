"""Clean-player onboarding through the shipped QML, with offline dependency fixtures."""

import json
import os
from threading import Event, get_ident
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QMetaObject

from tingmu import runtime, runtime_deps, runtime_install
from tingmu.quick.main import create_qml_app


@pytest.fixture
def portable(tmp_path, monkeypatch, qapp, qtbot):
    root = tmp_path / "portable"
    root.mkdir()
    (root / "tingmu-portable.json").write_text('{"clean_install":true}')
    monkeypatch.setattr(runtime, "portable_root", lambda: root)
    installed, checked, installs = {}, {}, []
    main_thread = get_ident()

    def probe(base, kind, configured=None, *, cancelled=None):
        assert get_ident() != main_thread
        assert base == root
        selected = configured or installed.get(kind)
        ready = bool(selected and os.path.exists(selected))
        row = {"kind": kind, "ready": ready, "path": str(selected or ""), "nvidia_present": False,
               "source": "configured" if configured and ready else "portable" if ready else "",
               "detail": "probe"}
        checked[kind] = row
        return row

    def last_probe(base, kind):
        row = checked.get(kind)
        return None if row and row["ready"] and not os.path.exists(row["path"]) else row

    def install(base, kind, progress, cancelled):
        assert get_ident() != main_thread
        assert base == root
        installs.append(kind)
        progress(11, 50)
        folder = root / ".cache" / kind
        folder.mkdir(parents=True, exist_ok=True)
        installed[kind] = folder
        progress(50, 50)
        return folder

    monkeypatch.setattr(runtime_deps, "probe", probe)
    monkeypatch.setattr(runtime_deps, "last_probe", last_probe)
    monkeypatch.setattr(runtime_install, "download_bytes", lambda kind: 50)
    monkeypatch.setattr(runtime_install, "install", install)
    sessions = []

    def launch(data=None, *, runtime_setup_dir=None):
        app, bridge, engine = create_qml_app(data or root / "data", runtime_setup_dir=runtime_setup_dir)
        window = engine.rootObjects()[0]
        sessions.append((bridge, engine, window))
        return bridge, window, engine

    yield root, launch, checked, installed, installs
    for bridge, engine, window in reversed(sessions):
        bridge.shutdown()
        window.hide()
        assert not engine._tingmu_warnings
        engine.deleteLater()
        qtbot.wait(15)


def find(window, name):
    item = window.findChild(QObject, name)
    assert item is not None, name
    return item


def invoke(item, method):
    assert QMetaObject.invokeMethod(item, method)


def wait_dialog(window, qtbot):
    qtbot.waitUntil(lambda: window.findChild(QObject, "runtimeSetupDialog") is not None)
    dialog = find(window, "runtimeSetupDialog")
    qtbot.waitUntil(lambda: bool(dialog.property("opened")))
    return dialog


def test_open_skip_relaunch_revisit_and_isolated_data(portable, qtbot, monkeypatch):
    root, launch, _, _, installs = portable
    running, release = Event(), Event()
    original = runtime_deps.probe

    def slow(*args, **kwargs):
        running.set()
        release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime_deps, "probe", slow)
    data = root / ".cache" / "smoke-data"
    bridge, window, engine = launch(data, runtime_setup_dir=data)
    dialog = wait_dialog(window, qtbot)
    qtbot.waitUntil(running.is_set)
    assert not (data / "runtime-setup.json").exists()
    invoke(find(dialog, "skipRuntimeSetup"), "click")
    qtbot.waitUntil(lambda: not dialog.property("opened"))
    assert json.loads((data / "runtime-setup.json").read_text()) == {"status": "skipped"}
    assert not (root / "data/runtime-setup.json").exists()
    release.set()
    qtbot.waitUntil(lambda: not bridge.workspaces.runtime_setup.phase)
    assert not installs
    _, next_window, _ = launch(data, runtime_setup_dir=data)
    qtbot.wait(80)
    assert not next_window.findChild(QObject, "runtimeSetupDialog")
    invoke(find(next_window, "runtimeSetupAction"), "trigger")
    dialog = wait_dialog(next_window, qtbot)
    invoke(find(dialog, "skipRuntimeSetup"), "click")
    setup = find(next_window, "workspaceSetup")
    invoke(setup, "open")
    invoke(find(setup, "workspaceModelsTab"), "click")
    invoke(find(setup, "workspaceRuntimeSetup"), "click")
    assert wait_dialog(next_window, qtbot) is dialog
    assert not installs


def test_explicit_selection_and_model_gate(portable, qtbot, monkeypatch):
    root, launch, _, installed, installs = portable
    bridge, window, _ = launch()
    dialog = wait_dialog(window, qtbot)
    manager = bridge.workspaces.runtime_setup
    qtbot.waitUntil(lambda: not manager.phase)
    selected = dialog.property("selectedIds").toVariant()
    assert selected == ["ffmpeg"]
    manager.installSelected(["whisper-cuda"])
    assert not manager.phase
    bridge.setting("asr_model", "small")
    workspaces = bridge.workspaces
    assert not workspaces.downloadPlan("download")
    assert "Whisper" in workspaces.error
    assert not workspaces.action("download", {"model": "small"})
    assert "model-download" not in bridge.task_keys()
    assert not workspaces.downloadPlan("downloadSpeech")
    assert not workspaces.action("downloadSpeech", {})
    qtbot.waitUntil(lambda: not manager.phase)
    invoke(find(dialog, "installSelectedRuntime"), "click")
    qtbot.waitUntil(lambda: not manager.firstRun)
    assert installs == ["ffmpeg"]
    assert json.loads((root / "data/runtime-setup.json").read_text())["status"] == "completed"
    assert manager.results["ffmpeg"]["ready"]
    assert dialog.property("selectedIds").toVariant() == []
    assert not manager.results["qwen"]["ready"]

    folder = root / "existing-whisper"
    folder.mkdir()
    installed["local-asr"] = folder
    manager.detect()
    qtbot.waitUntil(lambda: not manager.phase)
    monkeypatch.setattr("tingmu.quick.workspace_controller.model_inventory",
                        lambda model: {"download_bytes": 100, "estimated_download_bytes": 100, "installed": False})
    bridge.setting("device", "cpu")
    assert workspaces.downloadPlan("download")["model"] == "small"
    folder.rename(root / "moved-whisper")
    assert not workspaces.downloadPlan("download")
    qtbot.waitUntil(lambda: not manager.phase)
    bridge.setting("asr_model", "qwen3-asr-0.6b")
    assert not workspaces.downloadPlan("download")
    assert "Qwen" in workspaces.error
    qtbot.waitUntil(lambda: not manager.phase)


def test_verified_external_path_persisted_without_writes_and_cancel(portable, qtbot, monkeypatch):
    root, launch, _, _, installs = portable
    external = root.parent / "read-only-component"
    external.mkdir()
    sentinel = external / "source.dll"
    sentinel.write_bytes(b"external fixture")
    bridge, window, _ = launch()
    dialog = wait_dialog(window, qtbot)
    manager = bridge.workspaces.runtime_setup
    qtbot.waitUntil(lambda: not manager.phase)
    monkeypatch.setattr(bridge.native_dialogs, "getExistingDirectory", lambda *args: str(external))
    manager.choosePath("local-asr")
    marker = root / "data/runtime-setup.json"
    qtbot.waitUntil(lambda: marker.exists() and manager.phase == "")
    assert json.loads(marker.read_text()) == {"paths": {"local-asr": str(external)}}
    assert manager.firstRun and manager.results["local-asr"]["source"] == "configured"
    assert list(external.iterdir()) == [sentinel] and sentinel.read_bytes() == b"external fixture"

    def transfer(base, kind, progress, cancelled):
        installs.append(kind)
        progress(3_000_000_000, 5_000_000_000)
        while not cancelled():
            time.sleep(.01)
        raise InterruptedError

    monkeypatch.setattr(runtime_install, "install", transfer)
    invoke(find(dialog, "installSelectedRuntime"), "click")
    qtbot.waitUntil(lambda: manager.transfer.get("done") == 3_000_000_000)
    assert find(dialog, "runtimeInstallProgress").property("value") == pytest.approx(.6)
    invoke(find(dialog, "cancelRuntimeInstall"), "click")
    qtbot.waitUntil(lambda: not manager.phase)
    assert manager.firstRun and "取消" in manager.message and installs == ["ffmpeg"]
    assert json.loads(marker.read_text()) == {"paths": {"local-asr": str(external)}}


def test_ready_component_discovered_again_before_install(portable, qtbot):
    root, launch, _, installed, installs = portable
    bridge, window, _ = launch()
    dialog = wait_dialog(window, qtbot)
    manager = bridge.workspaces.runtime_setup
    qtbot.waitUntil(lambda: not manager.phase)
    installed["ffmpeg"] = root
    invoke(find(dialog, "installSelectedRuntime"), "click")
    qtbot.waitUntil(lambda: not manager.firstRun)
    assert manager.results["ffmpeg"]["ready"] and not installs

def test_custom_workspace_shares_installation_setup_marker(portable, qtbot):
    root, launch, _, _, installs = portable
    bridge, window, _ = launch(root / "other-workspaces")
    dialog = wait_dialog(window, qtbot)
    invoke(find(dialog, "skipRuntimeSetup"), "click")
    qtbot.waitUntil(lambda: not dialog.property("opened") and not bridge.workspaces.runtime_setup.phase)
    assert (root / "data/runtime-setup.json").is_file()
    assert not (root / "other-workspaces/runtime-setup.json").exists()
    _, next_window, _ = launch()
    qtbot.wait(60)
    assert not next_window.findChild(QObject, "runtimeSetupDialog")
    assert not installs

@pytest.mark.parametrize("language", ["zh-CN", "zh-TW", "en"])
def test_first_run_control_language(portable, qapp, qtbot, language):
    _, launch, _, _, _ = portable
    _, window, engine = launch()
    dialog = wait_dialog(window, qtbot)
    assert qapp._tingmu_language.select(language)
    expected = qapp.translate("Tingmu", "暂不下载，直接进入")
    qtbot.waitUntil(lambda: find(dialog, "skipRuntimeSetup").property("text") == expected)
    assert expected and (language == "zh-CN" or expected != "暂不下载，直接进入")
    invoke(find(dialog, "skipRuntimeSetup"), "click")
    assert not engine._tingmu_warnings
