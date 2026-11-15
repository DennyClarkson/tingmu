"""Keep this application's downloads and temporary files beside the project."""

import os
from pathlib import Path
import sys
import tempfile


def portable_root() -> Path | None:
    """Locate the shipped folder by its marker, independent of working directory."""
    candidates = [Path(__file__).resolve().parents[2], Path(sys.executable).resolve().parent]
    for candidate in candidates:
        if (candidate / "tingmu-portable.json").is_file():
            return candidate
    return None


def data_root() -> Path:
    portable = portable_root()
    if portable:
        return portable / "data"
    configured = os.environ.get("TINGMU_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    project = Path(__file__).resolve().parents[2]
    return project / (".data" if (project / "pyproject.toml").is_file() else "data")


def cache_root() -> Path:
    portable = portable_root()
    if portable:
        return portable / ".cache"
    configured = os.environ.get("TINGMU_CACHE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    project = Path(__file__).resolve().parents[2]
    if (project / "pyproject.toml").is_file():
        return project / ".cache"
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "Tingmu" / "cache"


def configure_local_caches() -> Path:
    """Set process-only paths before SDK imports; never change system settings.

    Existing per-tool locations inside our cache are retained (e.g. uv-qwen).
    Generic inherited cache paths elsewhere must not fill the system drive.
    TINGMU_CACHE_DIR is the explicit application-specific root override.
    """
    root = cache_root().resolve()
    portable = portable_root()
    if portable:
        # Ignore inherited machine/development overrides in a portable distribution.
        os.environ["TINGMU_CACHE_DIR"] = str(root)
        os.environ["TINGMU_DATA_DIR"] = str(data_root())
        os.environ["TINGMU_QWEN_PYTHON"] = str(portable / "runtime/asr/python.exe")
        os.environ["PATH"] = str(portable / "bin") + os.pathsep + os.environ.get("PATH", "")
        os.environ["QT_PLUGIN_PATH"] = str(portable / "runtime/python/Lib/site-packages/PySide6/plugins")
    folders = {
        "UV_CACHE_DIR": "uv", "UV_PYTHON_INSTALL_DIR": "python",
        "PIP_CACHE_DIR": "pip", "HF_HOME": "huggingface",
        "HF_HUB_CACHE": "huggingface/hub", "HF_XET_CACHE": "huggingface/xet",
        "TORCH_HOME": "torch", "TORCHINDUCTOR_CACHE_DIR": "torch/inductor",
        "NUMBA_CACHE_DIR": "numba", "CUDA_CACHE_PATH": "cuda",
        "QML_DISK_CACHE_PATH": "qml", "QT_SHADER_CACHE_PATH": "qt-shaders",
        "TEMP": "tmp", "TMP": "tmp", "TMPDIR": "tmp",
    }
    for variable, folder in folders.items():
        selected = root / folder
        inherited = os.environ.get(variable)
        if inherited:
            candidate = Path(inherited).expanduser().resolve()
            if candidate.is_relative_to(root):
                selected = candidate
        selected.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(selected)
    # tempfile caches its first choice; reset it if a caller imported it early.
    tempfile.tempdir = os.environ["TEMP"]
    return root
