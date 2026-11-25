"""Measure only disposable clean installs under .cache/qa/bundle-opt on Windows."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import py_compile
from statistics import median
import subprocess
import time

BASE = Path(__file__).resolve().parents[1] / ".cache/qa/bundle-opt"
GROUPS = ("runtime/python/Lib/site-packages/PySide6", "runtime/python", "runtime/asr", "app", "bin")


def footprint(root):
    result = {key: {"bytes": 0, "files": 0} for key in (*GROUPS, "total")}
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative.parts[0] in {".cache", "data"} or not path.is_file():
            continue
        size = path.stat().st_size
        for key, value in result.items():
            if key == "total" or relative.is_relative_to(key):
                value["bytes"] += size
                value["files"] += 1
    return result


def _working_sets():
    import ctypes
    from ctypes import wintypes
    import os

    class Counters(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("faults", wintypes.DWORD),
                    ("peak", ctypes.c_size_t), ("working_set", ctypes.c_size_t),
                    ("remaining_counters", ctypes.c_size_t * 6)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    query = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
    query.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]

    def size(handle):
        counters = Counters()
        counters.size = ctypes.sizeof(counters)
        if not query(handle, ctypes.byref(counters), counters.size):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.working_set / 1048576

    launcher = kernel.OpenProcess(0x0400 | 0x0010, False, os.getppid())  # QUERY_INFORMATION | VM_READ
    if not launcher:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return size(kernel.GetCurrentProcess()), size(launcher)
    finally:
        kernel.CloseHandle(launcher)


# This observes the ordinary launcher -> bootstrap -> CLI -> GUI path. Only the
# disposable copy of run_app is instrumented. Timestamp before importing helpers.
FRAME = """
        from PySide6.QtCore import Qt
        from time import perf_counter_ns
        def record():
            end = perf_counter_ns()
            import json
            import os
            from pathlib import Path
            gui_rss, launcher_rss = _working_sets()
            sources = set()
            root = Path(sys.executable).resolve().parents[2]
            for module in tuple(sys.modules.values()):
                name = getattr(module, '__file__', None)
                if name and name.endswith('.py') and Path(name).is_relative_to(root):
                    sources.add(Path(name).relative_to(root).as_posix())
            Path(os.environ['TINGMU_QA_FRAME']).write_text(json.dumps({
                'first_frame_ms': (end - int(os.environ['TINGMU_QA_T0'])) / 1e6,
                'working_set_mib': gui_rss, 'launcher_working_set_mib': launcher_rss, 'modules': sorted(sources),
                'qml_warnings': engine._tingmu_warnings,
            }), encoding='utf-8')
            application.quit()
        window = engine.rootObjects()[0]
        window.frameSwapped.connect(record, Qt.ConnectionType.QueuedConnection)
        window.update()
        return application.exec()
"""


def measure(root, label, count):
    import inspect
    if not root.is_relative_to(BASE.resolve()) or root == BASE.resolve():
        raise ValueError("Benchmark requires a disposable bundle under .cache/qa/bundle-opt")
    report_dir = BASE / label
    report_dir.mkdir(exist_ok=False)
    footprint_result = footprint(root)
    marker = json.loads((root / "tingmu-portable.json").read_text(encoding="utf-8"))
    if not marker.get("clean_install"):
        raise ValueError("Expected clean_install marker")
    source = root / "app/tingmu/quick/main.py"
    old = source.read_bytes()
    stat = source.stat()
    cache = Path(importlib.util.cache_from_source(str(source)))
    old_cache = cache.read_bytes() if cache.exists() else None
    helper = inspect.getsource(_working_sets)
    key = "        return application.exec()\n"
    code = source.read_text(encoding="utf-8")
    assert code.count(key) == 1
    env = {**os.environ, "QT_QPA_PLATFORM": "windows", "QT_QUICK_BACKEND": "software",
           "TINGMU_PORTABLE_ROOT": str(root), "PYTHONDONTWRITEBYTECODE": "1",
           "HF_HUB_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1"}
    interpreter = root / "runtime/python/python.exe"
    startup, imports = [], []
    import_code = ("from time import perf_counter_ns as t; start=t(); import tingmu.quick.main; "
                   "print((t()-start)/1e6)")
    for _ in range(count):
        value = subprocess.check_output([interpreter, "-B", "-c", import_code], cwd=root, env=env, text=True)
        imports.append(float(value))
    existing = set(root.glob("runtime/python/**/*.pyc")) | set((root / "app").rglob("*.pyc"))
    try:
        source.write_text(code.replace(key, FRAME) + "\n" + helper, encoding="utf-8")
        if old_cache:
            py_compile.compile(str(source), doraise=True)
        for i in range(count):
            qml = root / ".cache/qml"
            if qml.exists():
                qml.rename(report_dir / f"qml-{i}")
            output = report_dir / f"frame-{i}.json"
            env["TINGMU_QA_FRAME"] = str(output)
            env["TINGMU_QA_T0"] = str(time.perf_counter_ns())
            subprocess.run([root / "Tingmu.exe", "--data-dir", root / f".cache/qa-data-{label}-{i}", "gui"],
                           cwd=root, env=env, check=True, timeout=40)
            sample = json.loads(output.read_text(encoding="utf-8"))
            assert not sample["qml_warnings"]
            startup.append(sample)
            # The historical launcher can write early .pycs in isolated ._pth mode.
            for path in set(root.glob("runtime/python/**/*.pyc")) | set((root / "app").rglob("*.pyc")):
                if path not in existing:
                    path.unlink()
    finally:
        source.write_bytes(old)
        os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        if old_cache:
            cache.write_bytes(old_cache)
    result = {"size": footprint_result, "import_ms": imports, "median_import_ms": median(imports)}
    for key in ("first_frame_ms", "working_set_mib", "launcher_working_set_mib"):
        result[key] = [s[key] for s in startup]
        result["median_" + key] = median(result[key])
    (report_dir / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("label", type=str)
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()
    if Path(args.label).name != args.label or args.label in {".", ".."}:
        parser.error("label must name a fresh report directory")
    measure(args.bundle.resolve(), args.label, args.count)
