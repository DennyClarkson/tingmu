"""Bounded-memory, offline vocal separation and acoustic speaker-change detection."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from .task_progress import Progress
import wave

import numpy as np

from .speech_assets import speech_model_status as speech_model_status


def _session(path: Path):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 4
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"])


def _vocal_block(samples, window, hop, engines):
    frames = np.lib.stride_tricks.sliding_window_view(samples.T, len(window), axis=1)[:, ::hop]
    spectrum = np.fft.rfft(frames * window)
    inputs = np.ascontiguousarray(np.abs(spectrum[:, None, :, :1024]), dtype=np.float32)
    powers = [np.square(session.run(None, {session.get_inputs()[0].name: inputs})[0][:, 0])
              for session in engines]
    mask = (powers[0] + 5e-11) / (powers[0] + powers[1] + 1e-10)
    spectrum[:, :, :1024] *= mask
    spectrum[:, :, 1024:] = 0
    mono = np.fft.irfft(spectrum.mean(axis=0)) * window
    return _overlap_add(mono, hop, window)


def _overlap_add(frames, hop, window):
    length = (len(frames) - 1) * hop + len(window)
    signal = np.zeros(length, dtype=np.float32)
    weight = np.zeros(length, dtype=np.float32)
    for i, frame in enumerate(frames):
        idx = slice(i * hop, i * hop + len(window))
        signal[idx] += frame
        weight[idx] += window * window
    return signal / np.maximum(weight, 1e-8)


def isolate_vocals(source: Path, dest: Path, models: Path, check: Callable, progress=None):
    """44.1 kHz stereo raw PCM16 to mono PCM16, one fixed network window at a time."""
    engines = [_session(models / f"{name}.fp16.onnx") for name in ("vocals", "accompaniment")]
    hop, n_fft = 1024, 4096
    window = np.hanning(n_fft + 1)[:-1].astype(np.float32)
    # Read context for both sides, discard model edges. Every sample has one owner.
    length, context = 511 * hop + n_fft, 24 * hop
    stride = length - 2 * context
    total = source.stat().st_size // 4
    with source.open("rb") as stream, dest.open("wb") as output:
        for start in range(0, total, stride):
            check()
            origin = start - context
            left, right = max(0, origin), min(total, origin + length)
            block = np.zeros((length, 2), dtype=np.float32)
            stream.seek(left * 4)
            pcm = np.frombuffer(stream.read(4 * (right - left)), dtype="<i2").reshape(-1, 2)
            block[left-origin:right-origin] = pcm / 32768.0
            separated = _vocal_block(block, window, hop, engines)
            count = min(stride, total - start)
            mono = separated[context:context + count]
            output.write(np.clip(np.rint(mono * 32768), -32768, 32767).astype("<i2").tobytes())
            if progress and (start == 0 or start // stride % 8 == 0 or start + count == total):
                progress(Progress(f"本地人声分离 {(start + count)/44100:.0f}/{total/44100:.0f} 秒",
                                  activity="人声分离", done=(start + count)/44100, total=total/44100, unit="秒"))


def _solo_changes(scores):
    # Pyannote powerset classes 1–3 represent individual speakers within this
    # window. Ignore simultaneous voices, silence and very short transitions.
    labels = np.argmax(scores, axis=1)
    splits = np.flatnonzero(np.diff(labels)) + 1
    runs = zip(np.r_[0, splits], np.r_[splits, len(labels)])
    previous = None
    for first, end in runs:
        label = labels[first]
        if not 1 <= label <= 3 or end - first < 10:
            continue
        if previous is not None:
            prev_end, prev_label = previous
            if prev_label != label and first - prev_end < 72:
                yield (((prev_end - 1 + first) / 2) * 270 + 991 / 2) / 16000
        previous = (end, label)


def detect_speaker_changes(audio: Path, model: Path, check: Callable, progress=None) -> list[float]:
    """Local change instants relative to this WAV; no cross-window speaker identities."""
    engine = _session(model)
    input_name = engine.get_inputs()[0].name
    changes = []
    with wave.open(str(audio), "rb") as stream:
        total = stream.getnframes()
        size, step = 160000, 80000
        for begin in range(0, total, step):
            check()
            stream.setpos(begin)
            pcm = np.frombuffer(stream.readframes(size), dtype="<i2")
            signal = np.zeros((1, 1, size), dtype=np.float32)
            signal[0, 0, :len(pcm)] = pcm / 32768.0
            scores = engine.run(None, {input_name: signal})[0][0]
            last = begin + size >= total
            left = 0 if begin == 0 else 2.5
            right = len(pcm) / 16000 if last else 7.5
            for sec in _solo_changes(scores):
                absolute = sec + begin / 16000
                if left <= sec < right and (not changes or absolute - changes[-1] > 0.18):
                    changes.append(round(float(absolute), 3))
            if progress and (begin % (step * 12) == 0 or last):
                progress(Progress(f"本地换人检测 {min(total, begin + size)/16000:.0f}/{total/16000:.0f} 秒",
                                  activity="换人检测", done=min(total, begin + size)/16000, total=total/16000, unit="秒"))
            if last:
                break
    return changes

def suppress_repetitive_background(source: Path, dest: Path, check: Callable, progress=None):
    """Attenuate persistent frequency components in overlapping 4-second local windows."""
    width, hop, stride, context = 512, 128, 32000, 16000
    window = np.hanning(width + 1)[:-1].astype(np.float32)
    with wave.open(str(source), "rb") as audio, wave.open(str(dest), "wb") as output:
        output.setparams(audio.getparams())
        total = audio.getnframes()
        for start in range(0, total, stride):
            check()
            end = min(start + stride, total)
            left, right = max(0, start - context), min(total, end + context)
            audio.setpos(left)
            pcm = np.frombuffer(audio.readframes(right - left), dtype="<i2").astype(np.float32)
            pcm = np.pad(pcm, (width, width))
            frames = np.lib.stride_tricks.sliding_window_view(pcm, width)[::hop]
            spectrum = np.fft.rfft(frames * window)
            magnitude = np.abs(spectrum)
            floor = np.percentile(magnitude, 25, axis=0)
            # Keep a residual to avoid silencing sustained vowels at music frequencies.
            gain = np.clip(1 - 1.15 * floor / np.maximum(magnitude, 1e-6), .18, 1)
            filtered = _overlap_add(np.fft.irfft(spectrum * gain) * window, hop, window)
            offset = start - left + width
            output.writeframesraw(np.clip(np.rint(filtered[offset:offset + end - start]),
                                          -32768, 32767).astype("<i2").tobytes())
            if progress and (start // stride % 5 == 0 or end == total):
                progress(Progress(f"频谱降噪 {end/16000:.0f}/{total/16000:.0f} 秒",
                                  activity="频谱降噪", done=end/16000, total=total/16000, unit="秒"))
