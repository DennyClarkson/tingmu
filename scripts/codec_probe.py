"""Generate tiny local fixtures and verify Qt actually decodes common formats."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import time

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PySide6.QtWidgets import QApplication


def main():
    folder = Path(".cache/codec-smoke").resolve()
    folder.mkdir(parents=True, exist_ok=True)
    formats = [
        ("h264-aac.mp4", ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac"]),
        ("h264-aac.mov", ["-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac"]),
        ("hevc-aac.mkv", ["-c:v", "libx265", "-preset", "ultrafast", "-c:a", "aac"]),
        ("vp9-opus.webm", ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-c:a", "libopus"]),
        ("mpeg4-mp3.avi", ["-c:v", "mpeg4", "-c:a", "libmp3lame"]),
    ]
    rows = []
    for name, codecs in formats:
        command = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
                   "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "2", *codecs, str(folder / name)]
        result = subprocess.run(command, capture_output=True, timeout=45,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rows.append({"fixture": name, "generated": result.returncode == 0, "frames": 0, "errors": []})
    app = QApplication(sys.argv)
    player = QMediaPlayer()
    sink = QVideoSink()
    output = QAudioOutput()
    output.setMuted(True)
    player.setAudioOutput(output)
    player.setVideoSink(sink)
    current = [-1]
    began = [0.0]

    def frame_arrived(frame):
        if current[0] >= 0 and frame.isValid():
            rows[current[0]]["frames"] += 1

    def advance():
        player.stop()
        current[0] += 1
        if current[0] >= len(rows):
            app.quit()
            return
        began[0] = time.monotonic()
        player.setSource(QUrl.fromLocalFile(str(folder / rows[current[0]]["fixture"])))
        player.play()

    def tick():
        if current[0] < 0:
            return
        row = rows[current[0]]
        if row["frames"] >= 5 or time.monotonic() - began[0] > 12 or row["errors"]:
            row["decoded"] = row["frames"] >= 5 and not row["errors"]
            row["duration_ms"] = player.duration()
            advance()

    sink.videoFrameChanged.connect(frame_arrived)
    player.errorOccurred.connect(lambda error, message: rows[current[0]]["errors"].append(message))
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    QTimer.singleShot(0, advance)
    app.exec()
    report = {"checks": rows, "success": all(r.get("decoded", False) for r in rows)}
    Path("reports/codec-validation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
