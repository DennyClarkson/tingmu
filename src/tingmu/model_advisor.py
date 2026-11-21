"""Offline model planning against the exact interpreters used by ASR workers.

Resource budgets are conservative planning heuristics, not benchmark promises.
No inference weights are loaded, no service is contacted, and no download starts.
Run probe_hardware/recommend_models in a background worker (native probes time out).
"""
from __future__ import annotations

import ctypes
from functools import lru_cache
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from .media import _normalise_language, _qwen_python
from .runtime import cache_root, portable_root
from .workspace_models import QWEN_MODELS, model_inventory

GIB = 1024 ** 3
QWEN_LANGUAGES = frozenset("zh yue en fr de it ja ko pt ru es".split())
WHISPER_LANGUAGES = frozenset((
    "en zh de es ru ko fr ja pt tr pl ca nl ar sv it id hi fi vi he uk el ms cs ro da hu ta no th ur hr bg lt "
    "la mi ml cy sk te fa lv bn sr az sl kn et mk br eu is hy ne mn bs kk sq sw gl mr pa si km sn yo so af "
    "oc ka be tg sd gu am yi lo uz fo ht ps tk nn mt sa lb my bo tl mg as tt haw ln ha ba jw su yue"
).split())
# (label, CPU available RAM, CUDA available RAM, CUDA VRAM) in GiB.
MODEL_BUDGETS = {
    "small": ("Whisper small", 3, 2, 2),
    "large-v3-turbo": ("Whisper large-v3-turbo", 5, 3, 4),
    "large-v3": ("Whisper large-v3", 8, 4, 6),
    "qwen3-asr-0.6b": ("Qwen3-ASR 0.6B + 对齐器", 8, 4, 5),
    "qwen3-asr-1.7b": ("Qwen3-ASR 1.7B + 对齐器", 14, 6, 8),
}

# A standalone script deliberately imports packages from the worker interpreter,
# never from a different activated environment or the host's nvidia-smi verdict.
_PROBE_SCRIPT = r'''
import ctypes, json, os, pathlib, site, sys
result = {"cpu_usable": False, "cuda_usable": False, "error": "", "cuda_error": ""}
try:
    if sys.argv[1] == "whisper":
        handles = []
        if os.name == "nt":
            paths = [p for root in site.getsitepackages()
                     for p in (pathlib.Path(root) / "nvidia").glob("*/bin") if p.is_dir()]
            handles = [os.add_dll_directory(str(p)) for p in paths]
            os.environ["PATH"] = os.pathsep.join(map(str, paths)) + os.pathsep + os.environ.get("PATH", "")
        import ctranslate2
        from faster_whisper import WhisperModel
        result["version"] = ctranslate2.__version__
        result["cpu_usable"] = "int8" in ctranslate2.get_supported_compute_types("cpu")
        try:
            if ctranslate2.get_cuda_device_count() < 1:
                raise RuntimeError("no_cuda_device")
            if "float16" not in ctranslate2.get_supported_compute_types("cuda"):
                raise RuntimeError("float16_unavailable")
            # Opening real library handles catches the common portable-build
            # case: a GPU exists, but cuBLAS/cuDNN were not shipped.
            for library, create, destroy in (
                ("cublas64_12.dll" if os.name == "nt" else "libcublas.so.12", "cublasCreate_v2", "cublasDestroy_v2"),
                ("cudnn64_9.dll" if os.name == "nt" else "libcudnn.so.9", "cudnnCreate", "cudnnDestroy")):
                lib = ctypes.CDLL(library)
                handle = ctypes.c_void_p()
                if getattr(lib, create)(ctypes.byref(handle)) != 0:
                    raise RuntimeError("cuda_library_initialization_failed")
                getattr(lib, destroy)(handle)
            driver = ctypes.CDLL("nvcuda.dll" if os.name == "nt" else "libcuda.so.1")
            free, total = ctypes.c_size_t(), ctypes.c_size_t()
            if driver.cuMemGetInfo_v2(ctypes.byref(free), ctypes.byref(total)) == 0:
                result.update(cuda_free_bytes=free.value, cuda_total_bytes=total.value)
            result["cuda_usable"] = True
        except Exception as exc:
            result["cuda_error"] = type(exc).__name__
    else:
        import torch
        from qwen_asr import Qwen3ASRModel
        result["version"] = torch.__version__
        result["cpu_usable"] = bool(torch.ones(1).sum().item() == 1)
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
except Exception as exc:
    result["error"] = type(exc).__name__
print(json.dumps(result))
'''


def _probe_runtime(executable, engine):
    result = {"interpreter": str(executable), "cpu_usable": False, "cuda_usable": False,
              "error": "runtime_missing"}
    if not Path(executable).is_file():
        return result
    try:
        completed = subprocess.run([str(executable), "-c", _PROBE_SCRIPT, engine],
                                   capture_output=True, text=True, encoding="utf-8", errors="replace",
                                   env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                                        "HF_HUB_DISABLE_TELEMETRY": "1"},
                                   timeout=35, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if completed.returncode == 0:
            result.update(json.loads(completed.stdout.strip().splitlines()[-1]))
        else:
            result["error"] = "runtime_probe_failed"
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        result["error"] = "runtime_probe_failed_or_timed_out"
    return result


