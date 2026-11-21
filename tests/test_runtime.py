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
    import tingmu.runtime as runtime
    project = tmp_path / "source"
    (project / "src/tingmu").mkdir(parents=True)
    (project / "pyproject.toml").touch()
    monkeypatch.setattr(runtime, "__file__", str(project / "src/tingmu/runtime.py"))
    root = project / ".cache/isolated"
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


def test_source_ignores_external_spill_targets_and_restores_shared_models(tmp_path, monkeypatch):
    import tingmu.runtime as runtime
    from tingmu import media, workspace_models

    project = tmp_path / "moved source"
    (project / "src/tingmu").mkdir(parents=True)
    (project / "pyproject.toml").touch()
    monkeypatch.setattr(runtime, "__file__", str(project / "src/tingmu/runtime.py"))
    outside = tmp_path / "forbidden writes"
    for key in ("TINGMU_CACHE_DIR", "TINGMU_DATA_DIR", "TINGMU_QWEN_PYTHON", "HF_ASSETS_CACHE",
                "HUGGINGFACE_HUB_CACHE", "HF_MODULES_CACHE", "QSG_RHI_PIPELINE_CACHE_SAVE",
                "TRANSFORMERS_CACHE", "TMPDIR", "HF_XET_LOG_DEST", "FFREPORT"):
        monkeypatch.setenv(key, str(outside / key))
    previous = dict(os.environ)
    previous_tempdir = tempfile.tempdir
    try:
        cache = runtime.configure_local_caches()
        assert runtime.installation_root() == project
        assert runtime.data_root() == project / ".data"
        assert cache == media._cache_root() == project / ".cache"
        assert not outside.exists()
        assert Path(os.environ["HF_ASSETS_CACHE"]).is_relative_to(cache)
        assert os.environ["QT_DISABLE_SHADER_DISK_CACHE"] == "1"
        assert not {"QSG_RHI_PIPELINE_CACHE_SAVE", "HF_XET_LOG_DEST", "FFREPORT", "TRANSFORMERS_CACHE"} & os.environ.keys()
        assert {workspace_models._assets(m)[0] for m in ("qwen3-asr-0.6b", "qwen3-asr-1.7b")} == {cache / "qwen-models"}
        assert workspace_models._assets("large-v3-turbo")[0] == cache / "models"
        assert workspace_models._assets("qwen3-asr-0.6b")[1][-1] == workspace_models._assets("qwen3-asr-1.7b")[1][-1]
    finally:
        os.environ.clear()
        os.environ.update(previous)
        tempfile.tempdir = previous_tempdir


def test_confined_path_rejects_traversal_and_link_to_outside(tmp_path):
    import pytest
    from tingmu.runtime import confined_path
    root = tmp_path / "install"
    root.mkdir()
    with pytest.raises(ValueError):
        confined_path(root, "../elsewhere/output")
    outside = tmp_path / "outside"
    outside.mkdir()
    junction = root / "cache-link"
    try:
        junction.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        import subprocess
        subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)],
                       capture_output=True, check=True)
    with pytest.raises(ValueError):
        confined_path(root, junction / "weights/model.bin")
    assert not list(outside.iterdir())
