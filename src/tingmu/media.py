"""Local media/subtitle I/O and isolated faster-whisper inference.

ASR runs in a subprocess: cancellation can stop a model download or native
inference, and a failed CUDA DLL cannot bring down the desktop application.
"""
from __future__ import annotations

import dataclasses
from bisect import bisect_right
from contextlib import contextmanager
import hashlib
import html
import json
import math
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
from typing import Callable

from .media_files import MEDIA_EXTENSIONS, SUBTITLE_EXTENSIONS as SUBTITLE_EXTENSIONS
from .models import Cue
from .task_progress import Progress
from .runtime import cache_root as _cache_root, confined_path, installation_root, is_clean_install, portable_root

QWEN_MODELS = {
    "qwen3-asr-1.7b": "Qwen/Qwen3-ASR-1.7B",
    "qwen3-asr-0.6b": "Qwen/Qwen3-ASR-0.6B",
}
QWEN_CANTONESE_CONTEXT = "請以繁體中文記錄香港粵語對白，保留原聲字詞。"


def _qwen_model_id(model: str) -> str | None:
    return QWEN_MODELS.get(model.lower().removeprefix("qwen/"))


def _qwen_python() -> Path:
    from .runtime_deps import require
    try:
        return Path(require(installation_root(), "qwen")["path"])
    except RuntimeError as exc:
        raise AsrSetupError(str(exc)) from exc


def _qwen_snapshot(repo: str, cache: str, *, allow_model_downloads=True) -> str:
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
    if not allow_model_downloads:
        raise MediaError(f"共享目录缺少完整模型 {repo}，请在工作区确认下载后再准备视频。")
    downloaded = snapshot_download(repo, cache_dir=cache,
                                   allow_patterns=["*.json", "*.safetensors", "*.txt", "*.tiktoken"])
    if not complete(downloaded):
        raise MediaError(f"模型缓存不完整：{repo}；请重新运行下载命令。")
    return downloaded


class MediaError(RuntimeError):
    """An actionable media or ASR failure safe to display in the UI."""


class AsrSetupError(MediaError):
    """Local, user-actionable preflight result, safe to show in queue status."""


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
    from .runtime_deps import require
    try:
        return require(installation_root(), "ffmpeg")[name]
    except RuntimeError as exc:
        raise AsrSetupError(str(exc)) from exc


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


def load_subtitles(path: Path, *, learning_language=None, cancelled=None, metadata_out=None) -> list[Cue]:
    """Import SRT/VTT/ASS/SSA, retaining each original subtitle event's text."""
    if learning_language is not None:
        from .subtitle_sources import load_learning_subtitles
        return load_learning_subtitles(path, learning_language, cancelled=cancelled, metadata_out=metadata_out)
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
        # pysubs2 strips <v> in the display text. Read voice markup from the
        # same parsed event sequence, without changing display or timestamps.
        voice_events = (pysubs2.SSAFile.from_string(subtitle_text, format_="vtt", keep_html_tags=True)
                        if path.suffix.lower() == ".vtt" else None)
    except Exception as exc:
        raise MediaError(f"字幕格式无效：{exc}") from exc
    cues = []
    for i, event in enumerate(events):
        if event.is_comment or not event.plaintext.strip():
            continue
        start, end = max(0, event.start) / 1000, max(0, event.end) / 1000
        if end <= start:
            continue
        text = event.plaintext.replace("\r\n", "\n").replace("\r", "\n")
        speaker = event.name.strip()
        raw = event.text
        if voice_events is not None:
            raw = voice_events[i].text
            voice_tags = re.findall(r"<v(?:\.[^\s>]+)*\s+([^>]+)>", raw, flags=re.IGNORECASE)
            ends = re.split(r"</v\s*>", raw, flags=re.IGNORECASE)
            if (len(voice_tags) == 1 and re.match(r"^\s*<v[.\s]", raw, flags=re.IGNORECASE)
                    and (len(ends) == 1 or (len(ends) == 2 and not ends[1].strip()))):
                speaker = html.unescape(voice_tags[0].strip())
            text = html.unescape(text)
        cues.append(Cue(id=_cue_id(str(path), i, start, end), start=start, end=end,
                        text=text, original_text=raw, speaker=speaker))
    return sorted(cues, key=lambda cue: (cue.start, cue.end))


