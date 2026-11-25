"""Verify the actual launcher, isolated CPython, and first-use smoke dialog."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_qml_v4 import find, quick as quick


PACKAGING = Path(__file__).resolve().parents[1] / "packaging"


def test_smoke_opens_lazy_workspace_after_observing_player(quick, qtbot, monkeypatch):
    from PySide6.QtCore import QObject

    monkeypatch.syspath_prepend(str(PACKAGING))
    from portable_smoke import check_workspace

    _, bridge, engine, window = quick
    setup = find(window, "workspaceSetup")
    assert not setup.property("visible") and setup.findChild(QObject, "llmConcurrencyCount") is None
    checks = {}
    poll = check_workspace(window, bridge, checks)
    assert checks["opens_in_player"] and checks["workspace_switcher"]
    qtbot.waitUntil(lambda: setup.property("opened"))
    poll()
    assert checks["workspace_llm_concurrency"]
    qtbot.waitUntil(lambda: not setup.property("visible"))
    assert engine._tingmu_warnings == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows .NET launcher and isolated CPython layout")
def test_exe_reads_precompiled_module_and_writes_no_bytecode(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PACKAGING))
    from bundle_files import compile_launcher
    from compile_startup import compile_startup

    root = tmp_path / "新安装"
    app = root / "app"
    runtime = root / "runtime/python"
    app.mkdir(parents=True)
    runtime.mkdir(parents=True)
    base = Path(sys.base_prefix)
    shutil.copy2(base / "pythonw.exe", runtime / "TingmuRuntime.exe")
    for dll in base.glob("*.dll"):
        shutil.copy2(dll, runtime / dll.name)
    tag = f"python{sys.version_info.major}{sys.version_info.minor}"
    (runtime / (tag + "._pth")).write_text(f".\n{base / 'Lib'}\n{base / 'DLLs'}\n../../app\n", encoding="utf-8")
    source = app / "primed.py"
    source.write_text("primed = True\n", encoding="utf-8")
    assert compile_startup(app, include_runtime=False)["files"] == 1
    cached, = app.rglob("*.pyc")
    assert int.from_bytes(cached.read_bytes()[4:8], "little") == 3  # PEP 552 checked hash.
    os.utime(source, (315532800, 315532800))  # Simulate an extractor restoring the ZIP timestamp.
    (app / "uncached.py").write_text("fresh = True\n", encoding="utf-8")
    (app / "bootstrap.py").write_text(
        "from importlib.machinery import SourceFileLoader\n"
        "from pathlib import Path\n"
        "import json, os, sys\n"
        "original = SourceFileLoader.source_to_code\n"
        "def checked(loader, *args, **kwargs):\n"
        "    assert loader.name != 'primed', 'expected cached bytecode'\n"
        "    return original(loader, *args, **kwargs)\n"
        "SourceFileLoader.source_to_code = checked\n"
        "import primed, uncached\n"
        "Path('proof.json').write_text(json.dumps([primed.primed, uncached.fresh,\n"
        "    sys.dont_write_bytecode, os.environ['PYTHONDONTWRITEBYTECODE'], sys.argv[1:]]))\n",
        encoding="utf-8",
    )
    compile_launcher(PACKAGING / "Launcher.cs", root / "Tingmu.exe")
    assert b"System.Windows.Forms" not in (root / "Tingmu.exe").read_bytes()
    relocated = root.rename(tmp_path / "听幕 portable")
    cache = relocated / "app" / cached.relative_to(app)
    before = (cache.stat().st_mtime_ns, cache.read_bytes())
    subprocess.run([relocated / "Tingmu.exe", "中文 参数"], check=True, timeout=15)
    assert json.loads((relocated / "proof.json").read_bytes()) == [True, True, True, "1", ["中文 参数"]]
    assert list(relocated.rglob("*.pyc")) == [cache]
    assert (cache.stat().st_mtime_ns, cache.read_bytes()) == before
