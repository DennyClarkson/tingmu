"""Exercise the real shipped Qt Quick frontend using isolated smoke-test data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid


def open_smoke_notebook(window, bridge):
    """Write a synthetic note in the isolated workspace and open its real QML preview."""
    from PySide6.QtCore import QMetaObject, QObject

    bridge.command("saveNotebook", {
        "revision": bridge.notebooks["revision"],
        "markdown": "## Smoke heading\n\n[Smoke link](https://example.invalid/)\n\n"
                    "| Word | Meaning |\n|---|---|\n| One | First |\n\n```text\n<literal>\n```\n",
    })
    QMetaObject.invokeMethod(window.findChild(QObject, "learningViewButton"), "click")
    QMetaObject.invokeMethod(window.findChild(QObject, "notesTab"), "click")


def notebook_is_formatted(window):
    from PySide6.QtCore import QObject
    from PySide6.QtGui import QTextDocument, QTextTable

    preview = window.findChild(QObject, "notebookPreview")
    if not preview or not preview.isVisible():
        return False
    # Repeater delegates are visual children, not QObject children of the preview.
    items = [preview]
    heading = link = table = code = False
    while items:
        item = items.pop()
        items.extend(item.childItems())
        if item.objectName() != "markdownBody" or not item.isVisible():
            continue
        wrapper = item.property("textDocument")
        doc = wrapper.textDocument() if hasattr(wrapper, "textDocument") else None
        if not isinstance(doc, QTextDocument):
            return False
        stamp = doc.property("tingmu_format_stamp")
        if not stamp:
            code |= doc.toPlainText().rstrip("\n") == "<literal>"
            continue
        accent = stamp.split("|")[1]
        formats = doc.find("Smoke heading").blockFormat()
        heading |= formats.headingLevel() == 2 and formats.lineHeight() == 125
        anchor = doc.find("Smoke link").charFormat()
        link |= anchor.anchorHref() == "https://example.invalid/" and anchor.foreground().color().name() == accent
        table |= any(isinstance(frame, QTextTable) and frame.rows() == 2 and frame.format().cellPadding() == 7
                     for frame in doc.rootFrame().childFrames())
    return heading and link and table and code


def open_smoke_studio(window, bridge):
    """Render a bounded synthetic lane with the Qt modules in the shipped package."""
    from PySide6.QtCore import Q_ARG, QMetaObject

    stage = "逐句讲解"
    slots = [{"id": 1, "status": "running", "label": "第 1 句", "done": 0, "total": 1}]
    delta = bridge.queue.streams.bind("smoke", stage)
    bridge.queue.streams.sync("smoke", stage, slots)
    delta(1, '{"cues":[{"id":"hidden","translation":"Smoke 字幕')
    bridge._jobs = [{"id": "smoke", "name": "Studio", "status": "running", "worker_limit": 1,
                     "plan": [stage], "stage_name": stage, "matrix_stage": stage, "batch_slots": slots}]
    bridge.jobsChanged.emit()
    QMetaObject.invokeMethod(window, "changeView", Q_ARG("QVariant", "prepare"))


def studio_is_rendered(window):
    items = [window.contentItem()]
    while items:
        item = items.pop()
        if item.objectName() == "studioWorkerStream:0":
            return (item.property("displayed") == "Smoke 字幕" and not item.property("revealing")
                    and not item.property("cursorBlinking"))
        items.extend(item.childItems())
    return False


def check_workspace(window, bridge, checks):
    """Observe initial visibility before requesting the lazily loaded settings form."""
    from PySide6.QtCore import QMetaObject, QObject

    picker = window.findChild(QObject, "workspacePicker")
    manager = window.findChild(QObject, "workspaceSetup")
    checks["workspace_switcher"] = bool(picker and picker.property("visible"))
    checks["opens_in_player"] = bool(manager and not manager.property("visible"))
    checks["workspace_llm_concurrency"] = False
    if checks["opens_in_player"]:
        QMetaObject.invokeMethod(manager, "openTextSettings")

    def poll():
        if manager and manager.property("opened"):
            limit = manager.findChild(QObject, "llmConcurrencyCount")
            if (manager.property("section") == "text" and limit
                    and limit.property("count") == bridge.llmConcurrencyLimit
                    and limit.property("currentText") == str(bridge.store.load_settings().llm_workers)):
                checks["workspace_llm_concurrency"] = True
                QMetaObject.invokeMethod(manager, "close")

    return poll


def run_smoke(root: Path, media: Path | None = None) -> int:
    # Smoke is a local acceptance test, including first use of the runtime manager.
    blocked_network_events = []

    def offline(event, args):
        if event in {"socket.connect", "socket.getaddrinfo"}:
            blocked_network_events.append(event)
            raise RuntimeError("Portable smoke must run offline")

    sys.addaudithook(offline)
    bytecode = {path for name in ("app", "runtime/python") for path in (root / name).rglob("*.pyc")}
    import tingmu
    from PySide6.QtCore import QMetaObject, QObject, Qt, QTimer, qVersion
    from PySide6.QtGui import QFontDatabase, QIcon
    from tingmu.quick.main import create_qml_app
    from tingmu.runtime import cache_root, data_root, is_clean_install

    isolated_data = cache_root() / "portable-smoke" / uuid.uuid4().hex / "data"
    app, bridge, engine = create_qml_app(data_dir=isolated_data, runtime_setup_dir=isolated_data)
    windows = engine.rootObjects()
    first_page = engine.rootContext().contextProperty("studyHome").state["page"]
    ordinary_first_run = bool(windows and windows[0].property("atHome") and first_page == "method")
    bridge.setting("developer_mode", True)
    if is_clean_install():
        QMetaObject.invokeMethod(windows[0], "openRuntimeSetup")
    player = bridge.player
    if player.audioOutput():
        player.audioOutput().setMuted(True)
    report = {
        "ready": False, "frontend": "Qt Quick/QML", "version": tingmu.__version__,
        "python": sys.version, "executable": sys.executable, "prefix": sys.prefix,
        "sys_path": sys.path, "data": str(data_root()), "cache": str(cache_root()),
        "smoke_data": str(isolated_data), "qwen_python": os.environ.get("TINGMU_QWEN_PYTHON"),
        "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
        "qt": qVersion(), "platform": app.platformName(), "qml_root_count": len(windows),
        "window_visible": bool(windows and windows[0].isVisible()),
        "window_class": windows[0].metaObject().className() if windows else None,
        "frames": 0, "errors": [], "blocked_network_events": blocked_network_events, "checks": {},
    }
    report["checks"]["ordinary_first_run"] = ordinary_first_run
    report["checks"]["qml_window"] = report["window_visible"] and bool(windows)
    expected_icon = QIcon(str(root / "app/tingmu/quick/brand/app-icon.ico")).pixmap(32, 32)
    report["checks"]["application_icon"] = bool(
        windows and not expected_icon.isNull() and windows[0].icon().pixmap(32, 32).toImage() == expected_icon.toImage())
    report["checks"]["studio_animations_disabled"] = engine.rootContext().contextProperty("onscreenAnimations") is False
    report["checks"]["isolated_data"] = bridge.store.data_dir == isolated_data
    report["checks"]["portable_imports"] = all(Path(path).is_relative_to(root) for path in sys.path)
    report["checks"]["bundled_open_fonts"] = all(name in QFontDatabase.families() for name in (
        "Source Han Sans SC", "Source Han Serif SC", "Source Sans 3", "Source Code Pro"))
    report["checks"]["v4_learning_workspace"] = bool(bridge.notebooks.get("revision")) and bool(bridge.activeSessionId)
    report["checks"]["model_presets"] = len(bridge.modelPresets) >= 7
    report["checks"]["notebook_markdown_preview"] = False
    report["checks"]["studio_worker_preview"] = False
    poll_workspace = None
    expect_first_run = is_clean_install()
    skipped = False
    if media:
        report["checks"].update(dict.fromkeys(("decode", "seek", "rate", "audio_switch", "pause"), False))
    report["checks"]["shared_model_cache"] = cache_root() == root / ".cache"
    report["speech_preprocessing"] = bridge.speechModels
    player.errorOccurred.connect(lambda error, message: report["errors"].append(message))
    began = time.monotonic()
    phase = "waiting_for_ui"
    seek_target = 0
    target_track = 0
    latest_frame_ms = -1
    output = root / "data/logs/portable-smoke.json"

    def frame_arrived(frame):
        nonlocal latest_frame_ms
        if frame.isValid():
            report["frames"] += 1
            latest_frame_ms = frame.startTime() // 1000

    def finish():
        timer.stop()
        player.pause()
        tray = app._tingmu_tray
        report["tray_available"] = tray.available
        if tray.available:
            tray.hideToTray()
            hidden = not windows[0].isVisible() and not bridge._closed
            tray.restore()
            report["checks"]["system_tray"] = tray._tray.isVisible() and hidden and windows[0].isVisible()
        if windows and app.platformName() != "offscreen" and os.environ.get("TINGMU_SMOKE_CAPTURE") == "1":
            window = windows[0]
            screenshot = window.screen().grabWindow(window.winId())
            if not screenshot.isNull():
                capture_path = isolated_data.parent / "window.png"
                screenshot.save(str(capture_path))
                report["window_capture"] = str(capture_path)
        report["qml_warnings"] = list(engine._tingmu_warnings)
        added = {p for name in ("app", "runtime/python") for p in (root / name).rglob("*.pyc")} - bytecode
        report["new_bytecode"] = sorted(p.relative_to(root).as_posix() for p in added)
        report["checks"]["no_new_bytecode"] = not added
        report["ready"] = (all(report["checks"].values()) and not report["errors"]
                           and not report["qml_warnings"] and not blocked_network_events)
        report["wall_seconds"] = round(time.monotonic() - began, 3)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        app.quit()

    def tick():
        nonlocal poll_workspace, skipped, phase, seek_target, target_track
        elapsed = time.monotonic() - began
        if elapsed > 30:
            report["checks"]["completed_before_timeout"] = False
            finish()
            return
        if expect_first_run and not skipped and windows:
            dialog = windows[0].findChild(QObject, "runtimeSetupDialog")
            button = dialog.findChild(QObject, "skipRuntimeSetup") if dialog else None
            if button and dialog.property("opened"):
                report["checks"]["first_run_guide"] = True
                QMetaObject.invokeMethod(button, "click")
                skipped = True
                return  # Allow the first modal's close transition to finish.
            elif elapsed < 10:
                return
            else:
                report["checks"]["first_run_guide"] = False
                skipped = True
        if skipped and "first_run_skip_persisted" not in report["checks"]:
            dialog = windows[0].findChild(QObject, "runtimeSetupDialog")
            if dialog and dialog.property("visible") and elapsed < 10:
                return
            marker = isolated_data / "runtime-setup.json"
            report["checks"]["first_run_skip_persisted"] = (
                marker.is_file() and json.loads(marker.read_text("utf-8")).get("status") == "skipped")
        if poll_workspace is None and windows:
            poll_workspace = check_workspace(windows[0], bridge, report["checks"])
        elif poll_workspace:
            poll_workspace()
        if phase == "waiting_for_ui" and report["checks"].get("workspace_llm_concurrency"):
            open_smoke_notebook(windows[0], bridge)
            phase = "formatting_notebook"
        elif phase == "formatting_notebook" and notebook_is_formatted(windows[0]):
            report["checks"]["notebook_markdown_preview"] = True
            open_smoke_studio(windows[0], bridge)
            phase = "rendering_studio"
        elif phase == "rendering_studio" and studio_is_rendered(windows[0]):
            from PySide6.QtCore import Q_ARG
            report["checks"]["studio_worker_preview"] = True
            bridge.queue.streams.clear()
            bridge._jobs = []
            bridge.jobsChanged.emit()
            QMetaObject.invokeMethod(windows[0], "changeView", Q_ARG("QVariant", "watch"))
            phase = "waiting_for_video"
        elif phase == "waiting_for_video":
            sink = player.videoSink()
            if sink:
                sink.videoFrameChanged.connect(frame_arrived, Qt.ConnectionType.QueuedConnection)
                report["checks"]["qml_video_sink"] = True
                if not media:
                    finish()
                    return
                bridge.open_media(str(media))
                phase = "decoding"
        elif phase == "decoding" and report["frames"] >= 3 and player.duration() > 0:
            report["duration_ms"] = player.duration()
            report["audio_tracks"] = len(player.audioTracks())
            report["checks"]["decode"] = True
            player.pause()
            report["checks"]["pause"] = player.playbackState().name == "PausedState"
            if report["audio_tracks"] > 1:
                target_track = 0 if player.activeAudioTrack() == 1 else 1
                player.setActiveAudioTrack(target_track)
            player.setPlaybackRate(1.25)
            seek_target = min(180000, player.duration() // 2)
            report["seek_target_ms"] = seek_target
            player.setPosition(seek_target)
            phase = "seeking"
        elif (phase == "seeking" and abs(player.position() - seek_target) <= 100
              and abs(latest_frame_ms - seek_target) <= 250):
            # A decoded frame at the requested timestamp confirms the demuxer actually sought.
            report["seek_frame_ms"] = latest_frame_ms
            report["checks"]["seek"] = True
            player.play()
            phase = "resuming"
        elif phase == "resuming" and latest_frame_ms >= seek_target + 200:
            report["checks"]["rate"] = player.playbackRate() == 1.25 and player.position() > seek_target
            report["checks"]["audio_switch"] = report["audio_tracks"] > 1 and player.activeAudioTrack() == target_track
            finish()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    app.exec()
    return 0 if report["ready"] else 1
