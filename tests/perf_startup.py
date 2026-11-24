"""Native Windows first-frame benchmark. Each sample has a new process, catalog and QML disk cache."""

import ctypes
import json
import os
from pathlib import Path
from statistics import median
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns


def _working_set_bytes():
    from ctypes import wintypes

    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
            *[(field, ctypes.c_size_t) for field in (
                "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage",
                "QuotaPagedPoolUsage", "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage",
                "PagefileUsage", "PeakPagefileUsage")],
        ]

    counters = ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    get_memory = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    get_memory.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD]
    if not get_memory(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.WorkingSetSize


def _sample(folder, start_ns):
    from PySide6.QtCore import QObject, Qt, QTimer

    from tingmu.quick.main import create_qml_app

    app, bridge, engine = create_qml_app(folder / "data")
    window = engine.rootObjects()[0]
    result = {}

    def first_frame():
        if result:
            return
        elapsed = (perf_counter_ns() - start_ns) / 1_000_000
        rss = _working_set_bytes()
        result.update(startup_ms=elapsed, initial_qobjects=1 + len(window.findChildren(QObject)),
                      startup_rss_mib=rss / (1024 * 1024))
        app.quit()

    window.frameSwapped.connect(first_frame, Qt.ConnectionType.QueuedConnection)
    window.update()
    QTimer.singleShot(30_000, app.quit)
    app.exec()
    if not result:
        raise RuntimeError("No frame presented within 30 seconds")
    if engine._tingmu_warnings:
        raise RuntimeError("\n".join(engine._tingmu_warnings))
    window.setProperty("closingSession", True)
    window.close()
    bridge.shutdown()
    print(json.dumps(result), flush=True)


def measure(folder: Path) -> dict:
    """Median of five launches. Startup includes process creation, imports and first native frame."""
    if sys.platform != "win32":
        raise RuntimeError("The native startup/RSS benchmark requires Windows")
    folder = Path(folder).resolve()
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    for _ in range(5):
        with TemporaryDirectory(dir=folder) as run:
            base = Path(run)
            env = {**os.environ, "QT_QPA_PLATFORM": "windows", "PYTHONIOENCODING": "utf-8"}
            # configure_local_caches confines its overrides to the installation's .cache.
            repo_cache = Path(__file__).resolve().parents[1] / ".cache"
            repo_cache.mkdir(exist_ok=True)
            with TemporaryDirectory(dir=repo_cache) as runtime_cache:
                env.update(TINGMU_CACHE_DIR=runtime_cache, QML_DISK_CACHE_PATH=str(Path(runtime_cache) / "qml"))
                command = [sys.executable, str(Path(__file__).resolve()), "--sample", str(base)]
                command.append(str(perf_counter_ns()))
                child = subprocess.run(command, env=env, capture_output=True, encoding="utf-8",
                                       errors="replace", timeout=60)
            if child.returncode:
                raise RuntimeError(f"Startup benchmark failed ({child.returncode}): {child.stderr}")
            results.append(json.loads(child.stdout.strip().splitlines()[-1]))
    return {key: median(sample[key] for sample in results) for key in results[0]}


if __name__ == "__main__":
    if sys.argv[1:2] == ["--sample"]:
        _sample(Path(sys.argv[2]), int(sys.argv[3]))
    else:
        print(json.dumps(measure(Path(sys.argv[1])), indent=2))
