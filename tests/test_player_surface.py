"""Rendered player borders, fullscreen geometry and pointer behavior across windows."""

import math
import os
from pathlib import Path
import subprocess

import pytest
from PySide6.QtCore import QPoint, QPointF
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface
from PySide6.QtTest import QTest

from test_qml_v4 import find, invoke, quick as quick


THEMES = {
    "forest": ("#101315", "#30393c"),
    "paper": ("#f5f1e8", "#d9d2c5"),
    "mist": ("#e9f0ef", "#c4d6d1"),
    "graphite": ("#17191d", "#3c414b"),
}


def grab(window, name):
    assert isinstance(window, QQuickWindow)
    picture = window.grabWindow()
    assert not picture.isNull()
    if os.environ.get("TINGMU_PLAYER_SCREENSHOTS") == "1":
        assert QGuiApplication.platformName() == "windows"
        assert window.rendererInterface().graphicsApi() != QSGRendererInterface.GraphicsApi.Software, (
            "Native playback screenshots require Qt Quick's RHI renderer. Unset QT_QUICK_BACKEND=software."
        )
        folder = Path(__file__).resolve().parents[1] / ".cache/qa/stream-studio-player"
        folder.mkdir(parents=True, exist_ok=True)
        if picture.devicePixelRatio() != 1:
            name += f"-{picture.devicePixelRatio():g}x"
        assert picture.save(str(folder / (name + ".png")))
    return picture


def lime_at(picture, point):
    color = picture.pixelColor(point)
    return color.green() > 200 and color.red() < 35 and color.blue() < 35


def shows_clip(picture, video):
    center = video.mapToScene(QPointF(video.width() / 2, video.height() / 2))
    return lime_at(picture, (center * picture.devicePixelRatio()).toPoint())


def check_outline(picture, item, background, line):
    scale = picture.devicePixelRatio()
    origin = item.mapToScene(QPointF(0, 0))
    left, top = (origin * scale).toPoint().toTuple()
    end = (item.mapToScene(QPointF(item.width(), item.height())) * scale).toPoint()
    right, bottom = end.x() - 1, end.y() - 1
    radius = item.property("radius") * scale
    assert radius >= 4
    ink = line.getRgb()[:3]

    def ink_distance(color):
        return max(abs(a - b) for a, b in zip(ink, color.getRgb()[:3]))

    # A curved or fractionally aligned 1-DIP stroke can straddle two physical pixels.
    tolerance = ink_distance(item.property("color")) * 0.65

    def has_stroke(x, y, x_span, y_span):
        return any(ink_distance(picture.pixelColor(px, py)) <= tolerance
                   for px in range(max(left, x - x_span), min(right, x + x_span) + 1)
                   for py in range(max(top, y - y_span), min(bottom, y + y_span) + 1))

    for cx, dx in ((left, 1), (right, -1)):
        for cy, dy in ((top, 1), (bottom, -1)):
            for x in range(math.ceil(radius)):
                for y in range(math.ceil(radius)):
                    if math.hypot(radius - x - 0.5, radius - y - 0.5) > radius + 1:
                        assert picture.pixelColor(cx + dx * x, cy + dy * y) == background
            for angle in range(0, 91, 5):
                radians = math.radians(angle)
                x = int(radius - (radius - scale / 2) * math.cos(radians))
                y = int(radius - (radius - scale / 2) * math.sin(radians))
                assert has_stroke(cx + dx * x, cy + dy * y, 1, 1)
    # Scan the complete straight runs, including the seam between the video and its controls.
    inset = math.ceil(radius) + 1
    for x in range(left + inset, right - inset + 1):
        for y in (top, bottom):
            if scale.is_integer():
                assert picture.pixelColor(x, y) == line
            else:
                assert has_stroke(x, y, 0, 1)
    for y in range(top + inset, bottom - inset + 1):
        for x in (left, right):
            if scale.is_integer():
                assert picture.pixelColor(x, y) == line
            else:
                assert has_stroke(x, y, 1, 0)


@pytest.fixture
def playing_clip(quick, qtbot, tmp_path, ffmpeg):
    _, bridge, _, _ = quick
    clip = tmp_path / "green.mkv"
    subprocess.run([ffmpeg, "-v", "error", "-nostdin", "-f", "lavfi", "-i", "color=lime:s=128x80:r=5:d=60",
                    "-c:v", "ffv1", str(clip)], check=True, timeout=30)
    bridge.open_media(str(clip))
    qtbot.waitUntil(lambda: bridge.playbackDetails["playing"] and bridge._player.videoSink().videoFrame().isValid())
    decoded = bridge._player.videoSink().videoFrame().toImage()
    assert not decoded.isNull() and lime_at(decoded, decoded.rect().center())