def save_subtitles(cues: list[Cue], path: Path) -> Path:
    """Export current display text to SRT/VTT/ASS/SSA, preserving cue times."""
    import pysubs2
    path = Path(path).expanduser().resolve()
    if path.suffix.lower() not in SUBTITLE_EXTENSIONS:
        raise MediaError("导出文件扩展名须为 .srt、.vtt、.ass 或 .ssa")
    subs = pysubs2.SSAFile()
    suffix = path.suffix.lower()
    def vtt_literal(value):
        # Protect WebVTT markup as well as the SSA escapes used inside pysubs2.
        return html.escape(value, quote=False).translate({123: "&#123;", 125: "&#125;", 92: "&#92;"})
    for cue in cues:
        if not math.isfinite(cue.start) or not math.isfinite(cue.end) or cue.start < 0 or cue.end <= cue.start:
            raise MediaError(f"字幕时间无效：{cue.id}")
        event = pysubs2.SSAEvent(start=round(cue.start * 1000), end=round(cue.end * 1000))
        if suffix in {".vtt", ".ass", ".ssa"} and any(ord(c) < 32 for c in cue.speaker):
            raise MediaError("角色名含换行或控制字符，无法作为字幕角色导出。")
        if suffix in {".ass", ".ssa"}:
            if "," in cue.speaker:
                raise MediaError("ASS/SSA 的角色栏不能包含半角逗号；请使用 WebVTT 导出此角色名。")
            event.name = cue.speaker
        if suffix == ".vtt":
            payload = vtt_literal(cue.text)
            event.plaintext = f"<v {vtt_literal(cue.speaker)}>{payload}</v>" if cue.speaker else payload
        else:
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
    return _extract_pcm(media_path, output, audio_track, start, duration, cancelled,
                        channels=1, rate=16000, fmt="wav")


def _extract_pcm(media_path, output, audio_track, start, duration, cancelled, *, channels, rate, fmt):
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
    command += ["-ac", str(channels), "-ar", str(rate), "-c:a", "pcm_s16le", "-f", fmt, str(output)]
    try:
        _run(command, cancelled=cancelled)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    if not output.exists() or output.stat().st_size <= (78 if fmt == "wav" else 0):
        raise MediaError("没有可识别的音频，请检查音轨和片段时间。")
    return output


@contextmanager
def prepared_audio(media_path: Path, *, audio_track=0, start=0, duration=None, suppress_bgm=True,
                   progress=None, cancelled=None, metadata_out=None, original_out=None):
    """Yield temporary 16 kHz PCM WAV on the selected slice's clock for either ASR backend."""
    cache = _cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="speech-", dir=cache) as tmp:
        folder = Path(tmp)
        report = progress or (lambda msg: None)
        def check():
            _check_cancelled(cancelled)
        check()
        report(Progress(f"正在提取音轨 {audio_track + 1}", activity="提取音轨"))
        method = "off"
        if suppress_bgm:
            from .speech import isolate_vocals, speech_model_status, suppress_repetitive_background
            method = speech_model_status(cache)["bgm"]
        if method == "spleeter-2stems-fp16":
            report(Progress("Spleeter 正在本地分离人声与伴奏", activity="提取立体声音轨"))
            source = _extract_pcm(media_path, folder / "stereo.pcm", audio_track, start, duration,
                                  cancelled, channels=2, rate=44100, fmt="s16le")
            if original_out is not None:
                original = folder / "original.wav"
                _run([_executable("ffmpeg"), "-nostdin", "-loglevel", "error", "-f", "s16le",
                      "-ar", "44100", "-ac", "2", "-i", str(source), "-ac", "1", "-ar", "16000",
                      "-c:a", "pcm_s16le", str(original)], cancelled=cancelled)
                original_out["path"] = original
            vocal = folder / "vocal.pcm"
            isolate_vocals(source, vocal, cache / "speech-models/spleeter", check, progress)
            source.unlink()
            audio = folder / "audio.wav"
            _run([_executable("ffmpeg"), "-nostdin", "-loglevel", "error", "-f", "s16le",
                  "-ar", "44100", "-ac", "1", "-i", str(vocal), "-ar", "16000",
                  "-c:a", "pcm_s16le", str(audio)], cancelled=cancelled)
        else:
            audio = extract_audio(media_path, folder / "audio.wav", audio_track, start, duration,
                                  cancelled=cancelled)
            if original_out is not None:
                original_out["path"] = audio
            if suppress_bgm:
                report(Progress("本机未安装 Spleeter，使用频谱背景抑制", activity="频谱降噪"))
                filtered = folder / "foreground.wav"
                suppress_repetitive_background(audio, filtered, check, progress)
                audio = filtered
        check()
        if metadata_out is not None:
            metadata_out["bgm_method"] = method
        yield audio


def _register_cuda_dlls():
    """Use only the directories that passed native library checks."""
    from .runtime_deps import require
    from .runtime_probe_worker import add_dll_dirs
    add_dll_dirs(require(installation_root(), "whisper-cuda")["directories"])


def _normalise_language(language: str) -> str | None:
    code = language.strip().lower().replace("_", "-")
    if code in {"auto", "", "automatic"}:
        return None
    primary = code.split("-")[0]
    return {"zh-hk": "yue", "cantonese": "yue", "mandarin": "zh"}.get(code, {"jv": "jw"}.get(primary, primary))


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


