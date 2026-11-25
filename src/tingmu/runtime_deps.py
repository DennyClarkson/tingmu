"""Offline discovery of executable runtime components. Never installs dependencies or weights."""

from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
import site
import subprocess
import sys
import time

from .runtime import confined_path

KINDS = ("ffmpeg", "local-asr", "whisper-cuda", "qwen")
HINTS = {
    "ffmpeg": "缺少可用的 FFmpeg 和 ffprobe。请在「运行环境与依赖管理」中选择 FFmpeg 目录或安装媒体工具。",
    "local-asr": "缺少本地 Whisper / ONNX 运行环境。请在「运行环境与依赖管理」中检测或安装本地语音组件。",
    "whisper-cuda": "Whisper GPU 加速需要可用的 NVIDIA 驱动、CUDA 12 cuBLAS 与 cuDNN 9。可使用 CPU 或在「运行环境与依赖管理」中配置。",
    "qwen": "缺少可用的 Qwen 本地 Python 环境。请在「运行环境与依赖管理」中选择已安装 torch 和 qwen_asr 的解释器，或安装 Qwen。",
}
_SUFFIX = ".exe" if os.name == "nt" else ""
_CUDA_LIBS = ("cublas64_12.dll", "cudnn64_9.dll") if os.name == "nt" else ("libcublas.so.12", "libcudnn.so.9")
_CHECKS = {}


@lru_cache(maxsize=32)
def _saved_paths(filename, revision):
    try:
        if revision and revision[-1] <= 128 * 1024:
            value = json.loads(filename.read_bytes()).get("paths", {})
            return value if isinstance(value, dict) else {}
    except (OSError, ValueError, AttributeError):
        pass
    return {}


def _configured(root, kind):
    filename = confined_path(root, "data/runtime-setup.json")
    value = _saved_paths(filename, _stamp(filename)).get(kind)
    return Path(value) if isinstance(value, str) and value and Path(value).is_absolute() else None


def local_target(root):
    return confined_path(root, ".cache/runtime-deps/local-asr")


def _empty(kind):
    return {"kind": kind, "ready": False, "path": "", "source": "", "detail": HINTS[kind]}


def _offline_env(root):
    cache = confined_path(root, ".cache")
    # Probes and reused interpreters inherit installation-owned caches. Isolated mode
    # and -B keep the caller's packages and user bytecode out of a selected Python.
    env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
           "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "DO_NOT_TRACK": "1",
           "GRADIO_ANALYTICS_ENABLED": "False"}
    confined_path(cache, "tmp").mkdir(parents=True, exist_ok=True)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.pop("__PYVENV_LAUNCHER__", None)
    for key, subdir in {"TORCH_EXTENSIONS_DIR": "torch/extensions", "TORCHINDUCTOR_CACHE_DIR": "torch/inductor",
                        "TRITON_CACHE_DIR": "triton", "HF_ASSETS_CACHE": "huggingface/assets",
                        "HF_XET_CACHE": "huggingface/xet", "HF_MODULES_CACHE": "huggingface/modules",
                        "TORCH_HOME": "torch", "HF_HOME": "huggingface", "HF_HUB_CACHE": "huggingface/hub",
                        "XDG_CACHE_HOME": "xdg", "NUMBA_CACHE_DIR": "numba", "MPLCONFIGDIR": "matplotlib",
                        "CUDA_CACHE_PATH": "cuda", "TEMP": "tmp", "TMP": "tmp", "TMPDIR": "tmp"}.items():
        env[key] = str(confined_path(cache, subdir))
    return env


def worker_command(executable, env):
    """Launch the interpreter itself so cancellation closes native libraries and pipe handles."""
    path = Path(executable)
    if path.stem.lower() == "pythonw":
        path = path.with_name("python.exe")
    if os.name == "nt" and path.parent.name.lower() == "scripts":
        cfg = path.parent.parent / "pyvenv.cfg"
        if cfg.is_file():
            fields = dict(line.split(" = ", 1) for line in cfg.read_text("utf-8").splitlines() if " = " in line)
            base = Path(fields.get("home", "")) / "python.exe"
            if base.is_absolute() and base.is_file():
                env["__PYVENV_LAUNCHER__"] = str(path)
                return base
    return path


