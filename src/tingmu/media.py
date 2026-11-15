"""Local media/subtitle I/O and isolated faster-whisper inference.

ASR runs in a subprocess: cancellation can stop a model download or native
inference, and a failed CUDA DLL cannot bring down the desktop application.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from typing import Callable

from .models import Cue

MEDIA_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".wmv", ".flv",
    ".ts", ".mts", ".m2ts", ".mpg", ".mpeg", ".3gp", ".ogv",
    ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma",
})
SUBTITLE_EXTENSIONS = frozenset({".srt", ".vtt", ".ass", ".ssa"})
_DLL_HANDLES: list = []
QWEN_MODELS = {
    "qwen3-asr-1.7b": "Qwen/Qwen3-ASR-1.7B",
    "qwen3-asr-0.6b": "Qwen/Qwen3-ASR-0.6B",
}
QWEN_CANTONESE_CONTEXT = "請以繁體中文記錄香港粵語對白，保留原聲字詞。"


def _qwen_model_id(model: str) -> str | None:
    return QWEN_MODELS.get(model.lower().removeprefix("qwen/"))


def _qwen_python() -> Path:
    override = os.environ.get("TINGMU_QWEN_PYTHON")
    path = Path(override) if override else _cache_root() / "qwen-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not path.is_file():
        raise MediaError("Qwen3-ASR 本地环境未安装。请运行 scripts/setup_qwen.ps1，或设置 TINGMU_QWEN_PYTHON 指向已安装 qwen-asr 的 Python。")
    return path.resolve()


def _qwen_snapshot(repo: str, cache: str) -> str:
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError
    def complete(path: str) -> bool:
        folder = Path(path)
        index = folder / "model.safetensors.index.json"
        weights = set(json.loads(index.read_text(encoding="utf-8"))["weight_map"].values()) if index.is_file() else {"model.safetensors"}
        tokenizer_files = {"tokenizer.json"} if (folder / "tokenizer.json").is_file() else {"vocab.json", "merges.txt"}
        essential = {"config.json", "tokenizer_config.json", "preprocessor_config.json"} | weights | tokenizer_files
        return all((folder / name).is_file() and (folder / name).stat().st_size > 0 for name in essential)
    try:
        cached = snapshot_download(repo, cache_dir=cache, local_files_only=True)
        if complete(cached):
            return cached
    except LocalEntryNotFoundError:
        pass
    downloaded = snapshot_download(repo, cache_dir=cache,
                                   allow_patterns=["*.json", "*.safetensors", "*.txt", "*.tiktoken"])
    if not complete(downloaded):
        raise MediaError(f"模型缓存不完整：{repo}；请重新运行下载命令。")
    return downloaded


class MediaError(RuntimeError):
    """An actionable media or ASR failure safe to display in the UI."""


class TranscriptionCancelled(InterruptedError):
    pass


def _check_cancelled(cancelled=None):
    if cancelled and cancelled():
        raise TranscriptionCancelled("已取消本地转写")


def scan_media(folder: Path) -> list[Path]:
    """Recursively scan known media extensions without following directory links."""
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise MediaError(f"媒体目录不存在：{folder}")
    paths = []
    for root, dirs, files in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in {".git", ".venv", ".cache", "__pycache__"})
        for name in files:
            path = Path(root) / name
            if path.suffix.lower() in MEDIA_EXTENSIONS and path.is_file():
                paths.append(path.resolve())
    return sorted(set(paths), key=lambda p: str(p).casefold())


def _executable(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise MediaError(f"找不到 {name}。请安装 FFmpeg，并将其 bin 目录加入 PATH 后重启听幕。")
    return binary


def _run(command: list[str], cancelled=None, timeout=None) -> str:
    """Poll a process without filling its output pipes; terminate on cancellation."""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(command, stdout=out, stderr=err,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        import time
        began = time.monotonic()
        try:
            while True:
                _check_cancelled(cancelled)
                try:
                    proc.wait(timeout=0.15)
                    break
                except subprocess.TimeoutExpired:
                    if timeout and time.monotonic() - began > timeout:
                        raise MediaError(f"{Path(command[0]).stem} 运行超时")
            out.seek(0)
            err.seek(0)
            if proc.returncode:
                detail = err.read().decode("utf-8", errors="replace")[-3000:]
                raise MediaError(f"{Path(command[0]).stem} 失败：{detail.strip()}")
            return out.read().decode("utf-8", errors="replace")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()


def probe_media(path: Path) -> dict:
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise MediaError(f"媒体文件不存在：{path}")
    try:
        data = json.loads(_run([_executable("ffprobe"), "-v", "error", "-show_format",
                              "-show_streams", "-of", "json", str(path)], timeout=45))
    except json.JSONDecodeError as exc:
        raise MediaError("ffprobe 返回了无效的媒体信息") from exc
    # Keep the native ffprobe streams plus convenient audio ordinals for callers.
    data["audio_tracks"] = [dict(stream, ordinal=ordinal) for ordinal, stream in enumerate(
        s for s in data.get("streams", []) if s.get("codec_type") == "audio")]
    return data


def _cue_id(source: str, index: int, start: float, end: float) -> str:
    value = f"{source}|{index}|{start:.3f}|{end:.3f}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:20]


def load_subtitles(path: Path) -> list[Cue]:
    """Import SRT/VTT/ASS/SSA, retaining each original subtitle event's text."""
    import pysubs2
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() not in SUBTITLE_EXTENSIONS:
        raise MediaError("请导入文本字幕 SRT、VTT、ASS 或 SSA；图片字幕需要先做 OCR。")
    raw = path.read_bytes()
    encodings = ["utf-16"] if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else ["utf-8-sig", "gb18030", "big5"]
    subtitle_text = None
    for encoding in encodings:
        try:
            subtitle_text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if subtitle_text is None:
        raise MediaError("无法读取字幕编码，请另存为 UTF-8 后再导入。")
    try:
        events = pysubs2.SSAFile.from_string(subtitle_text, format_=path.suffix.lower()[1:])
    except Exception as exc:
        raise MediaError(f"字幕格式无效：{exc}") from exc
    cues = []
    for i, event in enumerate(events):
        if event.is_comment or not event.plaintext.strip():
            continue
        start, end = max(0, event.start) / 1000, max(0, event.end) / 1000
        if end <= start:
            continue
        cues.append(Cue(id=_cue_id(str(path), i, start, end), start=start, end=end,
                        text=event.plaintext.replace("\r\n", "\n").replace("\r", "\n"), original_text=event.text))
    return sorted(cues, key=lambda cue: (cue.start, cue.end))