def _whisper_turn_cues(cue, words, turns, offset, *, pauses=(), trace=None):
    """Split only at boundaries supported by the timed words and the source text."""
    def fallback(reason):
        if trace is not None:
            trace.update(alignment_error=reason)
        return [cue]

    if not words:
        return fallback("missing_word_times")
    words = list(words)
    raw = "".join(w.word for w in words)
    if raw.strip() != cue.original_text.strip():
        return fallback("word_text_mismatch")
    pieces = [w.word for w in words]
    pieces[0] = cue.original_text[:len(cue.original_text) - len(cue.original_text.lstrip())] + pieces[0].lstrip()
    pieces[-1] = pieces[-1].rstrip() + cue.original_text[len(cue.original_text.rstrip()):]
    try:
        times = [(float(w.start), float(w.end)) for w in words]
    except (TypeError, ValueError, OverflowError):
        return fallback("invalid_word_times")
    if any(not math.isfinite(t) for pair in times for t in pair):
        return fallback("invalid_word_times")
    # Overlap between non-degenerate Whisper words makes the boundary ambiguous.
    if any(a < prev_end - .001 and b > a for (_, prev_end), (a, b) in zip(times, times[1:])):
        return fallback("overlapping_word_times")
    from .acoustic import repair_times, sentence_breaks
    ends, separators, cursor = [], [], 0
    for i, piece in enumerate(pieces):
        cursor += len(piece)
        ends.append(cursor)
        tail = re.search(r"[^\w]*$", piece).group()
        if i + 1 < len(pieces) and pieces[i+1][:1].isspace():
            tail += " "
        separators.append(tail)
    lo, hi = cue.start-offset, cue.end-offset
    if any(a < lo-.025 or b > hi+.025 or b < a for a,b in times):
        return fallback("word_times_outside_segment")
    if all(b <= a for a,b in times):
        return fallback("no_positive_word_span")
    relative = [(a-lo, b-lo) for a,b in times]
    quiet = [(a-lo, b-lo) for a,b in pauses if a < hi and b > lo]
    weights = [_acoustic_weight(w.word) for w in words]
    preliminary = sentence_breaks(cue.original_text, ends, separators, relative, [t-lo for t in turns], quiet)
    try:
        relative, affected, repairs = repair_times(relative, weights, hi-lo, quiet, preliminary)
    except ValueError:
        if trace is not None:
            trace.update(alignment_error="word_times_outside_monotonic_anchors")
        return [dataclasses.replace(cue, uncertain=True)]
    cuts = sentence_breaks(cue.original_text, ends, separators, relative, [t-lo for t in turns], quiet)
    # Forced word onsets occasionally interrupt a short vocative or compound.
    # When only one unpunctuated CJK character precedes the internal pause,
    # preserve the short phrase ending at the following comma. Speaker evidence
    # and longer silences still support independent utterances.
    edges = [0, *sorted(cuts)]
    for first, cut, following in zip(edges, edges[1:], edges[2:]):
        prefix = "".join(pieces[first:cut]).strip()
        suffix = "".join(pieces[cut:following]).strip()
        if (len(prefix) == 1 and prefix.isalpha()
                and 2 <= len(suffix) <= 4 and suffix[:-1].isalpha() and suffix[-1] in ",，、：:"
                and relative[cut][0] - relative[cut-1][1] <= 2.2
                and not any(times[cut-1][1] <= t <= times[cut][0] for t in turns)):
            cuts.discard(cut)
    if trace is not None:
        trace.update(timing_repairs=repairs, repair_time_origin=cue.start)
    if len(cuts) == 1:
        start = round(cue.start + relative[0][0], 3)
        end = round(cue.start + relative[-1][1], 3)
        if end <= start:
            return fallback("no_supported_word_span")
        return [dataclasses.replace(cue, start=start, end=end, uncertain=cue.uncertain or bool(affected))]
    parts, first = [], 0
    for last in sorted(cuts):
        text = "".join(pieces[first:last])
        start = round(cue.start + relative[first][0], 3)
        end = round(cue.start + relative[last-1][1], 3)
        if not text.strip() or end <= start:
            return [dataclasses.replace(cue, uncertain=True)]
        parts.append(dataclasses.replace(cue, id=_cue_id(cue.id, first, start, end),
                                         start=start, end=end, text=text.strip(), original_text=text,
                                         uncertain=cue.uncertain or any(i in affected for i in range(first,last))))
        first = last
    return parts


def distribution(name):
    """Look up worker assets only when Whisper needs them."""
    from importlib.metadata import distribution as lookup
    return lookup(name)


