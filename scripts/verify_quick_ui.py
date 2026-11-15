"""Opt-in desktop acceptance using only this application's own Qt objects."""

import argparse
import json
from pathlib import Path
import sys
import traceback

from PySide6.QtCore import QObject, QPointF, QMetaObject, QTimer, Qt, Q_ARG
from PySide6.QtTest import QTest

from tingmu.quick.main import create_qml_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--media", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    app, bridge, engine = create_qml_app(args.data, args.media)
    window = engine.rootObjects()[0]
    window.resize(1280, 680)
    bridge.audio.setMuted(True)
    report = {"platform": app.platformName(), "checks": {}}

    def find(name):
        obj = window.findChild(QObject, name)
        assert obj is not None, name
        return obj

    def invoke(obj, name, *values):
        assert QMetaObject.invokeMethod(obj, name, Qt.ConnectionType.DirectConnection,
                                       *[Q_ARG("QVariant", value) for value in values])

    def snapshot(win, name):
        assert win.grabWindow().save(str(args.output / name))

    def safely(callback):
        def run():
            try:
                callback()
            except Exception as exc:
                report["error"] = str(exc)
                report["traceback"] = traceback.format_exc()
                finish()
        return run

    def seek():
        bridge._seek(90000)
        QTimer.singleShot(800, safely(watch))

    def watch():
        bridge.player.pause()
        report["checks"]["native_video_frame"] = bridge.player.videoSink().videoFrame().isValid()
        snapshot(window, "watch.png")
        player = find("playerModule").property("loadedItem")
        invoke(player, "toggleFullscreen")
        QTimer.singleShot(450, safely(fullscreen))

    def fullscreen():
        full = find("fullscreenWindow")
        overlay = find("companionOverlay")
        initial = (overlay.width(), overlay.height())
        bridge._sync_cue(1000)
        bridge._sync_cue(90000)
        report["checks"]["fixed_overlay"] = initial == (overlay.width(), overlay.height())
        grip = find("overlayResizeGrip")
        origin = grip.mapToScene(QPointF(grip.width()/2, grip.height()/2)).toPoint()
        end = origin + QPointF(80, 45).toPoint()
        QTest.mousePress(full, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, origin)
        QTest.mouseMove(full, end, delay=30)
        QTest.mouseRelease(full, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, end)
        report["checks"]["resize_grip"] = overlay.width() > initial[0] and overlay.height() > initial[1]
        report["checks"]["fullscreen_video"] = find("playerVideo").window() is full
        QTimer.singleShot(450, safely(fullscreen_shot))

    def fullscreen_shot():
        snapshot(find("fullscreenWindow"), "fullscreen.png")
        image = find("fullscreenWindow").grabWindow()
        colors = {image.pixelColor(round(image.width()*x/20), round(image.height()*y/20)).rgb()
                  for x in range(5, 16) for y in range(5, 14)}
        report["checks"]["fullscreen_pixels"] = len(colors) > 20
        invoke(find("playerModule").property("loadedItem"), "toggleFullscreen")
        report["checks"]["video_restored"] = find("playerVideo").window() is window
        invoke(window, "changeView", "prepare")
        QTimer.singleShot(200, safely(prepare))

    def prepare():
        snapshot(window, "prepare.png")
        invoke(window, "changeView", "watch")
        invoke(window, "openModule", "chat")
        QTimer.singleShot(250, safely(chat))

    def chat():
        dock = find("chatModule")
        report["checks"]["independent_chat"] = bool(dock.property("detached"))
        chat_item = dock.property("loadedItem")
        chat_window = chat_item.window()
        snapshot(chat_window, "chat.png")
        invoke(dock, "detach")
        bridge.setting("developer_mode", True)
        invoke(find("developerWindow"), "open")
        QTimer.singleShot(250, safely(developer))

    def developer():
        dev = find("developerWindow")
        report["checks"]["developer_logs"] = dev.isVisible() and bool(bridge.logs)
        snapshot(dev, "developer.png")
        bridge.setting("developer_mode", False)
        report["checks"]["developer_hides"] = not dev.isVisible()
        finish()

    def finish():
        report["qml_warnings"] = engine._tingmu_warnings
        report["checks"]["no_qml_warnings"] = not engine._tingmu_warnings
        report["ready"] = not report.get("error") and all(report["checks"].values())
        (args.output / "acceptance.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
        app.quit()

    QTimer.singleShot(1500, safely(seek))
    QTimer.singleShot(15000, app.quit)
    app.exec()
    return 0 if report.get("ready") else 1


if __name__ == "__main__":
    sys.exit(main())