def save_subtitles(cues: list[Cue], path: Path) -> Path:
    """Export current display text to SRT/VTT/ASS/SSA, preserving cue times."""
    import pysubs2
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() not in SUBTITLE_EXTENSIONS:
        raise MediaError("导出文件扩展名须为 .srt、.vtt、.ass 或 .ssa")
    subs = pysubs2.SSAFile()
    for cue in cues:
        if not math.isfinite(cue.start) or not math.isfinite(cue.end) or cue.start < 0 or cue.end <= cue.start:
            raise MediaError(f"字幕时间无效：{cue.id}")
        event = pysubs2.SSAEvent(start=round(cue.start * 1000), end=round(cue.end * 1000))
        event.plaintext = cue.text
        subs.append(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write beside destination then replace, so failed exports do not truncate it.
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=path.suffix, delete=False) as tmp:
        temp_path = Path(tmp.name)
    try:
        subs.save(str(temp_path), encoding="utf-8", format_=path.suffix.lower()[1:])
        temp_path.replace(path)
    finally:
        temp_path.unlink(missing_ok=True)
    return path


def save_srt(cues: list[Cue], path: Path):
    path = Path(path)
    if path.suffix.lower() != ".srt":
        raise MediaError("SRT 导出路径须以 .srt 结尾")
    save_subtitles(cues, path)