def _asr_worker(request_path: Path) -> int:
    """Internal child entry point. Never uploads audio; model downloads only."""
    request = json.loads(request_path.read_text(encoding="utf-8"))
    try:
        if _qwen_model_id(request["model"]):
            return _qwen_worker(request)
        from .runtime_probe_worker import activate_site, add_dll_dirs
        activate_site(request.get("asr_site"))
        if request["device"] == "cuda":
            if "cuda_directories" in request:
                add_dll_dirs(request["cuda_directories"])
            else:
                _register_cuda_dlls()
        import ctranslate2
        from faster_whisper import WhisperModel
        device = request["device"]
        if device == "cuda" and ctranslate2.get_cuda_device_count() == 0:
            raise RuntimeError("CUDA device unavailable")
        compute = "float16" if device == "cuda" else "int8"
        _worker_emit({"progress": f"ASR 设备: {device}; compute_type={compute}; 模型: {request['model']}"})
        engine = WhisperModel(request["model"], device=device, compute_type=compute,
                              local_files_only=not request.get("allow_model_downloads", False),
                              download_root=request["model_cache"], cpu_threads=min(8, os.cpu_count() or 4))
        language = request["language"]
        if language is not None and language not in engine.supported_languages:
            raise ValueError(f"模型 {request['model']} 不支持语言 {language}。粤语请使用 large-v3 或 large-v3-turbo。")
        turns = request.get("speaker_changes", [])
        assets = distribution("faster-whisper").locate_file("faster_whisper/assets")
        vad_filter = any(path.is_file() for path in assets.glob("silero_vad*.onnx"))
        if not vad_filter and is_clean_install():
            from . import whisper_support
            if whisper_support.inventory()["complete"]:
                from faster_whisper import vad
                # This worker handles one track. The upstream VAD loader takes
                # no path argument and reads this directory on first use.
                vad.get_assets_path = lambda: str(whisper_support.SUPPORT.path())
                vad_filter = True
        if not vad_filter:
            _worker_emit({"progress": "未安装 Silero 语音活动检测模型，Whisper 将直接转写完整音轨。"})
        from .acoustic import frame_energy, quiet_intervals
        audio = _read_pcm_audio(request["audio"])
        original = audio
        if request.get("alignment_audio", request["audio"]) != request["audio"]:
            original = _read_pcm_audio(request["alignment_audio"])
        pauses = quiet_intervals(frame_energy(original)) if original is not None and len(original) else []
        del original
        pause_ends = [end for _, end in pauses]
        segments, info = engine.transcribe(audio, language=language, task="transcribe",
                                          beam_size=5, vad_filter=vad_filter,
                                          word_timestamps=True,
                                          initial_prompt=request.get("initial_prompt"),
                                          condition_on_previous_text=False,
                                          vad_parameters={"min_silence_duration_ms": 500})
        _worker_emit({"progress": f"开始识别 {info.duration:.1f} 秒音频（语言 {info.language}）",
                      "metrics": {"activity": "语音识别", "done": 0, "total": float(info.duration), "unit": "秒"}})
        from .whisper_segments import quality_evidence, segment_evidence
        cues, raw_segments = [], []
        previous_text = ""
        origin = request.get("start", 0.)
        for index, segment in enumerate(segments):
            span, evidence = segment_evidence(segment, info.duration, origin)
            if request.get("raw_output"):
                raw_segments.append(evidence)
            if not segment.text.strip() or span is None:
                continue
            lo, hi = span
            start_index = bisect_right(pause_ends, lo)
            stop_index = bisect_right(pause_ends, hi) + 1
            local_pauses = pauses[start_index:stop_index]
            skip, uncertain, previous_text = quality_evidence(evidence, span, local_pauses, previous_text, vad_filter)
            if skip:
                evidence["disposition"] = "no_vad_quiet_low_confidence"
                continue
            start, end = round(origin + lo, 3), round(origin + hi, 3)
            cue = Cue(id=_cue_id(request["source"], index, start, end), start=start, end=end,
                      text=segment.text.strip(), original_text=segment.text, uncertain=bool(uncertain))
            local_turns = turns[bisect_right(turns, lo-.6):bisect_right(turns, hi+.6)]
            parts = _whisper_turn_cues(cue, getattr(segment, "words", None), local_turns, origin,
                                       pauses=local_pauses, trace=evidence)
            if evidence.get("alignment_error") not in (None, "missing_word_times"):
                for part in parts:
                    part.uncertain = True
            cues.extend(dataclasses.asdict(part) for part in parts)
            evidence.update(source_id=cue.id, disposition="published", cue_ids=[part.id for part in parts],
                            cue_timings=[{"id": part.id, "start": part.start, "end": part.end,
                                          "uncertain": part.uncertain} for part in parts])
            _worker_emit({"progress": f"已识别 {hi:.1f}/{info.duration:.1f} 秒 · {len(cues)} 句",
                          "metrics": {"activity": "语音识别", "done": float(hi), "total": float(info.duration), "unit": "秒"}})
        if request.get("raw_output"):
            audit = {"model": request["model"], "complete": True, "segments": raw_segments,
                     "quiet_intervals": [[round(origin+a, 3), round(origin+b, 3)] for a,b in pauses],
                     "word_timestamps": True, "vad_filter": vad_filter,
                     "word_alignment_input": request.get("recognition_input", "off"),
                     "boundary_input": "original_track"}
            Path(request["raw_output"]).write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")
        Path(request["result"]).write_text(json.dumps(cues, ensure_ascii=False), encoding="utf-8")
        _worker_emit({"done": True, "device": device, "compute_type": compute, "vad_filter": vad_filter,
                      "quiet_intervals": [[origin+a, origin+b] for a,b in pauses], "cues": len(cues),
                      "unplaced_segments": sum(s.get("disposition") == "unplaced_segment" for s in raw_segments),
                      "suppressed_segments": sum(s.get("disposition") == "no_vad_quiet_low_confidence"
                                                 for s in raw_segments)})
        return 0
    except Exception as exc:
        _worker_emit({"error": str(exc), "error_type": type(exc).__name__})
        return 1