def _memory():
    try:
        if os.name == "nt":
            class MemoryStatus(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in ("total", "available", "page_total", "page_free",
                                                           "virtual_total", "virtual_free", "extended")]
            status = MemoryStatus()
            status.length = ctypes.sizeof(status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.total, status.available
        else:
            rows = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
            return int(rows["MemTotal"].split()[0]) * 1024, int(rows["MemAvailable"].split()[0]) * 1024
    except (OSError, ValueError, KeyError, AttributeError):
        pass
    return None, None


def probe_hardware():
    """Probe app-local inference packages; CPU/GPU failures remain explicit."""
    total, available = _memory()
    cache = cache_root()
    existing = cache
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    try:
        disk_free = shutil.disk_usage(existing).free
    except OSError:
        disk_free = None
    whisper = _probe_runtime(sys.executable, "whisper")
    try:
        qwen = _probe_runtime(_qwen_python(), "qwen")
    except (OSError, ValueError, RuntimeError):
        qwen = {"cpu_usable": False, "cuda_usable": False, "error": "runtime_missing"}
    gpus = []
    # Inventory is separate from runtime eligibility: nvidia-smi reporting a GPU
    # does not establish that either of our packaged inference runtimes can use it.
    executable = shutil.which("nvidia-smi")
    if executable:
        try:
            result = subprocess.run([executable, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                                    capture_output=True, text=True, timeout=5,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    name, total_mb, free_mb = line.rsplit(",", 2)
                    gpus.append({"name": name.strip(), "total_bytes": int(total_mb) * 1024 ** 2,
                                 "free_bytes": int(free_mb) * 1024 ** 2})
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    return {"cpu_name": platform.processor() or platform.machine(), "cpu_threads": os.cpu_count() or 1,
            "ram_total_bytes": total, "ram_available_bytes": available, "disk_free_bytes": disk_free,
            "cache_path": str(cache), "portable": portable_root() is not None,
            "gpus": gpus, "gpu_inventory_note": "仅列出可检测的 NVIDIA GPU；是否可用以识别运行时探测为准",
            "runtimes": {"whisper": whisper, "qwen": qwen}}


@lru_cache(maxsize=32)
def _tokenizer_languages(path, modified_ns, size):
    # File identity and revision bound reuse when a local tokenizer is replaced.
    tokenizer = json.loads(path.read_bytes())
    tokens = {row.get("content") for row in tokenizer.get("added_tokens", []) if isinstance(row, dict)}
    vocab = tokenizer.get("model", {}).get("vocab", {})
    if isinstance(vocab, dict):
        tokens.update(vocab)
    return frozenset(code for code in WHISPER_LANGUAGES if f"<|{code}|>" in tokens)


def _local_languages(model):
    from .local_whisper import resolve_local_model
    try:
        tokenizer = resolve_local_model(model) / "tokenizer.json"
        stat = tokenizer.stat()
        return _tokenizer_languages(tokenizer, stat.st_mtime_ns, stat.st_size)
    except (OSError, ValueError, TypeError, AttributeError):
        return set()


def model_supports_language(model, language):
    """Check the selected recognizer's language coverage without probing hardware."""
    language = _normalise_language(language or "auto")
    if model.startswith("local:"):
        supported = _local_languages(model)
    elif model in QWEN_MODELS:
        return language in QWEN_LANGUAGES
    elif model in MODEL_BUDGETS:
        supported = WHISPER_LANGUAGES
    else:
        return False
    if not language:
        return bool(supported)
    return language in supported and (model != "small" or language != "yue")


def _alternatives(settings, offline):
    rows = [{"backend": "subtitles", "name": "导入已有字幕", "available": True,
             "verified": True, "reason": "无需下载模型，可离线学习"}]
    backend = getattr(settings, "asr_backend", "local")
    if backend in {"local_api", "external"}:
        from .external_asr import validate_speech_setting
        local = backend == "local_api"
        endpoint = getattr(settings, "local_asr_endpoint" if local else "asr_endpoint", "")
        model = getattr(settings, "local_asr_api_model" if local else "external_asr_model", "")
        valid = bool(model)
        try:
            validate_speech_setting("local_asr_endpoint" if local else "asr_endpoint", endpoint)
        except (ValueError, TypeError):
            valid = False
        allowed = local or (not offline and bool(getattr(settings, "allow_audio_upload", False)))
        rows.append({"backend": backend, "name": "已配置的本机 API" if local else "已配置的远程 API",
                     "model": model, "available": valid and allowed, "verified": False,
                     "reason": "尚未连接验证；服务、模型支持语言与凭据需另行测试" if allowed else
                               "离线模式或尚未授权发送音轨，不能使用远程识别"})
    return rows


def recommend_models(settings=None, *, language=None, preference="balanced", offline=False, hardware=None):
    """Return JSON-safe suggestions only. `available` means eligible for planning;
    `ready` additionally requires complete weights. Native probes do not certify
    transcription quality. Caller owns explicit download consent and execution.
    """
    if preference not in {"balanced", "accuracy", "speed"}:
        raise ValueError("识别偏好须为 balanced、accuracy 或 speed")
    language = _normalise_language(language if language is not None else getattr(settings, "target_language", "yue"))
    hardware = probe_hardware() if hardware is None else hardware
    models = list(MODEL_BUDGETS)
    selected = getattr(settings, "asr_model", "")
    if selected.startswith("local:"):
        models.append(selected)
    candidates = []
    for model in models:
        inventory = model_inventory(model)
        qwen = model in QWEN_MODELS
        engine = "qwen" if qwen else "whisper"
        runtime = hardware.get("runtimes", {}).get(engine, {})
        custom = model.startswith("local:")
        name, cpu_ram, gpu_ram, vram = MODEL_BUDGETS.get(model, ("已选本地 Whisper 模型", 8, 4, 6))
        language_ok = model_supports_language(model, language)
        ram = hardware.get("ram_available_bytes")
        gpu_free = runtime.get("cuda_free_bytes")
        gpu_ok = runtime.get("cuda_usable", False) and gpu_free is not None and gpu_free >= vram * GIB
        device = "cuda" if gpu_ok else "cpu"
        reasons, warnings = [], []
        if not language_ok:
            reasons.append("当前字幕后端不支持此语言" if language else "Qwen 自动检测可能得到对齐器不支持的语言，请先选择语种")
        if not runtime.get("cpu_usable") and not gpu_ok:
            reasons.append("当前应用识别运行时不可用")
        required_ram = gpu_ram if gpu_ok else cpu_ram
        if ram is None:
            reasons.append("未能读取可用内存，无法确认资源是否足够")
        elif ram < required_ram * GIB:
            reasons.append(f"可用内存不足，建议至少 {required_ram} GiB")
        if not inventory["installed"]:
            if custom:
                reasons.append("所选共享目录模型文件不完整")
            elif offline:
                reasons.append("离线模式下缺少完整模型")
            disk = hardware.get("disk_free_bytes")
            # Windows Hub caches may copy snapshots when symlinks are disabled.
            needed = inventory["download_bytes"] * 2 + GIB
            if disk is None:
                reasons.append("未能读取缓存盘剩余空间")
            elif disk < needed:
                reasons.append("共享缓存盘空间不足，需预留下载和临时文件空间")
        if device == "cpu":
            warnings.append("使用 CPU；长视频识别可能较慢")
        if custom:
            warnings.append("自选模型架构与内存需求未实测，按较大模型预算")
        if inventory["cache_state"] == "incomplete":
            warnings.append("缓存不完整；下载量按缺失仓库完整大小保守估算")
        candidate = {**inventory, "name": name, "engine": engine, "device": device,
                     "available": not reasons, "ready": not reasons and inventory["installed"],
                     "supported_language": language_ok, "reasons": reasons, "warnings": warnings,
                     "memory_budget_bytes": required_ram * GIB, "vram_budget_bytes": vram * GIB}
        candidates.append(candidate)
    ordering = {
        "speed": ["small", "large-v3-turbo", "qwen3-asr-0.6b", "large-v3", "qwen3-asr-1.7b"],
        "balanced": ["large-v3-turbo", "qwen3-asr-0.6b", "small", "large-v3", "qwen3-asr-1.7b"],
        "accuracy": ["qwen3-asr-1.7b", "large-v3", "qwen3-asr-0.6b", "large-v3-turbo", "small"],
    }[preference]
    eligible = [row for row in candidates if row["available"]]
    # Balanced setup avoids downloads. Explicit speed/accuracy priorities rank
    # eligible engines independently of what a different workspace has installed.
    eligible.sort(key=lambda row: ((not row["installed"]) if preference == "balanced" else False,
                                   (row["device"] != "cuda") if preference == "speed" else False,
                                   ordering.index(row["model"]) if row["model"] in ordering else -1))
    if eligible:
        pick = eligible[0]
        if pick["device"] == "cuda":
            free = hardware["runtimes"][pick["engine"]]["cuda_free_bytes"] / GIB
            capacity = f"识别运行时已检测到 CUDA，可用显存 {free:.1f} GiB"
        else:
            capacity = f"可用内存 {hardware['ram_available_bytes'] / GIB:.1f} GiB，满足此模型的 CPU 预算"
        priority = {"balanced": "综合考虑现有权重和模型规模", "speed": "按设备和识别速度优先选择",
                    "accuracy": "优先选用资源允许的高容量模型"}[preference]
        pick["rationale"] = capacity + "；" + priority + ("，共享模型已就绪" if pick["installed"] else "，需下载权重")
    return {"hardware": hardware, "language": language or "auto", "preference": preference,
            "offline": bool(offline), "recommendation": eligible[0] if eligible else None,
            "candidates": candidates, "alternatives": _alternatives(settings, offline),
            "notes": ["所有工作区共用当前安装目录中的模型；不会自动下载或联系 API。",
                      "容量与内存预算是估算；完整缓存仅检查必要文件，不代表通过识别质量验收。",
                      "偏好排序是资源与模型规模启发式，准确率需用实际音频验证。"]}