def _validate_slice(audio_track: int, start: float, duration: float | None):
    if not isinstance(audio_track, int) or audio_track < 0:
        raise ValueError("音轨序号必须是从 0 开始的非负整数")
    if not math.isfinite(start) or start < 0:
        raise ValueError("开始时间必须是非负秒数")
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise ValueError("片段时长必须大于 0 秒")


def extract_audio(media_path: Path, output: Path, audio_track: int = 0,
                  start: float = 0, duration: float | None = None, *, cancelled=None) -> Path:
    """Extract an audio *ordinal* to mono PCM 16kHz WAV, without altering input."""
    _validate_slice(audio_track, start, duration)
    media_path, output = Path(media_path).resolve(), Path(output).resolve()
    if media_path == output:
        raise MediaError("提取音频的输出路径不能覆盖原媒体")
    if not media_path.is_file():
        raise MediaError(f"媒体文件不存在：{media_path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [_executable("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    if start:
        command += ["-ss", str(start)]
    command += ["-i", str(media_path), "-map", f"0:a:{audio_track}", "-vn"]
    if duration is not None:
        command += ["-t", str(duration)]
    command += ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", str(output)]
    try:
        _run(command, cancelled=cancelled)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    if not output.exists() or output.stat().st_size <= 78:
        raise MediaError("没有可识别的音频，请检查音轨和片段时间。")
    return output


def _cache_root() -> Path:
    configured = os.environ.get("TINGMU_CACHE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    project = Path(__file__).resolve().parents[2]
    if (project / "pyproject.toml").is_file():
        return project / ".cache"
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache")) / "Tingmu" / "cache"


def _register_cuda_dlls():
    """Windows NVIDIA wheels are not automatically visible to LoadLibrary."""
    if os.name != "nt":
        return
    import site
    directories = []
    for root in site.getsitepackages():
        nvidia = Path(root) / "nvidia"
        if nvidia.is_dir():
            directories.extend(p for p in nvidia.glob("*/bin") if p.is_dir())
    for path in directories:
        _DLL_HANDLES.append(os.add_dll_directory(str(path)))
    if directories:
        os.environ["PATH"] = os.pathsep.join(map(str, directories)) + os.pathsep + os.environ.get("PATH", "")


def _normalise_language(language: str) -> str | None:
    code = language.strip().lower()
    if code in {"auto", "", "automatic"}:
        return None
    return {"zh-cn": "zh", "zh-tw": "zh", "zh-hans": "zh", "zh-hant": "zh",
            "zh-hk": "yue", "cantonese": "yue", "mandarin": "zh"}.get(code, code.split("-")[0])


def _worker_emit(event: dict):
    print(json.dumps(event, ensure_ascii=True), flush=True)


def _read_pcm_audio(path: str):
    # ffmpeg already decoded/resampled this WAV. Avoid a second PyAV decode,
    # including its version-dependent av.open metadata_errors API.
    import wave
    import numpy as np
    with wave.open(path, "rb") as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (1, 2, 16000):
            raise MediaError("ASR 内部音频须为单声道 16kHz PCM16 WAV")
        return np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32) / 32768.0


def _asr_worker(request_path: Path) -> int:
    """Internal child entry point. Never uploads audio; model downloads only."""
    request = json.loads(request_path.read_text(encoding="utf-8"))
    try:
        if _qwen_model_id(request["model"]):
            return _qwen_worker(request)
        _register_cuda_dlls()
        import ctranslate2
        from faster_whisper import WhisperModel
        device = request["device"]
        if device == "cuda" and ctranslate2.get_cuda_device_count() == 0:
            raise RuntimeError("CUDA device unavailable")
        compute = "float16" if device == "cuda" else "int8"
        _worker_emit({"progress": f"ASR 设备: {device}; compute_type={compute}; 模型: {request['model']}"})
        engine = WhisperModel(request["model"], device=device, compute_type=compute,
                              download_root=request["model_cache"], cpu_threads=min(8, os.cpu_count() or 4))
        language = request["language"]
        if language is not None and language not in engine.supported_languages:
            raise ValueError(f"模型 {request['model']} 不支持语言 {language}。粤语请使用 large-v3 或 large-v3-turbo。")
        segments, info = engine.transcribe(_read_pcm_audio(request["audio"]), language=language, task="transcribe",
                                          beam_size=5, vad_filter=True,
                                          initial_prompt=request.get("initial_prompt"),
                                          condition_on_previous_text=False,
                                          vad_parameters={"min_silence_duration_ms": 500})
        _worker_emit({"progress": f"开始识别 {info.duration:.1f} 秒音频（语言 {info.language}）"})
        cues = []
        for index, segment in enumerate(segments):
            # Do not normalize/translate the model output; preserve raw text separately.
            raw_text = segment.text
            if not raw_text.strip():
                continue
            start = request["start"] + max(0.0, segment.start)
            end = request["start"] + min(info.duration, segment.end)
            if end <= start:
                continue
            uncertain = (segment.avg_logprob < -0.8 or segment.no_speech_prob > 0.5
                         or segment.compression_ratio > 2.4 or end - start > 15)
            cue = Cue(id=_cue_id(request["source"], index, start, end), start=round(start, 3),
                      end=round(end, 3), text=raw_text.strip(), original_text=raw_text, uncertain=uncertain)
            cues.append(dataclasses.asdict(cue))
            _worker_emit({"progress": f"已识别 {segment.end:.1f}/{info.duration:.1f} 秒 · {len(cues)} 句"})
        Path(request["result"]).write_text(json.dumps(cues, ensure_ascii=False), encoding="utf-8")
        _worker_emit({"done": True, "device": device, "compute_type": compute, "cues": len(cues)})
        return 0
    except Exception as exc:
        _worker_emit({"error": str(exc), "error_type": type(exc).__name__})
        return 1


def _alignment_to_cues(text: str, items, *, source: str, offset: float,
                       duration: float, chunk_index: int) -> list[Cue]:
    """Group acoustically aligned units while retaining every raw character.

    No equal-duration interpolation: a missing/mismatched alignment is an error.
    Punctuation stays attached to the exact ASR text, not the aligner's cleaned text.
    """
    if not text.strip():
        return []
    units = list(items)
    if not units:
        raise MediaError("Qwen 对非空转写没有返回时间对齐；未生成猜测时间戳。")
    import unicodedata
    kept = [(i, char) for i, char in enumerate(text)
            if char == "'" or unicodedata.category(char).startswith(("L", "N"))]
    normalized = "".join(char for _, char in kept)
    locations, cursor = [], 0
    for item in units:
        token = "".join(char for char in item.text
                        if char == "'" or unicodedata.category(char).startswith(("L", "N")))
        if not token or not normalized.startswith(token, cursor):
            raise MediaError("Qwen 时间对齐文本与原始转写不一致，无法安全生成字幕。")
        locations.append((kept[cursor][0], kept[cursor + len(token) - 1][0] + 1))
        cursor += len(token)
    if cursor != len(normalized):
        raise MediaError("Qwen 时间对齐遗漏了原始转写字符，无法安全生成字幕。")
    cues, first, raw_start = [], 0, 0
    for i, item in enumerate(units):
        boundary = locations[i + 1][0] if i + 1 < len(units) else len(text)
        punctuation = text[locations[i][1]:boundary]
        span = float(item.end_time) - float(units[first].start_time)
        gap = float(units[i + 1].start_time) - float(item.end_time) if i + 1 < len(units) else 0
        should_break = (i == len(units) - 1 or any(c in punctuation for c in "。！？!?；;")
                        or gap > 0.65 or boundary - raw_start >= 38 or span >= 7)
        if not should_break:
            continue
        start = max(0.0, min(duration, float(units[first].start_time)))
        end = max(0.0, min(duration, float(item.end_time)))
        if end <= start:
            # Let a later real acoustic span absorb a zero-length unit; never invent duration.
            if i < len(units) - 1:
                continue
            if cues:
                cues[-1].text += text[raw_start:boundary]
                cues[-1].original_text += text[raw_start:boundary]
                cues[-1].uncertain = True
                break
            raise MediaError("Qwen 对齐只返回零时长，无法安全生成字幕。")
        raw = text[raw_start:boundary]
        # Qwen does not provide calibrated per-cue confidence through this API.
        # Flag invalid unit durations, long spans and boundary-cut chunks for review.
        bad_alignment = any(float(u.end_time) <= float(u.start_time) for u in units[first:i + 1])
        cues.append(Cue(id=_cue_id(source, chunk_index * 100000 + len(cues), offset + start, offset + end),
                        start=round(offset + start, 3), end=round(offset + end, 3),
                        text=raw.strip(), original_text=raw,
                        uncertain=bad_alignment or end - start > 15 or start < 0.05 or end > duration - 0.05))
        first, raw_start = i + 1, boundary
    return cues


def _qwen_worker(request: dict) -> int:
    import torch
    from qwen_asr import Qwen3ASRModel

    device = request["device"]
    if device == "cuda" and not torch.cuda.is_available():
        raise MediaError("Qwen CUDA 不可用，请确认安装了 CUDA 12.8 版 PyTorch 与兼容驱动。")
    dtype = torch.bfloat16 if device == "cuda" else torch.float32
    target = "cuda:0" if device == "cuda" else "cpu"
    language_names = {
        "zh": "Chinese", "yue": "Cantonese", "en": "English", "fr": "French", "de": "German",
        "it": "Italian", "ja": "Japanese", "ko": "Korean", "pt": "Portuguese", "ru": "Russian",
        "es": "Spanish",
    }
    code = request["language"]
    if code is not None and code not in language_names:
        raise MediaError(f"Qwen 的当前字幕后端需要 ForcedAligner 支持的语言，暂不支持 {code}；可选择 Whisper。")
    language = language_names.get(code)
    context = request.get("initial_prompt")
    if context is None:
        context = QWEN_CANTONESE_CONTEXT if code == "yue" else ""
    repo = _qwen_model_id(request["model"])
    model_cache = request["qwen_model_cache"]
    _worker_emit({"progress": f"Qwen 设备: {device}; dtype={dtype}; 加载 ASR 与 ForcedAligner（首次下载权重）"})
    paths = [_qwen_snapshot(name, model_cache) for name in (repo, "Qwen/Qwen3-ForcedAligner-0.6B")]
    engine = Qwen3ASRModel.from_pretrained(
        paths[0], dtype=dtype, device_map=target, attn_implementation="sdpa",
        forced_aligner=paths[1], forced_aligner_kwargs={"dtype": dtype, "device_map": target,
                                                       "attn_implementation": "sdpa"},
        max_inference_batch_size=1, max_new_tokens=512,
    )
    audio = _read_pcm_audio(request["audio"])
    sample_rate, chunk_samples = 16000, 30 * 16000
    if request.get("duration") is not None:
        audio = audio[:round(request["duration"] * sample_rate)]
    cues, raw_chunks = [], []
    for chunk_index, (begin, finish) in enumerate(_audio_windows(len(audio), chunk_samples)):
        chunk = audio[begin:finish]
        offset, duration = begin / sample_rate, len(chunk) / sample_rate
        _worker_emit({"progress": f"Qwen 正在识别与对齐 {offset:.1f}–{offset + duration:.1f}/{len(audio)/sample_rate:.1f} 秒"})
        with torch.inference_mode():
            result = engine.transcribe(audio=(chunk, sample_rate), language=language,
                                       context=context, return_time_stamps=True)[0]
        alignment_error = None
        try:
            batch = _alignment_to_cues(result.text, result.time_stamps or [], source=request["source"],
                                       offset=request["start"] + offset, duration=duration, chunk_index=chunk_index)
        except MediaError as exc:
            # Keep the actual transcript and alignment in the audit artifact, but
            # never manufacture acoustic times or discard all preceding work.
            alignment_error, batch = str(exc), []
            _worker_emit({"progress": f"Qwen {offset:.1f}–{offset+duration:.1f} 秒无法可靠对齐；保留原文记录，不生成猜测时间字幕。"})
        cues.extend(dataclasses.asdict(cue) for cue in batch)
        raw_chunks.append({"start": request["start"] + offset, "duration": duration,
                           "text": result.text, "language": result.language,
                           "alignment_error": alignment_error,
                           "alignment": [dataclasses.asdict(item) for item in (result.time_stamps or [])]})
        if request.get("raw_output"):
            Path(request["raw_output"]).write_text(json.dumps({"model": repo, "complete": False,
                                                                "chunks": raw_chunks}, ensure_ascii=False, indent=2), encoding="utf-8")
        _worker_emit({"progress": f"Qwen 已完成 {offset + duration:.1f}/{len(audio)/sample_rate:.1f} 秒 · {len(cues)} 句"})
    Path(request["result"]).write_text(json.dumps(cues, ensure_ascii=False), encoding="utf-8")
    # Probe callers can request full raw aligned units for auditable model comparison.
    if request.get("raw_output"):
        from importlib.metadata import version
        runtime = {p: version(p) for p in ("torch", "qwen-asr", "transformers", "accelerate")}
        runtime.update({"device": device, "dtype": str(dtype), "torch_cuda": torch.version.cuda,
                        "context": context, "model_revision": Path(paths[0]).name,
                        "aligner_revision": Path(paths[1]).name})
        if device == "cuda":
            runtime.update({"gpu_name": torch.cuda.get_device_name(),
                            "gpu_capability": torch.cuda.get_device_capability(),
                            "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated()})
        Path(request["raw_output"]).write_text(json.dumps({"model": repo, "complete": True, "runtime": runtime,
                                                            "chunks": raw_chunks}, ensure_ascii=False, indent=2), encoding="utf-8")
    _worker_emit({"done": True, "device": device, "model": repo, "cues": len(cues)})
    return 0


def _audio_windows(sample_count: int, chunk_samples: int) -> list[tuple[int, int]]:
    """Keep every sample, merging tiny decoder/resampler tails into the last window."""
    windows = [(start, min(start + chunk_samples, sample_count))
               for start in range(0, sample_count, chunk_samples)]
    if len(windows) > 1 and windows[-1][1] - windows[-1][0] < 1600:
        windows[-2] = (windows[-2][0], windows[-1][1])
        windows.pop()
    return windows


def _run_worker(request: dict, temp_dir: Path, progress, cancelled) -> tuple[bool, str]:
    request_path = temp_dir / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    environment = os.environ.copy()
    # Supports source checkout and installed package without relying on caller CWD.
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]) + os.pathsep + environment.get("PYTHONPATH", "")
    environment["PYTHONIOENCODING"] = "utf-8"
    events: queue.Queue = queue.Queue()
    error, done = "", False
    with tempfile.TemporaryFile() as stderr:
        executable = _qwen_python() if _qwen_model_id(request["model"]) else Path(sys.executable)
        if executable.stem.lower() == "pythonw":
            executable = executable.with_name("python.exe")
        proc = subprocess.Popen([str(executable), "-m", "tingmu.media", "--asr-worker", str(request_path)],
                                stdout=subprocess.PIPE, stderr=stderr, text=True, encoding="utf-8",
                                env=environment, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        def read_events():
            assert proc.stdout is not None
            for line in proc.stdout:
                events.put(line)
            events.put(None)
        reader = threading.Thread(target=read_events, daemon=True)
        reader.start()
        try:
            while True:
                _check_cancelled(cancelled)
                try:
                    line = events.get(timeout=0.15)
                except queue.Empty:
                    continue
                if line is None:
                    break
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "progress" in event and progress:
                    progress(event["progress"])
                if "error" in event:
                    error = event["error"]
                done = done or event.get("done", False)
            proc.wait()
            stderr.seek(0)
            diagnostic = stderr.read().decode("utf-8", errors="replace")[-2000:]
            if proc.returncode or not done:
                return False, error or diagnostic.strip() or f"本地 ASR 进程退出，代码 {proc.returncode}"
            return True, ""
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            reader.join(timeout=2)
            if proc.stdout:
                proc.stdout.close()


def _transcription_metadata(request: dict) -> dict:
    """Best-effort small provenance; raw transcript/alignment stays in its audit file."""
    qwen_model = _qwen_model_id(request["model"])
    result = {"asr_device": request["device"], "asr_model": qwen_model or request["model"],
              "asr_backend": "qwen3-asr" if qwen_model else "faster-whisper"}
    if not qwen_model:
        result["asr_runtime"] = {"device": request["device"],
                                 "compute_type": "float16" if request["device"] == "cuda" else "int8"}
        return result
    audit_path = request.get("raw_output")
    result["asr_audit_metadata_available"] = False
    if not audit_path:
        return result
    result["asr_audit_path"] = str(audit_path)
    try:
        path = Path(audit_path)
        # Avoid loading arbitrarily large optional audit documents into the UI process.
        if path.stat().st_size > 64 * 1024 * 1024:
            return result
        audit = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(audit, dict):
            return result
        result["asr_audit_metadata_available"] = (isinstance(audit.get("runtime"), dict)
                                                  or isinstance(audit.get("chunks"), list))
        runtime = audit.get("runtime")
        safe_runtime = {"device": request["device"]}
        if isinstance(runtime, dict):
            for key in ("torch", "qwen-asr", "transformers", "accelerate", "dtype", "torch_cuda",
                        "context", "model_revision", "aligner_revision", "gpu_name", "peak_cuda_allocated_bytes_scope"):
                value = runtime.get(key)
                if isinstance(value, str):
                    safe_runtime[key] = value[:256]
            capability = runtime.get("gpu_capability")
            if (isinstance(capability, (list, tuple)) and len(capability) == 2
                    and all(type(value) is int and 0 <= value < 1000 for value in capability)):
                safe_runtime["gpu_capability"] = list(capability)
            allocated = runtime.get("peak_cuda_allocated_bytes")
            if type(allocated) is int and 0 <= allocated < 10**15:
                safe_runtime["peak_cuda_allocated_bytes"] = allocated
        result["asr_runtime"] = safe_runtime
        chunks = audit.get("chunks")
        if isinstance(chunks, list):
            warnings, warning_count = [], 0
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    continue
                error = chunk.get("alignment_error")
                if not isinstance(error, str) or not error:
                    continue
                start, duration = chunk.get("start"), chunk.get("duration")
                if (type(start) not in (int, float) or type(duration) not in (int, float)
                        or not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration <= 0):
                    continue
                warning_count += 1
                if len(warnings) < 100:
                    warnings.append({"start": start, "duration": duration, "error": error[:256]})
            result.update(alignment_warnings=warnings, alignment_warning_count=warning_count,
                          alignment_warnings_truncated=warning_count > len(warnings))
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        # Metadata is optional: successful, validated cues must remain usable.
        pass
    return result


def transcribe(media_path: Path, language: str = "yue", model: str = "large-v3-turbo",
               device: str = "auto", audio_track: int = 0, start: float = 0,
               duration: float | None = None, progress: Callable[[str], None] | None = None,
               cancelled: Callable[[], bool] | None = None, *, initial_prompt: str | None = None,
               raw_output: Path | None = None, metadata_out: dict | None = None) -> list[Cue]:
    """Return local ASR cues on the original media timeline.

    ``auto`` tries CUDA, then safely restarts inference on CPU if CUDA fails.
    Explicit ``cuda`` fails clearly rather than silently running on CPU.
    Progress/cancellation callbacks run in the calling thread.
    On success, ``metadata_out`` receives small provenance/audit metadata without
    raw chunks or transcript text. Unavailable optional metadata never invalidates cues.
    """
    _validate_slice(audio_track, start, duration)
    if device not in {"auto", "cuda", "cpu"}:
        raise ValueError("设备须为 auto、cuda 或 cpu")
    _check_cancelled(cancelled)
    if _qwen_model_id(model):
        _qwen_python()  # Fail before extracting the media if optional environment is absent.
    cache = _cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    if _qwen_model_id(model) and raw_output is None:
        import uuid
        audit_dir = cache / "qwen-audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        raw_output = audit_dir / f"{uuid.uuid4().hex}.json"
    if raw_output is not None:
        Path(raw_output).resolve().parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="asr-", dir=cache) as temporary:
        folder = Path(temporary)
        if progress:
            progress(f"正在提取音轨 {audio_track + 1}（本地 16kHz 音频）")
        audio = extract_audio(media_path, folder / "audio.wav", audio_track, start, duration, cancelled=cancelled)
        request = {"audio": str(audio), "model": model, "language": _normalise_language(language),
                   "model_cache": str(cache / "models"), "start": start, "result": str(folder / "result.json"),
                   "duration": duration,
                   "initial_prompt": initial_prompt,
                   "qwen_model_cache": str(cache / "qwen-models"),
                   "raw_output": str(Path(raw_output).resolve()) if raw_output else None,
                   "source": f"{Path(media_path).resolve()}|{audio_track}|{model}|{language}"}
        errors = []
        for attempt in (["cuda", "cpu"] if device == "auto" else [device]):
            _check_cancelled(cancelled)
            if progress:
                progress("正在加载本地语音模型；首次使用需下载模型权重，音视频不会上传。")
            request["device"] = attempt
            success, error = _run_worker(request, folder, progress, cancelled)
            if success:
                _check_cancelled(cancelled)
                data = json.loads(Path(request["result"]).read_text(encoding="utf-8"))
                cues = [Cue(**item) for item in data]
                if isinstance(metadata_out, dict):
                    metadata = _transcription_metadata(request)
                    # A reused output dict must not retain a prior run's Qwen warnings.
                    for key in ("asr_device", "asr_model", "asr_backend", "asr_runtime", "asr_audit_path",
                                "asr_audit_metadata_available", "alignment_warnings", "alignment_warning_count",
                                "alignment_warnings_truncated"):
                        metadata_out.pop(key, None)
                    metadata_out.update(metadata)
                return cues
            errors.append(f"{attempt}: {error}")
            if device == "auto" and attempt == "cuda" and progress:
                progress(f"CUDA 转写失败，改用 CPU（速度较慢）：{error[:500]}")
        if _qwen_model_id(model):
            raise MediaError("Qwen 本地转写失败。请运行 scripts/setup_qwen.ps1 检查隔离环境，确认 CUDA 12.8 版 PyTorch、"
                             "显存和 ASR/ForcedAligner 权重完整；也可在设置中选择 CPU（较慢）。\n" + "\n".join(errors))
        raise MediaError("本地转写失败。请检查模型下载网络/磁盘空间；CUDA 需要兼容的 NVIDIA 驱动、"
                         "CUDA 12 cuBLAS 与 cuDNN 9 DLL，可在设置中改用 CPU。\n" + "\n".join(errors))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--asr-worker":
        raise SystemExit(_asr_worker(Path(sys.argv[2])))
    raise SystemExit("此模块仅支持内部 ASR worker；使用 tingmu CLI 启动应用。")