@pytest.mark.parametrize("playing", [False, True], ids=["empty", "playback"])
def test_rendered_dock_and_floating_borders(quick, qtbot, playing, request):
    app, bridge, engine, window = quick
    if playing:
        request.getfixturevalue("playing_clip")
    module = find(window, "playerModule")
    surface = find(module, "playerSurface")
    video = find(module, "playerVideo")
    for floating in (False, True):
        module.setProperty("detached", floating)
        qtbot.waitUntil(lambda: surface.window().isVisible() and (surface.window() != window) == floating)
        target = surface.window()
        target.setScreen(app.primaryScreen())
        target.setPosition(app.primaryScreen().availableGeometry().topLeft() + QPoint(20, 30))
        qtbot.waitUntil(target.isExposed)
        # Qt Quick's software scene graph paints the panel but does not compose VideoOutput frames.
        rendered_video = (playing and app.platformName() == "windows"
                          and target.rendererInterface().graphicsApi() != QSGRendererInterface.GraphicsApi.Software)
        for size in ("wide", "narrow"):
            if floating:
                target.resize(760 if size == "wide" else 330, 480)
            else:
                target.resize(1480 if size == "wide" else 980, 720)
            qtbot.waitUntil(lambda: (surface.width() >= 590) == (size == "wide"))
            if rendered_video:
                # After reparenting the video, the new window presents it on the next decoded frame.
                qtbot.waitUntil(lambda: shows_clip(target.grabWindow(), video))
            for theme, (background, line) in THEMES.items():
                bridge.setting("theme", theme)
                qtbot.waitUntil(lambda: target.color() == QColor(background))
                qtbot.wait(40)
                kind = "floating" if floating else "docked"
                picture = grab(target, f"{theme}-{kind}-{size}-{'playing' if playing else 'empty'}")
                check_outline(picture, surface, QColor(background), QColor(line))
                if rendered_video:
                    assert shows_clip(picture, video)
    assert engine._tingmu_warnings == []


def test_fullscreen_pod_pixels_reservation_and_hover(quick, qtbot, playing_clip):
    _, bridge, engine, window = quick
    player = find(window, "playerModule").property("loadedItem")
    player.setProperty("autoHide", False)
    invoke(player, "toggleFullscreen")
    qtbot.waitUntil(lambda: player.property("fullscreenGeometryReady"))
    full = find(window, "fullscreenWindow")
    pod = find(full, "fullscreenTransport")
    area = find(full, "fullscreenVideoArea")
    overlay = find(full, "companionOverlay")
    assert abs(2 * pod.x() + pod.width() - full.width()) <= 1
    assert pod.x() >= 12 and full.height() - pod.y() - pod.height() >= 12
    assert area.height() == pod.y()
    overlay.setY(full.height())
    invoke(player, "clampOverlay")
    assert overlay.y() + overlay.height() == area.height()

    for theme, (_, line) in THEMES.items():
        bridge.setting("theme", theme)
        qtbot.wait(40)
        picture = grab(full, f"{theme}-fullscreen-visible")
        # The reserved area displays the video background around all four pod corners.
        check_outline(picture, pod, full.color(), QColor(line))
        player.setProperty("controlsVisible", False)
        qtbot.wait(40)
        hidden = grab(full, f"{theme}-fullscreen-hidden")
        position = pod.mapToScene(QPointF(pod.width() / 2, pod.height() / 2))
        assert hidden.pixelColor((position * hidden.devicePixelRatio()).toPoint()) == full.color()
        assert area.height() == pod.y()
        invoke(player, "showControls")

    player.setProperty("reserveBar", False)
    assert area.height() == full.height()
    captions = find(player, "playbackCaptionLayer")
    # Fitted video can end above the pod on a square offscreen display.
    assert captions.property("controlsClearance") == max(0, captions.y() + captions.height() - pod.y() + 8)
    player.setProperty("aspectMode", 2)

    def check_stretched_captions():
        assert captions.y() == 0
        assert captions.height() == full.height()
        assert captions.property("controlsClearance") == full.height() - pod.y() + 8

    qtbot.waitUntil(check_stretched_captions)
    player.setProperty("controlsVisible", False)
    assert captions.property("controlsClearance") == 0
    invoke(player, "showControls")
    overlay.setY(full.height())
    invoke(player, "clampOverlay")
    assert overlay.y() + overlay.height() == full.height()
    player.setProperty("reserveBar", True)
    qtbot.waitUntil(lambda: overlay.y() + overlay.height() == pod.y())
    player.setProperty("hideDelay", 1)
    player.setProperty("autoHide", True)
    QTest.mouseMove(full, QPoint(round(pod.x() + pod.width() / 2), round(pod.y() + 6)))
    qtbot.waitUntil(lambda: pod.property("interacting"))
    qtbot.wait(1200)
    assert pod.isVisible()
    QTest.mouseMove(full, QPoint(2, full.height() - 3))
    qtbot.waitUntil(lambda: not pod.isVisible(), timeout=3000)
    QTest.mouseMove(full, QPoint(6, full.height() - 5))
    qtbot.waitUntil(pod.isVisible)
    invoke(player, "toggleFullscreen")
    assert engine._tingmu_warnings == []
