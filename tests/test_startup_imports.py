"""Startup imports are measured in fresh interpreters, independent of pytest collection."""

import json
import os
import subprocess
import sys

import pytest


def run_python(program, *args):
    result = subprocess.run(
        [sys.executable, "-B", "-c", program, *args],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen", "QT_QUICK_BACKEND": "software"},
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("module", ["tingmu.batch", "tingmu.quick.main"])
def test_import_keeps_processing_and_model_backends_unloaded(module):
    run_python("""
import importlib, sys
blocked = {
    "tingmu.pipeline", "tingmu.preparation", "tingmu.agent", "tingmu.review", "tingmu.providers",
    "tingmu.media", "tingmu.workspace_models", "tingmu.quick.ui_actions", "tingmu.app_actions",
}
importlib.import_module(sys.argv[1])
assert not blocked.intersection(sys.modules), sorted(blocked.intersection(sys.modules))
""", module)


def test_media_metadata_lookup_is_worker_only():
    run_python("""
import sys
before = set(sys.modules)
import tingmu.media
assert "importlib.metadata" not in set(sys.modules) - before
""")


def test_clean_player_defers_tool_actions_until_used(tmp_path):
    run_python("""
import sys
from pathlib import Path
from tingmu.quick.main import create_qml_app
from tingmu.models import AppSettings
from tingmu.storage import atomic_json
root = Path(sys.argv[1])
settings = AppSettings(text_models_enabled=False, cloud_text_enabled=False)
atomic_json(root / "settings.json", settings.to_dict())
app, bridge, engine = create_qml_app(root)
try:
    assert not engine._tingmu_warnings, engine._tingmu_warnings
    lazy = {"tingmu.pipeline", "tingmu.media", "tingmu.agent", "tingmu.workspace_models",
            "tingmu.quick.ui_actions", "tingmu.app_actions"}
    assert not lazy.intersection(sys.modules), sorted(lazy.intersection(sys.modules))
    actions = bridge.actions
    assert "tingmu.app_actions" in sys.modules and actions is bridge.actions
finally:
    bridge.shutdown()
    engine.rootObjects()[0].hide()
""", str(tmp_path))


def test_cli_gui_entry_skips_legacy_ui():
    run_python("""
import sys
from tingmu.cli import main
from tingmu.quick import main as quick
calls = []
quick.run_app = lambda path, data: calls.append((path, data)) or 0
assert main(["gui", "movie.mkv"]) == 0
assert calls == [("movie.mkv", None)]
assert "tingmu.ui" not in sys.modules
""")


def test_download_cancellation_monitors_the_python_worker_itself():
    from tingmu.workspace_models import _download_python

    command, environment = _download_python()
    program = """
import json, os, sys
print(json.dumps([os.getpid(), sys.prefix, sys.dont_write_bytecode, os.getenv("__PYVENV_LAUNCHER__")]))
"""
    with subprocess.Popen([*command, "-c", program], env=environment, stdout=subprocess.PIPE) as worker:
        output, _ = worker.communicate(timeout=10)
        assert worker.returncode == 0
        assert json.loads(output) == [worker.pid, sys.prefix, True, None]


def test_cache_setup_checks_child_junction_of_resolved_base(tmp_path):
    portable = tmp_path / "portable"
    package = portable / "app/tingmu"
    package.mkdir(parents=True)
    (portable / "tingmu-portable.json").write_text("{}", encoding="utf-8")
    cache = portable / ".cache"
    cache.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = cache / "huggingface"
    try:
        junction.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)],
                       capture_output=True, check=True)
    run_python("""
import sys
from tingmu import runtime
runtime.__file__ = sys.argv[1]
try:
    runtime.configure_local_caches()
except ValueError:
    pass
else:
    raise AssertionError("accepted a cache directory outside the portable installation")
""", str(package / "runtime.py"))
    assert not list(outside.iterdir())
