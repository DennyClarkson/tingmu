"""Constrain generated application files and SDK caches to this installation."""

import os
from pathlib import Path
import sys
import tempfile


def portable_root() -> Path | None:
    """Find the release marker from shipped code / executable, independent of cwd."""
    candidates = [Path(__file__).resolve().parents[2], Path(sys.executable).resolve().parent]
    return next((p for p in candidates if (p / "tingmu-portable.json").is_file()), None)


def installation_root() -> Path:
    portable = portable_root()
    if portable:
        return portable
    source = Path(__file__).resolve()
    for parent in source.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src/tingmu").is_dir():
            return parent
    # A standalone installed wheel owns its package directory, not the user's home.
    return source.parent


def confined_path(root: Path, path: Path | str) -> Path:
    """Resolve links before admitting an output. Relative paths are rooted at root."""
    root = Path(root).resolve()
    path = (root / Path(path).expanduser()).resolve()
    if not path.is_relative_to(root):
        raise ValueError("生成的文件必须位于应用目录内")
    return path


def inside_installation(path: Path | str) -> Path:
    return confined_path(installation_root(), path)


def _directory(allowed: Path, default: Path, variable: str) -> Path:
    inherited = os.environ.get(variable)
    if inherited:
        try:
            return confined_path(allowed, inherited)
        except (ValueError, OSError, RuntimeError):
            pass
    return confined_path(allowed, default)


def data_root() -> Path:
    root = installation_root()
    if portable_root():
        return confined_path(root, "data")
    return _directory(root, root / ".data", "TINGMU_DATA_DIR")


def cache_root() -> Path:
    root = inside_installation(".cache")
    if portable_root():
        return root
    # Test/source runs may isolate their cache inside the shared project cache.
    return _directory(root, root, "TINGMU_CACHE_DIR")


def configure_local_caches() -> Path:
    """Set process-only paths before SDK imports without affecting host settings."""
    root = cache_root()
    portable = portable_root()
    os.environ["TINGMU_CACHE_DIR"] = str(root)
    os.environ["TINGMU_DATA_DIR"] = str(data_root())
    if portable:
        os.environ["TINGMU_QWEN_PYTHON"] = str(confined_path(portable, "runtime/asr/python.exe"))
        binary_dir = str(confined_path(portable, "bin"))
        if os.environ.get("PATH", "").split(os.pathsep)[0] != binary_dir:
            os.environ["PATH"] = binary_dir + os.pathsep + os.environ.get("PATH", "")
        os.environ["QT_PLUGIN_PATH"] = str(confined_path(portable, "runtime/python/Lib/site-packages/PySide6/plugins"))
    folders = {
        "UV_CACHE_DIR": "uv", "UV_PYTHON_INSTALL_DIR": "python", "PIP_CACHE_DIR": "pip",
        "XDG_CACHE_HOME": "xdg", "HF_HOME": "huggingface", "HF_HUB_CACHE": "huggingface/hub",
        "HF_ASSETS_CACHE": "huggingface/assets", "HF_XET_CACHE": "huggingface/xet",
        "HF_MODULES_CACHE": "huggingface/modules", "TORCH_HOME": "torch",
        "TORCH_EXTENSIONS_DIR": "torch/extensions", "TORCHINDUCTOR_CACHE_DIR": "torch/inductor",
        "TRITON_CACHE_DIR": "triton", "NUMBA_CACHE_DIR": "numba", "CUDA_CACHE_PATH": "cuda",
        "MPLCONFIGDIR": "matplotlib", "QML_DISK_CACHE_PATH": "qml", "QT_SHADER_CACHE_PATH": "qt-shaders",
        "TEMP": "tmp", "TMP": "tmp", "TMPDIR": "tmp",
    }
    for variable, folder in folders.items():
        selected = _directory(root, root / folder, variable)
        selected.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(selected)
    # Legacy HF/Transformers variables can take precedence in the Qwen environment.
    for legacy in ("HUGGINGFACE_HUB_CACHE", "HUGGINGFACE_ASSETS_CACHE", "TRANSFORMERS_CACHE",
                   "PYTORCH_TRANSFORMERS_CACHE", "PYTORCH_PRETRAINED_BERT_CACHE"):
        os.environ.pop(legacy, None)
    os.environ["HF_TOKEN_PATH"] = str(confined_path(root, "huggingface/token"))
    for redirect in ("HF_XET_LOG_DEST", "HF_XET_SYSTEM_MONITOR_LOG_PATH", "HF_XET_SESSION_DIR_NAME",
                     "HF_XET_SHARD_CACHE_SUBDIR", "HF_XET_DATA_STAGING_SUBDIR", "HF_XET_LOG_PREFIX",
                     "FFREPORT", "TORCH_LOGS_OUT", "TORCH_TRACE"):
        os.environ.pop(redirect, None)
    # Qt Quick otherwise writes its automatic graphics pipeline cache to QStandardPaths.
    os.environ["QT_DISABLE_SHADER_DISK_CACHE"] = "1"
    os.environ["QSG_RHI_DISABLE_DISK_CACHE"] = "1"
    os.environ.pop("QSG_RHI_PIPELINE_CACHE_SAVE", None)
    os.environ.pop("QSG_RHI_PIPELINE_CACHE_LOAD", None)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ.pop("PYTHONPYCACHEPREFIX", None)
    sys.dont_write_bytecode = True
    tempfile.tempdir = os.environ["TEMP"]
    return root
