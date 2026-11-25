"""Explicit audio transcription at a user-selected OpenAI-compatible endpoint."""
from __future__ import annotations

import io
import ipaddress
import json
import math
from pathlib import Path
import re
import uuid
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import wave

from .credentials import protect, unprotect
from .media import _cue_id, _normalise_language
from .models import Cue
from .providers import NoRedirect, ProviderError, RequestCancelled, validate_base_url
from .storage import atomic_json
from .task_progress import Progress


CHUNK_SECONDS = 600
MAX_WAV_BYTES = 20_000_000  # PCM upload file including its 44-byte RIFF header.
MAX_RESPONSE = 8 * 1024 * 1024


def _is_loopback(url):
    host = urlsplit(url).hostname
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def api_options(settings):
    if settings.asr_backend == "local_api":
        return settings.local_asr_endpoint, settings.local_asr_api_model, settings.local_asr_response_format, settings.local_asr_key_env
    return settings.asr_endpoint, settings.external_asr_model, settings.asr_response_format, settings.asr_key_env


def validate_speech_setting(name, value):
    choices = {"asr_backend": ("local", "local_api", "external"), "asr_response_format": ("verbose_json", "diarized_json"),
               "local_asr_response_format": ("verbose_json", "diarized_json")}
    if name in choices and value not in choices[name]:
        raise ValueError("请选择有效的字幕识别方式或返回格式。")
    if name in ("asr_endpoint", "local_asr_endpoint"):
        try:
            endpoint = validate_base_url(value, key_source="none")
        except ProviderError as exc:
            raise ValueError(str(exc)) from None
        if name == "local_asr_endpoint" and not _is_loopback(endpoint):
            raise ValueError("本机语音 API 地址必须指向 127.0.0.1、localhost 或 [::1]。")
        return endpoint
    if name in ("asr_key_env", "local_asr_key_env") and value and not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]{0,127}", value):
        raise ValueError("请填写密钥的环境变量名称；手工密钥请填在专用密码框。")
    if name in ("external_asr_model", "local_asr_api_model") and (not value or len(value) > 180 or any(c.isspace() for c in value)):
        raise ValueError("请输入服务支持的语音识别模型 ID。")
    return value


def save_audio_key(data_dir, endpoint, secret, *, local=False):
    path = Path(data_dir) / ("local-audio-credential.json" if local else "audio-credential.json")
    if not secret:
        path.unlink(missing_ok=True)
        return
    endpoint = validate_speech_setting("local_asr_endpoint", endpoint) if local else validate_base_url(endpoint)
    if len(secret) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in secret):
        raise ProviderError("密钥格式无效。")
    atomic_json(path, {"endpoint": endpoint, "encrypted": protect(secret)})


def _api_key(data_dir, endpoint, env_name, *, local=False):
    path = Path(data_dir) / ("local-audio-credential.json" if local else "audio-credential.json")
    if path.is_file():
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if entry["endpoint"] == endpoint:
                return unprotect(entry["encrypted"])
        except (ValueError, TypeError, KeyError) as exc:
            raise ProviderError("无法读取工作区语音密钥，请重新保存。") from exc
    from .agent import _environment
    return _environment((env_name,)) if env_name else ""


def _connection(settings, data_dir):
    local = settings.asr_backend == "local_api"
    endpoint, _, _, env_name = api_options(settings)
    endpoint = validate_speech_setting("local_asr_endpoint" if local else "asr_endpoint", endpoint)
    validate_speech_setting("asr_key_env", env_name)
    key = _api_key(data_dir, endpoint, env_name, local=local)
    if not local:
        validate_base_url(endpoint, key_source="environment" if key else "none")
    return endpoint, key


def _json_request(request, timeout):
    handlers = [NoRedirect()]
    if _is_loopback(request.full_url):
        handlers.append(urllib.request.ProxyHandler({}))
    try:
        with urllib.request.build_opener(*handlers).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError()
        return json.loads(raw)
    except urllib.error.HTTPError as error:
        error.close()
        raise ProviderError(f"语音服务返回 HTTP {error.code}，请检查地址、认证、模型和返回格式。") from None
    except OSError:
        raise ProviderError("语音服务连接失败或超时。") from None
    except (UnicodeError, ValueError):
        raise ProviderError("语音服务未返回可用的 JSON。") from None


