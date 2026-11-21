"""Normal embedded text, bitmap seek and selection lifetimes."""

import os
from pathlib import Path
import subprocess
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tingmu import embedded_subtitles as subtitles
from tingmu.quick import embedded


def test_text_overlaps_and_exclusive_end():
    events = subtitles.text_events(
        b"1\n00:00:01,000 --> 00:00:04,000\nOne\n\n"
        b"2\n00:00:02,000 --> 00:00:03,000\nTwo\n\n"
        b"3\n00:00:04,000 --> 00:00:05,000\nThree\n")
    assert [events.at(t) for t in (999, 1000, 2000, 3000, 4000, 5000)] == [
        "", "One", "One\nTwo", "One", "Three", ""]


def test_reads_real_muxed_text(tmp_path):
    try:
        ffmpeg = subtitles._binary("ffmpeg")
        subtitles._binary("ffprobe")
    except subtitles.SubtitleError:
        pytest.skip("FFmpeg CLI unavailable")
    srt = tmp_path / "fixture.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:03,000\nTwo lines,\nexact time.\n", encoding="utf-8")
    media = tmp_path / "clip.mkv"
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "color=s=64x64:d=4",
                    "-i", str(srt), "-c:v", "ffv1", "-c:s", "srt", str(media)], check=True)
    events = subtitles.load_track(str(media), 0, threading.Event())
    assert events.at(1100) == "Two lines,\nexact time."
    assert events.at(3000) == ""


def test_page_contains_real_alpha_bounds_and_timing(tmp_path, monkeypatch):
    from PySide6.QtGui import QColor, QImage
    monkeypatch.setattr(subtitles, "_binary", lambda _: "ffmpeg")

    def produce(args, folder, stop, **kwargs):
        image = QImage(32, 16, QImage.Format.Format_RGBA8888)
        image.fill(QColor("transparent"))
        image.setPixelColor(8, 4, QColor("white"))
        image.setPixelColor(15, 7, QColor("white"))
        # Long cue that started 15 s before the target page.
        assert args[args.index("-ss") + 1] == "14.000"
        image.save(str(Path(folder) / "000000001000.png"))
        image.fill(QColor("transparent"))
        image.save(str(Path(folder) / "000000018000.png"))
        return b""

    monkeypatch.setattr(subtitles, "_run", produce)
    events = subtitles.load_page("movie.mkv", 2, subtitles.BitmapTrack(32, 16, (15000, 60000)),
                                 1, threading.Event())
    frame = events.at(31000)
    assert frame.url.startswith("data:image/png;base64,")
    assert frame.bounds == {"x": 0.25, "y": 0.25, "width": 0.25, "height": 0.25}
    assert events.at(32000) == ""


def test_switch_off_ignores_old_work_even_if_it_finishes(qtbot, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def load(media, ordinal, stop):
        started.set()
        release.wait(2)
        return subtitles.Events((0,), ("old track",))

    monkeypatch.setattr(embedded, "load_track", load)
    controller = embedded.EmbeddedCaptions(None)
    try:
        controller.select("movie", 0, 0)
        qtbot.waitUntil(started.is_set)
        controller.select("movie", -1, 0)
        release.set()
        qtbot.wait(120)
        assert (controller.text, controller.image, controller.status, controller.track) == ("", "", "", -1)
    finally:
        release.set()
        controller.close()


def test_latest_seek_wins_while_paused(qtbot, monkeypatch):
    monkeypatch.setattr(embedded, "load_track", lambda *args: subtitles.BitmapTrack(32, 16, (1, 90000)))
    started, release = threading.Event(), threading.Event()
    def load(media, ordinal, source, page, stop):
        if page == 0:
            started.set()
            release.wait(2)
        return subtitles.Events((0,), (subtitles.BitmapFrame(f"page{page}", {"y": 0.4}),))
    monkeypatch.setattr(embedded, "load_page", load)
    controller = embedded.EmbeddedCaptions(None)
    try:
        controller.select("movie", 0, 0)
        qtbot.waitUntil(started.is_set)
        controller.position(31000)
        controller.position(61000)
        release.set()
        qtbot.waitUntil(lambda: controller.image == "page2")
        assert controller.bounds == {"y": 0.4}
        controller.select("other movie", -1, 0)
        assert controller.image == "" and controller.bounds == {}
    finally:
        release.set()
        controller.close()

def test_unknown_bitmap_canvas_is_discovered_by_decoder(monkeypatch):
    monkeypatch.setattr(subtitles, "_binary", lambda name: name)
    commands = []
    def run(args, folder, stop, **kwargs):
        commands.append(args)
        if args[0] == "ffmpeg":
            return b""
        if "packet=pts_time,duration_time" in args:
            return b"1.000,N/A\n"
        if "v:0" in args:
            return b'{"streams":[{"width":256,"height":144}]}'
        return b'{"streams":[{"codec_name":"dvb_subtitle"}],"format":{"start_time":"0"}}'
    monkeypatch.setattr(subtitles, "_run", run)
    track = subtitles.load_track("movie.mkv", 0, threading.Event())
    assert track.native_canvas is False
    subtitles.load_page("movie.mkv", 0, track, 0, threading.Event())
    assert "-canvas_size" not in commands[-1]