def _run(command, *, timeout, cancelled=None, **kwargs):
    if cancelled is None:
        return subprocess.run(command, capture_output=True, timeout=timeout, **kwargs)
    if cancelled():
        raise InterruptedError("已停止运行环境检测")
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs) as child:
        deadline = time.monotonic() + timeout
        try:
            while True:
                if cancelled():
                    raise InterruptedError("已停止运行环境检测")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    out, err = child.communicate(timeout=min(.2, remaining))
                    return subprocess.CompletedProcess(command, child.returncode, out, err)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate()


def _python_check(root, executable, kind, *, cancelled=None, **options):
    result = {"interpreter": str(executable), "cpu_usable": False, "cuda_usable": False, "error": "runtime_missing"}
    if not Path(executable).is_file():
        return result
    program = Path(__file__).with_name("runtime_probe_worker.py")
    try:
        env = _offline_env(root)
        command = worker_command(executable, env)
        output = _run([str(command), "-I", "-B", "-S", str(program),
                       json.dumps({**options, "cache": env["XDG_CACHE_HOME"]}), kind],
                      stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                      env=env, timeout=35, cancelled=cancelled,
                      creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if output.returncode == 0:
            result.update(json.loads(output.stdout.strip().splitlines()[-1]))
        else:
            result["error"] = "runtime_probe_failed"
    except InterruptedError:
        raise
    except (OSError, ValueError, IndexError, subprocess.TimeoutExpired):
        result["error"] = "runtime_probe_failed_or_timed_out"
    return result


def _ffmpeg_dirs(root, configured):
    yield "portable", root / "bin"
    if configured:
        yield "configured", configured.parent if configured.is_file() else configured
        yield "configured", configured / "bin"
    for directory in os.get_exec_path():
        if directory and Path(directory).is_absolute():
            yield "system", Path(directory)
    if os.name == "nt":
        for env, pattern in (("SystemDrive", "ffmpeg/bin"), ("ProgramFiles", "ffmpeg/bin"),
                             ("ProgramData", "chocolatey/bin"), ("USERPROFILE", "scoop/apps/ffmpeg/current/bin"),
                             ("LOCALAPPDATA", "Microsoft/WinGet/Packages/*FFmpeg*/*/bin")):
            location = os.environ.get(env)
            if location:
                base = Path(location + os.sep) if env == "SystemDrive" else Path(location)
                for directory in base.glob(pattern):
                    yield "system", directory
    if (root / "pyproject.toml").is_file():
        yield "system", root / "dist/Tingmu/bin"


def _ffmpeg(root, directory, **control):
    paths = {name: directory / (name + _SUFFIX) for name in ("ffmpeg", "ffprobe")}
    if all(path.is_file() for path in paths.values()):
        versions = {}
        for name, path in paths.items():
            try:
                run = _run([str(path), "-version"], stdin=subprocess.DEVNULL, **control,
                                     timeout=5, env=_offline_env(root), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                first = run.stdout.decode(errors="replace").splitlines()[0]
                if run.returncode or not first.lower().startswith(name + " version "):
                    return {}
                versions[name] = first[:180]
            except InterruptedError:
                raise
            except (OSError, IndexError, subprocess.TimeoutExpired):
                return {}
        return {**{k: str(v) for k, v in paths.items()}, "ready": True, "version": versions["ffmpeg"],
                "files": list(map(str, paths.values()))}
    return {}


def _interpreter(folder):
    if folder.is_file():
        return folder
    names = ("python.exe", "Scripts/python.exe") if os.name == "nt" else ("bin/python", "python")
    return next((folder / name for name in names if (folder / name).is_file()), folder / names[0])


def _qwen_candidates(root, configured):
    yield "portable", _interpreter(root / "runtime/asr")
    yield "portable", _interpreter(root / ".cache/qwen-venv")
    if configured:
        yield "configured", _interpreter(configured)
    for key in ("TINGMU_QWEN_PYTHON", "VIRTUAL_ENV", "CONDA_PREFIX"):
        value = os.environ.get(key)
        if value and Path(value).is_absolute():
            yield "system", _interpreter(Path(value))
    yield "current", Path(sys.executable)
    yield "system", _interpreter(root / ".venv")
    for directory in os.get_exec_path():
        base = Path(directory)
        if not base.is_absolute() or "windowsapps" in {part.lower() for part in base.parts}:
            continue
        yield "system", base / ("python" + _SUFFIX)
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        for python in Path(os.environ["LOCALAPPDATA"]).glob("Programs/Python/Python*/python.exe"):
            yield "system", python


def _candidate_sites(root, configured):
    yield "portable", local_target(root)
    if configured:
        yield "configured", configured
        yield "configured", configured / "Lib/site-packages"
    yield "current", None


def _cuda_candidates(root, configured):
    yield "portable", [root / ".cache/runtime-deps/whisper-cuda/bin"]
    sites = [local_target(root), *map(Path, site.getsitepackages())]
    for folder in sites:
        yield "current", list((folder / "nvidia").glob("*/bin"))
    for source, executable in _qwen_candidates(root, _configured(root, "qwen")):
        if not executable.is_file():
            continue
        base = executable.parent.parent if executable.parent.name.lower() in {"bin", "scripts"} else executable.parent
        yield source, [base / "Lib/site-packages/torch/lib"]
        for lib in base.glob("lib/python*/site-packages/torch/lib"):
            yield source, [lib]
    if configured:
        yield "configured", [configured, configured / "bin", configured / "bin/x64"]
    common = []
    for key, value in os.environ.items():
        if (key.upper().startswith("CUDA_PATH") or key.upper() in {"CUDNN_PATH", "CUDNN_HOME"}) and value:
            base = Path(value)
            common.extend([base / "bin", base / "bin/x64", base])
    common.extend(Path(folder) for folder in os.get_exec_path() if folder and Path(folder).is_absolute())
    programs = os.environ.get("ProgramFiles")
    if programs:
        for pattern in ("NVIDIA GPU Computing Toolkit/CUDA/v12*/bin", "NVIDIA/CUDNN/v9*/bin/*"):
            common.extend(Path(programs).glob(pattern))
    yield "system", common


def _cuda(root, dirs, **control):
    dirs = list(dict.fromkeys(path for path in dirs if path.is_dir()))
    libraries = [next((path / name for path in dirs if (path / name).is_file()), None) for name in _CUDA_LIBS]
    if not all(libraries):
        return {}
    # Only add directories belonging to the chosen runtime; PATH already covers
    # system dependencies. Leave unrelated PATH entries out of AddDllDirectory.
    owners = {library.parent for library in libraries}
    dirs = [path for path in dirs if path in owners or any(path.glob("cudart*")) or any(path.glob("nvrtc*"))]
    directories = list(map(str, dirs))
    files = list(map(str, libraries))
    result = _python_check(root, sys.executable, "whisper-cuda", directories=directories, libraries=files, **control)
    return {**result, "ready": result["cuda_usable"], "path": str(libraries[0].parent),
            "directories": directories, "files": files}


def _probe_python(root, kind, candidate, **control):
    if kind == "local-asr":
        if candidate and not (candidate / "faster_whisper/__init__.py").is_file():
            return {}
        result = _python_check(root, sys.executable, kind, site=str(candidate) if candidate else None, **control)
        return {**result, "ready": result["cpu_usable"], "path": result.get("site", "")}
    if not candidate.is_file():
        return {}
    result = _python_check(root, candidate, kind, **control)
    return {**result, "ready": result["cpu_usable"], "path": str(candidate),
            "files": [str(candidate), *result.get("files", [])]}


def _candidates(root, kind, configured):
    if kind == "ffmpeg":
        return _ffmpeg_dirs(root, configured)
    if kind == "local-asr":
        return _candidate_sites(root, configured)
    if kind == "qwen":
        return _qwen_candidates(root, configured)
    return _cuda_candidates(root, configured)


def _check(root, kind, candidate, **control):
    if kind == "ffmpeg":
        return _ffmpeg(root, candidate, **control)
    if kind == "whisper-cuda":
        return _cuda(root, candidate, **control)
    return _probe_python(root, kind, candidate, **control)


def _result(kind, source, candidate, result):
    if not result.get("ready"):
        return {**_empty(kind), "error": result.get("error", "runtime_missing")}
    path = result.get("path", str(candidate))
    return {**result, "kind": kind, "source": source, "path": path, "detail": "已通过本机检测"}


def verify_candidate(root, kind, path, *, cancelled=None):
    """Verify exactly the selected/staged environment without falling through to another install."""
    if kind not in KINDS:
        raise ValueError("未知运行组件")
    path = Path(path).absolute()
    if kind == "qwen":
        path = _interpreter(path)
    if kind == "ffmpeg" and path.is_file():
        path = path.parent
    candidate = [path, path / "bin", path / "bin/x64"] if kind == "whisper-cuda" else path
    return _result(kind, "configured", path, _check(Path(root), kind, candidate, **({"cancelled": cancelled} if cancelled else {})))


def _stamp(path):
    try:
        stat = Path(path).stat()
        return stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size
    except OSError:
        return None


def _context(root, kind):
    primary = {"ffmpeg": "bin", "qwen": "runtime/asr", "local-asr": ".cache/runtime-deps/local-asr",
               "whisper-cuda": ".cache/runtime-deps/whisper-cuda/bin"}
    return (_stamp(root / primary[kind]), _configured(root, kind),
            _configured(root, "qwen") if kind == "whisper-cuda" else None,
            os.environ.get("PATH", ""), os.environ.get("TINGMU_QWEN_PYTHON", ""))


def probe(root: Path, kind: str, configured=None, *, cancelled=None):
    """Perform a fresh, bounded, offline capability check. Invoke on a background worker."""
    root = Path(root).absolute()
    if kind not in KINDS:
        raise ValueError("未知运行组件")
    selected = Path(configured).absolute() if configured else _configured(root, kind)
    status = _empty(kind)
    seen = set()
    control = {"cancelled": cancelled} if cancelled else {}
    for source, candidate in _candidates(root, kind, selected):
        if cancelled and cancelled():
            raise InterruptedError("已停止运行环境检测")
        key = tuple(candidate) if isinstance(candidate, list) else candidate
        if key in seen:
            continue
        seen.add(key)
        result = _check(root, kind, candidate, **control)
        if result.get("ready"):
            status = _result(kind, source, candidate, result)
            break
        if result.get("error"):
            status["error"] = result["error"]
    if kind == "whisper-cuda":
        status["nvidia_present"] = status["ready"] or _python_check(root, sys.executable, "nvidia", **control).get("nvidia_present", False)
    if not configured or selected == _configured(root, kind):
        stamps = {p: _stamp(p) for p in status.get("files", [])}
        _CHECKS[root, kind] = time.monotonic(), _context(root, kind), stamps, status
    return status.copy()


def last_probe(root, kind):
    root = Path(root).absolute()
    previous = _CHECKS.get((root, kind))
    if previous:
        when, context, stamps, status = previous
        if ((status["ready"] or time.monotonic() - when < 15)
                and context == _context(root, kind) and all(_stamp(p) == stamp for p, stamp in stamps.items())):
            return status.copy()
    return None


def cached_probe(root, kind):
    return last_probe(root, kind) or probe(root, kind)


def require(root, kind):
    result = cached_probe(root, kind)
    if not result["ready"]:
        raise RuntimeError(result["detail"])
    return result
