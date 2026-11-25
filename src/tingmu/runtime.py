"""Constrain generated application files and SDK caches to this installation."""

import json
import os
from pathlib import Path
import sys
import tempfile


def portable_root() -> Path | None:
    """Find the release marker from shipped code / executable, independent of cwd."""
    root = Path(__file__).resolve().parents[2]
    if (root / "tingmu-portable.json").is_file():
        return root
    root = Path(sys.executable).resolve().parent
    return root if (root / "tingmu-portable.json").is_file() else None


def _source_root() -> Path:
    source = Path(__file__).resolve()
    for parent in source.parents:
        if (parent / "pyproject.toml").is_file() and (parent / "src/tingmu").is_dir():
            return parent
    # A standalone installed wheel owns its package directory, not the user's home.
    return source.parent


def installation_root() -> Path:
    return portable_root() or _source_root()


def is_clean_install() -> bool:
    root = portable_root()
    if root is None:
        return False
    try:
        manifest = json.loads((root / "tingmu-portable.json").read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("clean_install") is True


def confined_path(root: Path, path: Path | str) -> Path:
    """Resolve links before admitting an output. Relative paths are rooted at root."""
    return _confined(Path(root).resolve(), path)


def _confined(root: Path, path: Path | str) -> Path:
    """Check a candidate against an already resolved root, including child junctions."""
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
            return _confined(allowed, inherited)
        except (ValueError, OSError, RuntimeError):
            pass
    return _confined(allowed, default)


def _data_dir(root: Path, portable: bool) -> Path:
    if portable:
        return _confined(root, "data")
    return _directory(root, root / ".data", "TINGMU_DATA_DIR")


def _cache_dir(root: Path, portable: bool) -> Path:
    cache = _confined(root, ".cache")
    if portable or not os.environ.get("TINGMU_CACHE_DIR"):
        return cache
    return _directory(cache, cache, "TINGMU_CACHE_DIR")


def data_root() -> Path:
    portable = portable_root()
    return _data_dir(portable or _source_root(), bool(portable))


def cache_root() -> Path:
    portable = portable_root()
    return _cache_dir(portable or _source_root(), bool(portable))


def configure_local_caches() -> Path:
    """Set process-only paths before SDK imports without affecting host settings."""
    portable = portable_root()
    installation = portable or _source_root()
    root = _cache_dir(installation, bool(portable))
    os.environ["TINGMU_CACHE_DIR"] = str(root)
    os.environ["TINGMU_DATA_DIR"] = str(_data_dir(installation, bool(portable)))
    if portable:
        os.environ["TINGMU_QWEN_PYTHON"] = str(_confined(portable, "runtime/asr/python.exe"))
        binary_dir = str(_confined(portable, "bin"))
        if os.environ.get("PATH", "").split(os.pathsep)[0] != binary_dir:
            os.environ["PATH"] = binary_dir + os.pathsep + os.environ.get("PATH", "")
        os.environ["QT_PLUGIN_PATH"] = str(_confined(portable, "runtime/python/Lib/site-packages/PySide6/plugins"))
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
    os.environ["HF_TOKEN_PATH"] = str(_confined(root, "huggingface/token"))
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
    # A pip --target install is outside CPython's isolated ._pth. Make it visible
    # without importing recognition libraries during player startup. Qwen owns a
    # different interpreter/ABI and must retain its own packages.
    if portable and not os.environ.get("TINGMU_QWEN_WORKER"):
        target = _confined(portable, ".cache/runtime-deps/local-asr")
        if target.is_dir():
            from .runtime_probe_worker import activate_site
            activate_site(target)
    tempfile.tempdir = os.environ["TEMP"]
    return root