def list_models(settings, data_dir):
    endpoint, key = _connection(settings, data_dir)
    suffix = "/audio/transcriptions"
    if not endpoint.endswith(suffix):
        raise ProviderError("此地址没有标准模型列表路径，可直接填写服务使用的模型 ID。")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    result = _json_request(urllib.request.Request(endpoint[:-len(suffix)] + "/models", headers=headers), 20)
    rows = result.get("data") if isinstance(result, dict) else None
    if not isinstance(rows, list):
        raise ProviderError("服务未提供模型列表，可直接填写模型 ID。")
    return sorted({row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)
                   and 0 < len(row["id"]) <= 180 and not any(ord(c) <= 32 for c in row["id"])})


def _wav_chunks(path, check=None, *, original=None, quiet_out=None):
    """Cut the prepared PCM at sustained quiet intervals within the upload budget."""
    import numpy as np
    from .acoustic import frame_energy, pcm_windows, quiet_intervals

    with wave.open(str(path), "rb") as reader:
        rate = reader.getframerate()
        if (reader.getnchannels(), reader.getsampwidth(), rate) != (1, 2, 16000):
            raise ProviderError("外部识别需要单声道 16 kHz PCM 音频。")
        total = reader.getnframes()
        size = min(int(CHUNK_SECONDS * rate), (MAX_WAV_BYTES - 44) // 2)

        def read_at(start, count):
            if check:
                check()
            reader.setpos(start)
            samples = reader.readframes(count)
            if len(samples) != count * 2:
                raise ProviderError("临时音轨读取不完整。")
            return samples

        # Only the compact 20 ms RMS trace lives for the whole track. The
        # original mix is local timing evidence; uploads always use reader.
        with wave.open(str(original or path), "rb") as mix:
            if (mix.getnchannels(), mix.getsampwidth(), mix.getframerate()) != (1, 2, rate):
                raise ProviderError("原始音轨格式与识别音轨不符。")
            measured = min(total, mix.getnframes())
            hop = rate // 50
            energy = np.empty(measured // hop, dtype=np.float32)
            for first in range(0, len(energy), 1500):
                if check:
                    check()
                last = min(first + 1500, len(energy))
                count = (last - first) * hop
                raw = mix.readframes(count)
                if len(raw) != count * 2:
                    raise ProviderError("临时音轨读取不完整。")
                energy[first:last] = frame_energy(np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768., rate)
            if quiet_out is not None:
                quiet_out.extend(quiet_intervals(energy))

        for position, end in pcm_windows(total, size, energy, rate):
            length = end - position
            samples = read_at(position, length)
            output = io.BytesIO()
            with wave.open(output, "wb") as writer:
                writer.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                writer.writeframes(samples)
            yield position / rate, length / rate, output.getvalue(), total > size


def _post(endpoint, key, fields, audio):
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.extend([f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
                  'Content-Type: audio/wav\r\n\r\n'.encode(), audio, f"\r\n--{boundary}--\r\n".encode()])
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(endpoint, data=b"".join(parts), headers=headers, method="POST")
    return _json_request(request, 180)


def _segments(data, *, source, offset, duration, namespace, diarized, evidence=None, chunk=1, blanks=None):
    if not isinstance(data, dict) or not isinstance(data.get("segments"), list):
        raise ProviderError("语音服务必须返回带起止时间的 segments。请检查模型与返回格式。")
    result = []
    for index, segment in enumerate(data["segments"]):
        try:
            start, end, text = segment["start"], segment["end"], segment["text"]
            if not all(type(v) in (float, int) and math.isfinite(v) for v in (start, end)):
                raise ValueError()
            if start < 0 or start >= end or start >= duration or end > duration + 0.25 or not isinstance(text, str):
                raise ValueError()
            speaker = segment.get("speaker", "")
            if not isinstance(speaker, str) or len(speaker) > 128 or any(ord(c) < 32 for c in speaker):
                raise ValueError()
            if diarized and not speaker.strip():
                raise ValueError()
        except (KeyError, ValueError, TypeError):
            raise ProviderError("语音服务返回了无效的时间段、正文或说话人标记。") from None
        if text.strip():
            absolute_start, absolute_end = start + offset, min(end, duration) + offset
            ident = _cue_id(source, index, absolute_start, absolute_end)
            if evidence is not None:
                evidence[ident] = {"chunk": chunk, "segment_index": index, "offset": offset,
                                   "reported_start": start, "reported_end": end}
            result.append(Cue(ident, absolute_start, absolute_end, text.strip(),
                              original_text=text, speaker=namespace + speaker if speaker else ""))
        elif blanks is not None:
            blanks.append({"chunk": chunk, "segment_index": index, "offset": offset,
                           "reported_start": start, "reported_end": end})
    return sorted(result, key=lambda cue: (cue.start, cue.end))


def transcribe_external(media_path, settings, data_dir, *, start=0, duration=None,
                        progress=None, cancelled=None, permission=None, metadata_out=None):
    from .media import prepared_audio
    local = settings.asr_backend == "local_api"

    def check():
        if cancelled and cancelled():
            raise RequestCancelled("外部识别已停止。")
        if not local and (settings.allow_audio_upload is not True or (permission and not permission())):
            raise ProviderError("请先在当前工作区允许向所选语音服务发送音轨，并检查任务的服务地址。")

    def preparation_cancelled():
        check()
        return False

    check()
    _, model, fmt, _ = api_options(settings)
    endpoint, key = _connection(settings, data_dir)
    validate_speech_setting("external_asr_model", model)
    validate_speech_setting("asr_response_format", fmt)
    fields = {"model": model, "response_format": fmt}
    language = _normalise_language(settings.target_language)
    if language and re.fullmatch("[a-z]{2}", language):
        fields["language"] = language
    if fmt == "diarized_json":
        fields["chunking_strategy"] = "auto"
    cues = []
    chunks = 0
    segment_sources = {} if metadata_out is not None else None
    blanks = [] if metadata_out is not None else None
    quiet = [] if metadata_out is not None else None
    original = {}
    with prepared_audio(media_path, audio_track=settings.audio_track, start=start, duration=duration,
                        suppress_bgm=settings.suppress_bgm, progress=progress, cancelled=preparation_cancelled,
                        metadata_out=metadata_out, original_out=original) as audio:
        with wave.open(str(audio), "rb") as source:
            length = source.getnframes() / source.getframerate()
        windows = _wav_chunks(audio, check, original=original.get("path"), quiet_out=quiet)
        for chunks, (offset, seconds, wav, multiple) in enumerate(windows, 1):
            label = "本机 API" if local else "外部语音识别"
            if progress:
                progress(Progress(f"{label} · 第 {chunks} 段（{seconds:.0f} 秒）",
                                  activity=label, done=offset, total=length, unit="秒"))
            check()
            data = _post(endpoint, key, fields, wav)
            check()
            source = f"{Path(media_path).resolve()}|{settings.audio_track}|{model}|{chunks}"
            cues.extend(_segments(data, source=source, offset=start + offset, duration=seconds,
                                  namespace=f"{chunks}·" if multiple else "", diarized=fmt == "diarized_json",
                                  evidence=segment_sources, chunk=chunks, blanks=blanks))
            if progress:
                progress(Progress(f"{label}已完成第 {chunks} 段", activity=label, done=offset+seconds,
                                  total=length, unit="秒"))
    if metadata_out is not None:
        metadata_out.update(asr_backend=settings.asr_backend, asr_model=model, asr_response_format=fmt,
                            audio_chunks=chunks, asr_segment_sources=segment_sources, asr_blank_segments=blanks,
                            asr_quiet_intervals=[[start + a, start + b] for a, b in quiet],
                            speaker_source="provider" if any(c.speaker for c in cues) else "")
    return cues
