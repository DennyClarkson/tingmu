"""Reproducible inbox and Qt idle samples on generated, workspace-local data."""

import json
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch


def _qt_sample(folder):
    from PySide6.QtCore import QEventLoop, QTimer, Qt
    from PySide6.QtWidgets import QApplication

    from tingmu.quick.bridge import Bridge
    from tingmu.storage import Store

    app = QApplication([])
    bridge = Bridge(Store(folder / "workspace"))
    loop = QEventLoop()

    def run_for(milliseconds):
        timer = QTimer()
        timer.setSingleShot(True)
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        timer.timeout.connect(loop.quit)
        timer.start(milliseconds)
        loop.exec()

    try:
        run_for(500)
        wakeups, changes = [], []
        bridge.inbox_timer.timeout.connect(lambda: wakeups.append(True))
        bridge.mediaInboxChanged.connect(lambda: changes.append(True))
        cpu = time.process_time()
        start = time.perf_counter()
        run_for(6200)
        elapsed = time.perf_counter() - start
        cpu_ms = (time.process_time() - cpu) * 1000
        samples = []
        for _ in range(5):
            start = time.perf_counter()
            for _ in range(5000):
                _ = bridge.queueState
            samples.append((time.perf_counter() - start) * 1000)
        return {"idle_wall_s": elapsed, "idle_cpu_ms": cpu_ms, "idle_inbox_wakeups": len(wakeups),
                "idle_inbox_signals": len(changes), "queue_state_5000_reads_ms": median(samples)}
    finally:
        bridge.shutdown()
        app.processEvents()


def measure(folder: Path) -> dict:
    from tingmu import media_inbox

    folder.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=folder) as temporary:
        root = Path(temporary)
        inbox = root / "inbox"
        inbox.mkdir()
        for index in range(30):
            (inbox / f"episode-{index:02}.mp4").write_bytes(b"synthetic media")
        scanner = media_inbox.InboxScanner(inbox, quiet_seconds=3)
        scanner.scan(now=0)
        assert len(scanner.scan(now=4)["ready"]) == 30
        samples = []
        original = media_inbox._not_being_written
        checks = 0

        def counted(path):
            nonlocal checks
            checks += 1
            return original(path)

        with patch.object(media_inbox, "_not_being_written", counted):
            for index in range(11):
                start = time.perf_counter()
                assert len(scanner.scan(now=6 + index * 2)["ready"]) == 30
                samples.append((time.perf_counter() - start) * 1000)
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "TINGMU_DATA_DIR": str(root / "install")}
        completed = subprocess.run([sys.executable, str(Path(__file__).resolve()), str(root)], env=env,
                                   capture_output=True, text=True, timeout=90)
        if completed.returncode:
            raise RuntimeError(completed.stderr[-4000:])
        return {"inbox_30_ready_scan_ms": median(samples), "inbox_lock_checks_per_scan": checks / len(samples),
                **json.loads(completed.stdout)}


if __name__ == "__main__":
    print(json.dumps(_qt_sample(Path(sys.argv[1]))))
