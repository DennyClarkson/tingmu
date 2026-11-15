"""Exercise the real shipped Qt Quick frontend using isolated smoke-test data."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time
import uuid


def run_smoke(root: Path, media: Path | None = None) -> int:
    import tingmu
    from PySide6.QtCore import QTimer, qVersion
    from PySide6.QtGui import QFontDatabase
    from tingmu.quick.main import create_qml_app
    from tingmu.runtime import cache_root, data_root

    isolated_data = cache_root() / "portable-smoke" / uuid.uuid4().hex / "data"
    app, bridge, engine = create_qml_app(data_dir=isolated_data,
                                         initial_path=str(media) if media else None)
    windows = engine.rootObjects()
    player = bridge.player
    if player.audioOutput():
        player.audioOutput().setMuted(True)
    report = {
        "ready": False, "frontend": "Qt Quick/QML", "version": tingmu.__version__,
        "python": sys.version, "executable": sys.executable, "prefix": sys.prefix,
        "sys_path": sys.path, "data": str(data_root()), "cache": str(cache_root()),
        "smoke_data": str(isolated_data), "qwen_python": os.environ["TINGMU_QWEN_PYTHON"],
        "ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe"),
        "qt": qVersion(), "platform": app.platformName(), "qml_root_count": len(windows),
        "window_visible": bool(windows and windows[0].isVisible()),
        "window_class": windows[0].metaObject().className() if windows else None,
        "frames": 0, "errors": [], "checks": {},
    }
    report["checks"]["qml_window"] = report["window_visible"] and bool(windows)
    report["checks"]["isolated_data"] = bridge.store.data_dir == isolated_data
    report["checks"]["portable_imports"] = all(Path(path).is_relative_to(root) for path in sys.path)
    report["checks"]["bundled_open_fonts"] = all(name in QFontDatabase.families() for name in (
        "Source Han Sans SC", "Source Han Serif SC", "Source Sans 3", "Source Code Pro"))
    report["checks"]["v4_learning_workspace"] = bool(bridge.notebooks.get("revision")) and bool(bridge.activeSessionId)
    report["checks"]["model_presets"] = len(bridge.modelPresets) >= 7
    engine.warnings.connect(lambda errors: report["errors"].extend(error.toString() for error in errors))
    player.errorOccurred.connect(lambda error, message: report["errors"].append(message))
    began = time.monotonic()
    stage = [0]
    changed_at = [0.0]
    seek_target = [0]
    output = root / "data/logs/portable-smoke.json"

    def frame_arrived(frame):
        if frame.isValid():
            report["frames"] += 1

    def finish():
        timer.stop()
        player.pause()
        if windows and app.platformName() != "offscreen" and os.environ.get("TINGMU_SMOKE_CAPTURE") == "1":
            screenshot = windows[0].grabWindow()
            if not screenshot.isNull():
                capture_path = isolated_data.parent / "window.png"
                screenshot.save(str(capture_path))
                report["window_capture"] = str(capture_path)
        report["ready"] = all(report["checks"].values()) and not report["errors"]
        report["wall_seconds"] = round(time.monotonic() - began, 3)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        app.quit()

    def tick():
        elapsed = time.monotonic() - began
        if stage[0] == 0:
            sink = player.videoSink()
            if sink:
                sink.videoFrameChanged.connect(frame_arrived)
                report["checks"]["qml_video_sink"] = True
                stage[0] = 1
                if media:
                    player.play()
        if not media and elapsed > 1.5:
            report["checks"]["qml_video_sink"] = player.videoSink() is not None
            finish()
        elif media and stage[0] == 1 and report["frames"] >= 3 and player.duration() > 0:
            report["duration_ms"] = player.duration()
            report["audio_tracks"] = len(player.audioTracks())
            report["checks"]["decode"] = True
            if report["audio_tracks"] > 1:
                player.setActiveAudioTrack(1)
            player.setPlaybackRate(1.25)
            seek_target[0] = min(180000, player.duration() // 2)
            player.setPosition(seek_target[0])
            changed_at[0] = time.monotonic()
            stage[0] = 2
        elif media and stage[0] == 2 and time.monotonic() - changed_at[0] > 1.5:
            report["checks"]["seek"] = abs(player.position() - seek_target[0] - 1875) < 4000
            report["checks"]["rate"] = player.playbackRate() == 1.25
            if report["audio_tracks"] > 1:
                report["checks"]["audio_switch"] = player.activeAudioTrack() == 1
            player.pause()
            report["checks"]["pause"] = player.playbackState().name == "PausedState"
            finish()
        elif elapsed > 30:
            report["checks"]["completed_before_timeout"] = False
            finish()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    app.exec()
    return 0 if report["ready"] else 1
