"""Exercise actual local decoding, seeking and audio tracks; capture only app windows."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from tingmu.storage import Store
from tingmu.ui.app import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path(".data/player-smoke"))
    parser.add_argument("--output", type=Path, default=Path("reports/player-smoke.json"))
    parser.add_argument("--hold", type=int, default=0, help="Keep paused app visible for native UI inspection (seconds)")
    args = parser.parse_args()
    app = QApplication(sys.argv)
    store = Store(args.data_dir)
    settings = store.load_settings()
    settings.library_path = ""
    store.save_settings(settings)
    window = MainWindow(store)
    window.audio_output.setMuted(True)
    window.resize(1180, 820)
    window.show()
    state = {"media": args.media.name, "frames": 0, "errors": [], "audio_tracks": [], "checks": {}}
    started = time.monotonic()

    def frame_arrived(frame):
        if frame.isValid():
            state["frames"] += 1

    window.video.videoSink().videoFrameChanged.connect(frame_arrived)
    window.player.errorOccurred.connect(lambda error, message: state["errors"].append(message))
    window.open_media(args.media)
    phase = [0]
    seek_at = [0.0]

    def tick():
        elapsed = time.monotonic() - started
        if phase[0] == 0 and state["frames"] >= 3 and window.player.duration() > 0:
            state["duration_ms"] = window.player.duration()
            state["audio_tracks"] = [window.track_label(t, i) for i, t in enumerate(window.player.audioTracks())]
            state["checks"]["decoded_frames"] = True
            state["checks"]["two_audio_tracks"] = len(window.player.audioTracks()) == 2
            window.player.setActiveAudioTrack(1)
            window.player.setPlaybackRate(1.25)
            window.player.setPosition(180000)
            seek_at[0] = time.monotonic()
            phase[0] = 1
        elif phase[0] == 1 and time.monotonic() - seek_at[0] > 2:
            state["checks"]["seek"] = abs(window.player.position() - 182500) < 5000
            state["checks"]["speed"] = window.player.playbackRate() == 1.25
            state["checks"]["audio_switch"] = window.player.activeAudioTrack() == 1
            window.player.setActiveAudioTrack(0)
            window.player.setPlaybackRate(1)
            window.player.pause()
            state["checks"]["pause"] = window.player.playbackState().name == "PausedState"
            if window.document and window.cues:
                cue = next((c for c in window.cues if c.start >= 180 and c.explanation), window.cues[0])
                window.player.setPosition(int((cue.start + 0.1) * 1000))
                window.sync_cue(window.player.position(), force=True)
            window.show_companion()
            phase[0] = 2
            seek_at[0] = time.monotonic()
        elif phase[0] == 2 and time.monotonic() - seek_at[0] > 1:
            screenshot_dir = args.output.parent / "screenshots"
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            window.grab().save(str(screenshot_dir / "player.png"))
            window.companion.grab().save(str(screenshot_dir / "companion.png"))
            frame = window.video.videoSink().videoFrame()
            if frame.isValid():
                frame.toImage().save(str(screenshot_dir / "decoded-frame.png"))
            state["wall_seconds"] = round(elapsed, 2)
            state["success"] = all(state["checks"].values()) and not state["errors"]
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            timer.stop()
            if args.hold:
                QTimer.singleShot(args.hold * 1000, lambda: (window.close(), app.quit()))
            else:
                window.close()
                app.quit()
        elif elapsed > 35:
            state["success"] = False
            state["error"] = "decode/seek timed out"
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            window.close()
            app.quit()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    app.exec()
    print(json.dumps(state, ensure_ascii=True, indent=2))
    return 0 if state.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