def _acoustic_weight(token):
    # A Chinese character is usually one syllable. A Latin word, even a long
    # spelling, is a single aligner unit and should not get a per-letter floor.
    return max(1, sum("\u3400" <= c <= "\u9fff" or "\U00020000" <= c <= "\U0002ffff" for c in token))


def _alignment_to_cues(text: str, items, *, source: str, offset: float,
                       duration: float, chunk_index: int, speaker_changes=(), pauses=(), trace=None) -> list[Cue]:
    """Preserve all raw text; estimate collapsed spans only between acoustic anchors."""
    from .acoustic import repair_times, sentence_breaks
    if not text.strip():
        return []
    units = list(items)
    if not units:
        raise MediaError("Qwen 对非空转写没有返回时间对齐；未生成猜测时间戳。")
    from .acoustic import alignment_text_ranges
    try:
        locations = alignment_text_ranges(text, [item.text for item in units])
    except ValueError as exc:
        raise MediaError(str(exc)) from exc
    boundaries = [location[0] for location in locations[1:]] + [len(text)]
    ends = [end for _, end in locations]
    punctuation = [text[end:boundary] for end, boundary in zip(ends, boundaries)]
    times = [(float(u.start_time), float(u.end_time)) for u in units]
    if any(not math.isfinite(t) for pair in times for t in pair):
        raise MediaError("Qwen 返回非有限时间，无法定位字幕。")
    if all(b <= a for a, b in times):
        raise MediaError("Qwen 对齐只返回零时长，缺少可信声学锚点。")
    cuts = sentence_breaks(text, ends, punctuation, times, speaker_changes, pauses)
    try:
        times, affected, repairs = repair_times(times, [_acoustic_weight(u.text) for u in units],
                                                duration, pauses, cuts)
    except ValueError as exc:
        raise MediaError(str(exc)) from exc
    cuts = sentence_breaks(text, ends, punctuation, times, speaker_changes, pauses)
    limited = {i for repair in repairs if repair["method"] in {"anchor_limited", "no_supported_span"}
               for i in range(repair["unit_start"], repair["unit_end"])}
    cues, cue_timings = [], []
    first = raw_start = 0
    for last in sorted(cuts):
        start, end = round(offset + times[first][0], 3), round(offset + times[last-1][1], 3)
        boundary = boundaries[last-1]
        if end <= start:
            # A measured span may follow contiguous collapsed units. Keep the
            # words together when the anchors leave no support for a separate cue.
            if last < len(units) and times[last][0] - times[last-1][1] < .24:
                continue
            if cues and 0 <= start-cues[-1].end < .24:
                cues[-1].original_text += text[raw_start:boundary]
                cues[-1].text = cues[-1].original_text.strip()
                cues[-1].uncertain = True
                cue_timings[-1].update(unit_end=last, timing_basis="anchor_limited")
            else:
                if trace is None:
                    raise MediaError("缺少相邻声学锚点，无法安全定位零时长原文。")
                trace.setdefault("unplaced_unit_ranges", []).append([first, last])
            first, raw_start = last, boundary
            continue
        estimated = any(i in affected for i in range(first,last))
        raw = text[raw_start:boundary]
        cue = Cue(id=_cue_id(source, chunk_index * 100000 + len(cues), start, end),
                  start=start, end=end, text=raw.strip(), original_text=raw,
                  uncertain=estimated or end-start > 15 or start-offset < .05 or end-offset > duration-.05)
        cues.append(cue)
        basis = "bounded_estimate" if estimated else "aligned"
        if any(i in limited for i in range(first,last)):
            basis = "anchor_limited"
        cue_timings.append({"cue_id": cue.id, "unit_start": first, "unit_end": last,
                            "start": start, "end": end, "timing_basis": basis})
        first, raw_start = last, boundary
    if trace is not None:
        trace.update(timing_repairs=repairs, cue_timings=cue_timings)
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
    _worker_emit({"progress": f"Qwen 设备: {device}; dtype={dtype}; 加载 ASR 与 ForcedAligner"})
    paths = [_qwen_snapshot(name, model_cache, allow_model_downloads=request.get("allow_model_downloads", False))
             for name in (repo, "Qwen/Qwen3-ForcedAligner-0.6B")]
    engine = Qwen3ASRModel.from_pretrained(
        paths[0], dtype=dtype, device_map=target, attn_implementation="sdpa",
        forced_aligner=paths[1], forced_aligner_kwargs={"dtype": dtype, "device_map": target,
                                                       "attn_implementation": "sdpa"},
        max_inference_batch_size=1, max_new_tokens=512,
    )
    audio = _read_pcm_audio(request["audio"])
    from .acoustic import frame_energy, quiet_intervals
    align_audio = audio if request.get("alignment_audio", request["audio"]) == request["audio"] else _read_pcm_audio(request["alignment_audio"])
    sample_rate, chunk_samples = 16000, 30 * 16000
    if request.get("duration") is not None:
        audio = audio[:round(request["duration"] * sample_rate)]
    cues, raw_chunks = [], []
    energy = frame_energy(align_audio[:len(audio)])
    for chunk_index, (begin, finish) in enumerate(_audio_windows(len(audio), chunk_samples, energy)):
        chunk = audio[begin:finish]
        offset, duration = begin / sample_rate, len(chunk) / sample_rate
        _worker_emit({"progress": f"Qwen 正在识别与对齐 {offset:.1f}–{offset + duration:.1f}/{len(audio)/sample_rate:.1f} 秒",
                      "metrics": {"activity": "识别与对齐", "done": offset, "total": len(audio)/sample_rate, "unit": "秒"}})
        align_chunk = align_audio[begin:finish]
        pauses = quiet_intervals(frame_energy(align_chunk))
        with torch.inference_mode():
            result = engine.transcribe(audio=(chunk, sample_rate), language=language,
                                       context=context, return_time_stamps=False)[0]
            alignment = (engine.forced_aligner.align(audio=(align_chunk, sample_rate), text=result.text,
                         language=result.language or language)[0] if result.text.strip() else [])
        trace = {}
        alignment_error = None
        try:
            changes = request.get("speaker_changes", [])
            local = [t - offset for t in changes[bisect_right(changes, offset):bisect_right(changes, offset + duration)]]
            batch = _alignment_to_cues(result.text, alignment, source=request["source"],
                                       offset=request["start"] + offset, duration=duration, chunk_index=chunk_index,
                                       speaker_changes=local, pauses=pauses, trace=trace)
            if trace.get("unplaced_unit_ranges"):
                alignment_error = "部分零时长原文缺少相邻声学锚点，已保留在审计记录。"
        except MediaError as exc:
            # Keep the actual transcript and alignment in the audit artifact, but
            # never manufacture acoustic times or discard all preceding work.
            alignment_error, batch = str(exc), []
            _worker_emit({"progress": f"Qwen {offset:.1f}–{offset+duration:.1f} 秒无法可靠对齐；保留原文记录，不生成猜测时间字幕。"})
        cues.extend(dataclasses.asdict(cue) for cue in batch)
        raw_chunks.append({**trace, "quiet_intervals": pauses, "start": request["start"] + offset, "duration": duration,
                           "text": result.text, "language": result.language,
                           "alignment_error": alignment_error,
                           "alignment": [dataclasses.asdict(item) for item in alignment]})
        if request.get("raw_output"):
            Path(request["raw_output"]).write_text(json.dumps({"model": repo, "complete": False,
                                                                "chunks": raw_chunks}, ensure_ascii=False, indent=2), encoding="utf-8")
        _worker_emit({"progress": f"Qwen 已完成 {offset + duration:.1f}/{len(audio)/sample_rate:.1f} 秒 · {len(cues)} 句",
                      "metrics": {"activity": "识别与对齐", "done": offset + duration, "total": len(audio)/sample_rate, "unit": "秒"}})
    Path(request["result"]).write_text(json.dumps(cues, ensure_ascii=False), encoding="utf-8")
    # Probe callers can request full raw aligned units for auditable model comparison.
    if request.get("raw_output"):
        from importlib.metadata import version
        runtime = {p: version(p) for p in ("torch", "qwen-asr", "transformers", "accelerate")}
        runtime.update({"alignment_input": request.get("alignment_input", "recognition_audio"),
                        "recognition_input": request.get("recognition_input", "off"),
                        "device": device, "dtype": str(dtype), "torch_cuda": torch.version.cuda,
                        "context": context, "model_revision": Path(paths[0]).name,
                        "aligner_revision": Path(paths[1]).name})
        if device == "cuda":
            runtime.update({"gpu_name": torch.cuda.get_device_name(),
                            "gpu_capability": torch.cuda.get_device_capability(),
                            "peak_cuda_allocated_bytes": torch.cuda.max_memory_allocated()})
        Path(request["raw_output"]).write_text(json.dumps({"model": repo, "complete": True, "runtime": runtime,
                                                            "chunks": raw_chunks}, ensure_ascii=False, indent=2), encoding="utf-8")
    _worker_emit({"done": True, "device": device, "model": repo, "cues": len(cues),
                  "quiet_intervals": [[chunk["start"]+a, chunk["start"]+b] for chunk in raw_chunks
                                      for a,b in chunk["quiet_intervals"]]})
    return 0


