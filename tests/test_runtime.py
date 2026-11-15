import os
from pathlib import Path
import tempfile

from tingmu.runtime import configure_local_caches


def test_portable_paths_follow_moved_package_and_ignore_external_overrides(tmp_path, monkeypatch):
    import tingmu.runtime as runtime
    portable = tmp_path / "听幕 moved folder"
    package = portable / "app/tingmu"
    package.mkdir(parents=True)
    (portable / "tingmu-portable.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runtime, "__file__", str(package / "runtime.py"))
    monkeypatch.setenv("TINGMU_CACHE_DIR", str(tmp_path / "wrong-cache"))
    monkeypatch.setenv("TINGMU_DATA_DIR", str(tmp_path / "wrong-data"))
    previous = dict(os.environ)
    previous_tempdir = tempfile.tempdir
    try:
        assert runtime.portable_root() == portable
        assert runtime.cache_root() == portable / ".cache"
        assert runtime.data_root() == portable / "data"
        runtime.configure_local_caches()
        assert Path(os.environ["TINGMU_QWEN_PYTHON"]) == portable / "runtime/asr/python.exe"
        assert Path(tempfile.gettempdir()).is_relative_to(portable)
        from tingmu.storage import Store
        assert Store().data_dir == portable / "data"
    finally:
        os.environ.clear()
        os.environ.update(previous)
        tempfile.tempdir = previous_tempdir


def test_caches_and_temp_override_external_defaults_without_global_changes(tmp_path, monkeypatch):
    root = tmp_path / "project-cache"
    monkeypatch.setenv("TINGMU_CACHE_DIR", str(root))
    for key in ("HF_HOME", "UV_CACHE_DIR", "TEMP", "TMP", "TORCH_HOME"):
        monkeypatch.setenv(key, str(tmp_path / "outside-project"))
    previous = dict(os.environ)
    previous_tempdir = tempfile.tempdir
    try:
        configure_local_caches()
        for key in ("HF_HOME", "HF_HUB_CACHE", "HF_XET_CACHE", "UV_CACHE_DIR", "UV_PYTHON_INSTALL_DIR",
                    "PIP_CACHE_DIR", "TORCH_HOME", "CUDA_CACHE_PATH", "TEMP", "TMP", "TMPDIR"):
            path = Path(os.environ[key])
            assert path.is_relative_to(root)
            assert path.is_dir()
        with tempfile.TemporaryFile() as handle:
            handle.write(b"local temporary storage")
        assert Path(tempfile.gettempdir()).is_relative_to(root)
        os.environ["UV_CACHE_DIR"] = str(root / "uv-qwen")
        configure_local_caches()
        assert os.environ["UV_CACHE_DIR"] == str(root / "uv-qwen")
    finally:
        os.environ.clear()
        os.environ.update(previous)
        tempfile.tempdir = previous_tempdir
