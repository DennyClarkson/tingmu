"""Native capability checks. Executed by absolute filename in the selected interpreter."""

import ctypes
import json
import os
from pathlib import Path
import sys

_DLL_HANDLES = {}


def add_dll_dirs(directories):
    if os.name != "nt":
        return
    paths = []
    for directory in directories:
        path = str(Path(directory).absolute())
        if Path(path).is_dir():
            if path not in _DLL_HANDLES:
                _DLL_HANDLES[path] = os.add_dll_directory(path)
            paths.append(path)
    if paths:
        entries = os.environ.get("PATH", "").split(os.pathsep)
        os.environ["PATH"] = os.pathsep.join(dict.fromkeys([*paths, *entries]))


def activate_site(folder):
    if not folder:
        return
    path = Path(folder)
    if not path.is_dir():
        return
    if str(path) in sys.path:
        sys.path.remove(str(path))
    sys.path.insert(0, str(path))
    add_dll_dirs([path / name for name in ("ctranslate2", "onnxruntime/capi", "av.libs", "numpy.libs")])


def _driver():
    driver = ctypes.CDLL("nvcuda.dll" if os.name == "nt" else "libcuda.so.1")
    count = ctypes.c_int()
    if driver.cuInit(0) or driver.cuDeviceGetCount(ctypes.byref(count)) or count.value < 1:
        raise RuntimeError("no_cuda_device")
    return driver


def _cuda(options, result):
    driver = _driver()
    result["nvidia_present"] = True
    add_dll_dirs(options["directories"])
    for library, create, destroy in zip(options["libraries"], ("cublasCreate_v2", "cudnnCreate"),
                                        ("cublasDestroy_v2", "cudnnDestroy"), strict=True):
        lib = ctypes.CDLL(library)
        handle = ctypes.c_void_p()
        if getattr(lib, create)(ctypes.byref(handle)) or not handle:
            raise RuntimeError("cuda_library_initialization_failed")
        if getattr(lib, destroy)(handle):
            raise RuntimeError("cuda_library_cleanup_failed")
    free, total = ctypes.c_size_t(), ctypes.c_size_t()
    if driver.cuMemGetInfo_v2(ctypes.byref(free), ctypes.byref(total)) == 0:
        result.update(cuda_free_bytes=free.value, cuda_total_bytes=total.value)
    result["cuda_usable"] = True


def _whisper(options, result):
    activate_site(options.get("site"))
    import ctranslate2
    import faster_whisper
    from faster_whisper import WhisperModel
    import onnxruntime
    if not callable(WhisperModel) or "CPUExecutionProvider" not in onnxruntime.get_available_providers():
        raise RuntimeError("cpu_provider_missing")
    files = [module.__file__ for module in (faster_whisper, ctranslate2, onnxruntime)]
    selected = options.get("site")
    if selected and not all(Path(file).resolve().is_relative_to(Path(selected).resolve()) for file in files):
        raise ImportError("incomplete_selected_environment")
    if "int8" not in ctranslate2.get_supported_compute_types("cpu"):
        raise RuntimeError("int8_unavailable")
    result.update(cpu_usable=True, version=ctranslate2.__version__, files=files,
                  site=str(Path(faster_whisper.__file__).parent.parent))
    try:
        result["cuda_float16"] = (ctranslate2.get_cuda_device_count() > 0
                                  and "float16" in ctranslate2.get_supported_compute_types("cuda"))
    except Exception:
        result["cuda_float16"] = False


def _qwen(result):
    import torch
    import qwen_asr
    from qwen_asr import Qwen3ASRModel
    if not callable(Qwen3ASRModel) or torch.ones(1).sum().item() != 1:
        raise RuntimeError("qwen_cpu_unavailable")
    result.update(cpu_usable=True, version=torch.__version__, files=[qwen_asr.__file__, torch.__file__])
    try:
        if not torch.cuda.is_available() or not torch.cuda.is_bf16_supported():
            raise RuntimeError("cuda_bfloat16_unavailable")
        value = torch.ones((2, 2), device="cuda", dtype=torch.bfloat16)
        (value @ value).sum().item()
        torch.cuda.synchronize()
        free, total = torch.cuda.mem_get_info()
        result.update(cuda_usable=True, cuda_free_bytes=free, cuda_total_bytes=total,
                      gpu_name=torch.cuda.get_device_name())
    except Exception as exc:
        result["cuda_error"] = type(exc).__name__


def check(options, kind):
    result = {"cpu_usable": False, "cuda_usable": False, "error": ""}
    try:
        if kind in {"whisper", "local-asr"}:
            _whisper(options, result)
        elif kind == "whisper-cuda":
            _cuda(options, result)
        elif kind == "nvidia":
            _driver()
            result["nvidia_present"] = True
        elif kind == "qwen":
            _qwen(result)
    except Exception as exc:
        result["error"] = type(exc).__name__
    return result


def install_probe_guard(cache):
    """Disable Python networking and confine optional-package writes during discovery."""
    attempts = {"network_attempts": 0}
    writes = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND

    def writable(value):
        if isinstance(value, (str, bytes, os.PathLike)):
            name = os.fsdecode(value)
            if name.lower() != os.devnull.lower() and not Path(name).resolve().is_relative_to(cache):
                raise PermissionError("runtime_probe_read_only")

    def audit(event, args):
        if event in {"socket.connect", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo",
                     "socket.gethostbyname", "socket.gethostbyaddr", "socket.getnameinfo"}:
            attempts["network_attempts"] += 1
            raise PermissionError("runtime_probe_offline")
        if event == "open" and args[2] & writes:
            writable(args[0])
        elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.utime", "os.truncate"}:
            writable(args[0])
        elif event in {"os.rename", "os.link"}:
            writable(args[0])
            writable(args[1])
        elif event == "os.symlink":
            writable(args[1])

    sys.addaudithook(audit)
    return attempts


if __name__ == "__main__":
    options = json.loads(sys.argv[1])
    attempts = install_probe_guard(Path(options["cache"]).parent.resolve())
    import site
    site.main()
    print(json.dumps({**check(options, sys.argv[2]), **attempts}))