def _audio_windows(sample_count: int, chunk_samples: int, energy=None) -> list[tuple[int, int]]:
    """Prefer quiet window edges. Legacy callers without PCM keep fixed windows."""
    if energy is not None:
        from .acoustic import pcm_windows
        return list(pcm_windows(sample_count, chunk_samples, energy))
    windows = [(start, min(start + chunk_samples, sample_count))
               for start in range(0, sample_count, chunk_samples)]
    if len(windows) > 1 and windows[-1][1] - windows[-1][0] < 1600:
        windows[-2] = (windows[-2][0], windows[-1][1])
        windows.pop()
    return windows


def _run_worker(request: dict, temp_dir: Path, progress, cancelled) -> tuple[bool, str]:
    from .runtime_deps import require, worker_command
    is_qwen = bool(_qwen_model_id(request["model"]))
    if not is_qwen:
        try:
            request["asr_site"] = require(installation_root(), "local-asr")["path"]
            if request["device"] == "cuda":
                request["cuda_directories"] = require(installation_root(), "whisper-cuda")["directories"]
        except RuntimeError as exc:
            return False, str(exc)
    request_path = temp_dir / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    environment = os.environ.copy()
    # Supports source checkout and installed package without relying on caller CWD.
    app_path = str(Path(__file__).resolve().parents[1])
    if is_qwen:
        environment["TINGMU_QWEN_WORKER"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    if not request.get("allow_model_downloads", False):
        environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    events: queue.Queue = queue.Queue()
    error, done = "", False
    with tempfile.TemporaryFile() as stderr:
        executable = _qwen_python() if _qwen_model_id(request["model"]) else Path(sys.executable)
        executable = worker_command(executable, environment)
        entry = ("import sys; sys.path.insert(0, sys.argv[1]); from pathlib import Path; "
                 "from tingmu.media import _asr_worker; sys.exit(_asr_worker(Path(sys.argv[2])))")
        proc = subprocess.Popen([str(executable), "-I", "-B", "-c", entry, app_path, str(request_path)],
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
                    progress(Progress(event["progress"], **event.get("metrics", {})))
                if "error" in event:
                    error = event["error"]
                if event.get("done") and type(event.get("vad_filter")) is bool:
                    request["vad_filter"] = event["vad_filter"]
                if event.get("done") and isinstance(event.get("quiet_intervals"), list):
                    request["asr_quiet_intervals"] = event["quiet_intervals"]
                if event.get("done"):
                    for key in ("suppressed_segments", "unplaced_segments"):
                        if type(event.get(key)) is int and event[key] >= 0:
                            request[key] = event[key]
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
    if "asr_quiet_intervals" in request:
        intervals = []
        for a,b in request["asr_quiet_intervals"]:
            if not (math.isfinite(a) and math.isfinite(b) and 0 <= a < b):
                continue
            a,b = round(a,3), round(b,3)
            if intervals and a <= intervals[-1][1]+.001:
                intervals[-1][1] = max(intervals[-1][1], b)
            else:
                intervals.append([a,b])
        result["asr_quiet_intervals"] = intervals
    if not qwen_model:
        result["asr_runtime"] = {"device": request["device"],
                                 "compute_type": "float16" if request["device"] == "cuda" else "int8"}
        if "vad_filter" in request:
            result["asr_runtime"]["vad_filter"] = request["vad_filter"]
        for key in ("suppressed_segments", "unplaced_segments"):
            if key in request:
                result["asr_runtime"][key] = request[key]
        if request.get("raw_output") and Path(request["raw_output"]).is_file():
            result["asr_audit_path"] = str(request["raw_output"])
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
                        "context", "model_revision", "aligner_revision", "recognition_input", "alignment_input", "gpu_name", "peak_cuda_allocated_bytes_scope"):
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
            estimated_cues = limited_cues = 0
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    continue
                timings = chunk.get("cue_timings")
                if isinstance(timings, list):
                    for timing in timings:
                        basis = timing.get("timing_basis") if isinstance(timing, dict) else None
                        if basis in ("bounded_estimate", "anchor_limited"):
                            estimated_cues += 1
                        if basis == "anchor_limited":
                            limited_cues += 1
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
            if estimated_cues:
                result.update(alignment_estimated_cue_count=estimated_cues, alignment_limited_cue_count=limited_cues)
    except (OSError, ValueError, TypeError, RecursionError, OverflowError):
        # Metadata is optional: successful, validated cues must remain usable.
        pass
    return result


def transcribe(media_path: Path, language: str = "yue", model: str = "large-v3-turbo",
               device: str = "auto", audio_track: int = 0, start: float = 0,
               duration: float | None = None, progress: Callable[[str], None] | None = None,
               cancelled: Callable[[], bool] | None = None, *, initial_prompt: str | None = None,
               raw_output: Path | None = None, metadata_out: dict | None = None,
               suppress_bgm: bool = False, speaker_turns: bool = False,
               audit_dir: Path | None = None, allow_model_downloads: bool = False) -> list[Cue]:
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
    from .workspace_models import WHISPER_MODELS, weight_readiness
    if model.startswith("local:"):
        from .local_whisper import resolve_local_model
        model = str(resolve_local_model(model))
    qwen_model = _qwen_model_id(model)
    selected = model.lower().removeprefix("qwen/") if qwen_model else model
    if not allow_model_downloads and (qwen_model or selected in WHISPER_MODELS):
        readiness = weight_readiness(selected)
        if not readiness.ready:
            raise AsrSetupError(readiness.message)
    if qwen_model:
        _qwen_python()  # Fail before extracting the media if optional environment is absent.
    else:
        from .runtime_deps import require
        try:
            require(installation_root(), "local-asr")
        except RuntimeError as exc:
            raise AsrSetupError(str(exc)) from exc
    if portable_root():
        _executable("ffmpeg")  # Surface setup before a consented weight download.
        if device == "cuda":
            from .runtime_deps import cached_probe
            is_qwen = bool(_qwen_model_id(model))
            check = cached_probe(installation_root(), "qwen" if is_qwen else "whisper-cuda")
            if not check.get("cuda_usable"):
                raise AsrSetupError("Qwen CUDA 不可用，可在语音识别设置中选择 CPU 或自动。" if is_qwen else check["detail"])
    if allow_model_downloads and is_clean_install():
        from .workspace_models import download_model
        if selected in QWEN_MODELS or selected in WHISPER_MODELS:
            download_model(selected, progress or (lambda _: None), cancelled or (lambda: False))
            allow_model_downloads = False
    cache = _cache_root()
    cache.mkdir(parents=True, exist_ok=True)
    if raw_output is None:
        import uuid
        audit_dir = Path(audit_dir) if audit_dir is not None else cache / ("qwen-audit" if _qwen_model_id(model) else "whisper-audit")
        audit_dir.mkdir(parents=True, exist_ok=True)
        raw_output = audit_dir / f"{uuid.uuid4().hex}.json"
    if raw_output is not None:
        Path(raw_output).resolve().parent.mkdir(parents=True, exist_ok=True)
    audio_metadata = {}
    with (tempfile.TemporaryDirectory(prefix="asr-", dir=cache) as temporary,
          prepared_audio(media_path, audio_track=audio_track, start=start, duration=duration,
                         suppress_bgm=suppress_bgm, progress=progress, cancelled=cancelled,
                         metadata_out=audio_metadata) as audio):
        folder = Path(temporary)
        align_audio = audio
        if suppress_bgm:
            align_audio = extract_audio(media_path, folder / "original.wav", audio_track, start, duration,
                                        cancelled=cancelled)
        changes = []
        if speaker_turns:
            from .speech import detect_speaker_changes, speech_model_status
            method = speech_model_status(cache)["speaker"]
            if method == "unavailable":
                if progress:
                    progress("未安装本地换人检测模型，保留识别器原有分句。")
            else:
                changes = detect_speaker_changes(align_audio, cache / "speech-models/pyannote/model.onnx",
                                                 lambda: _check_cancelled(cancelled), progress)
            audio_metadata.update(speaker_method=method, speaker_change_count=len(changes),
                                  speaker_changes=[round(start + t, 3) for t in changes])
        request = {"audio": str(audio), "alignment_audio": str(align_audio), "alignment_input": "original_track",
                   "recognition_input": audio_metadata.get("bgm_method", "off"), "model": model, "language": _normalise_language(language),
                   "model_cache": str(confined_path(cache, "models")), "start": start, "result": str(folder / "result.json"),
                   "duration": duration, "speaker_changes": changes, "allow_model_downloads": allow_model_downloads,
                   "initial_prompt": initial_prompt,
                   "qwen_model_cache": str(confined_path(cache, "qwen-models")),
                   "raw_output": str(Path(raw_output).resolve()) if raw_output else None,
                   "source": f"{Path(media_path).resolve()}|{audio_track}|{model}|{language}"}
        errors = []
        for attempt in (["cuda", "cpu"] if device == "auto" else [device]):
            _check_cancelled(cancelled)
            if progress:
                progress(Progress("正在加载共享目录中的本地语音模型。", activity="加载模型"))
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
                                "alignment_warnings_truncated", "alignment_estimated_cue_count",
                                "alignment_limited_cue_count", "asr_quiet_intervals", "bgm_method", "speaker_method",
                                "speaker_change_count", "speaker_changes"):
                        metadata_out.pop(key, None)
                    metadata_out.update(metadata)
                    if suppress_bgm or speaker_turns:
                        metadata_out.update(audio_metadata)
                return cues
            errors.append(f"{attempt}: {error}")
            if device == "auto" and attempt == "cuda" and progress:
                progress(f"CUDA 转写失败，改用 CPU（速度较慢）：{error[:500]}")
        if _qwen_model_id(model):
            raise MediaError("Qwen 本地转写失败。请打开「运行环境与依赖管理」检测 Qwen，确认 PyTorch、"
                             "显存和 ASR/ForcedAligner 权重完整；也可在设置中选择 CPU（较慢）。\n" + "\n".join(errors))
        raise MediaError("本地转写失败。请检查工作区语音设置中的模型是否已下载完整；CUDA 需要兼容的 NVIDIA 驱动、"
                         "CUDA 12 cuBLAS 与 cuDNN 9 DLL，可在设置中改用 CPU。\n" + "\n".join(errors))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--asr-worker":
        raise SystemExit(_asr_worker(Path(sys.argv[2])))
    raise SystemExit("此模块仅支持内部 ASR worker；使用 tingmu CLI 启动应用。")
