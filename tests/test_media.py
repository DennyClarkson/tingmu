from pathlib import Path
import json
from types import SimpleNamespace
import sys

import pytest

from tingmu import media
from tingmu.models import Cue


def test_scan_recursive_case_insensitive_ignores_cache(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / ".cache").mkdir()
    for name in ["a.MKV", "sub/b.mp3", "skip.txt", ".cache/c.wav"]:
        (tmp_path / name).touch()
    assert [p.name for p in media.scan_media(tmp_path)] == ["a.MKV", "b.mp3"]


@pytest.mark.parametrize("extension", ["srt", "vtt", "ass", "ssa"])
def test_subtitle_roundtrip_multiline_and_absolute_times(tmp_path, extension):
    cues = [Cue("one", 181.234, 183.567, "你喺邊度？\nWhere are you?", original_text="unchanged raw")]
    path = tmp_path / f"字幕.{extension}"
    media.save_subtitles(cues, path)
    imported = media.load_subtitles(path)
    assert len(imported) == 1
    assert imported[0].text == cues[0].text
    # ASS/SSA time precision is centiseconds; SRT/VTT is milliseconds.
    precision = 0.011 if extension in {"ass", "ssa"} else 0.001
    assert imported[0].start == pytest.approx(cues[0].start, abs=precision)
    assert imported[0].end == pytest.approx(cues[0].end, abs=precision)
    assert media.load_subtitles(path)[0].id == imported[0].id
    assert cues[0].original_text == "unchanged raw"


def test_ass_preserves_raw_formatting_but_displays_plain_text(tmp_path):
    import pysubs2
    path = tmp_path / "styled.ass"
    subs = pysubs2.SSAFile()
    subs.append(pysubs2.SSAEvent(start=1000, end=2000, text=r"{\i1}原文{\i0}\N第二行"))
    subs.save(str(path))
    cue = media.load_subtitles(path)[0]
    assert cue.text == "原文\n第二行"
    assert cue.original_text == r"{\i1}原文{\i0}\N第二行"


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "gb18030"])
def test_subtitle_encodings(tmp_path, encoding):
    path = tmp_path / "chinese.srt"
    path.write_bytes("1\n00:00:01,000 --> 00:00:02,000\n你好\n".encode(encoding))
    assert media.load_subtitles(path)[0].text == "你好"


def test_extract_maps_audio_ordinal_not_stream_index(tmp_path, monkeypatch):
    source, output = tmp_path / "source.mkv", tmp_path / "track.wav"
    source.touch()
    captured = []
    monkeypatch.setattr(media, "_executable", lambda name: name)
    def run(command, **kwargs):
        captured.extend(command)
        output.write_bytes(b"0" * 100)
    monkeypatch.setattr(media, "_run", run)
    media.extract_audio(source, output, audio_track=1, start=180, duration=120)
    assert captured[captured.index("-map") + 1] == "0:a:1"
    assert captured[captured.index("-ss") + 1] == "180"
    assert captured[captured.index("-t") + 1] == "120"


def test_cancellation_before_any_work(tmp_path):
    with pytest.raises(media.TranscriptionCancelled):
        media.transcribe(tmp_path / "missing.mkv", cancelled=lambda: True)


def test_language_aliases_and_auto_detection():
    assert media._normalise_language("auto") is None
    assert media._normalise_language("zh-CN") == "zh"
    assert media._normalise_language("zh-HK") == "yue"


def test_pcm_reader_preserves_signed_sample_scaling(tmp_path):
    import wave
    import struct
    path = tmp_path / "test.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(struct.pack("<hhh", -32768, 0, 16384))
    assert media._read_pcm_audio(str(path)).tolist() == [-1.0, 0.0, 0.5]


def test_running_subprocess_cancelled_promptly():
    import time
    began = time.monotonic()
    with pytest.raises(media.TranscriptionCancelled):
        media._run([sys.executable, "-c", "import time; time.sleep(30)"],
                   cancelled=lambda: time.monotonic() - began > 0.2)
    assert time.monotonic() - began < 5


def test_auto_cuda_failure_retries_cpu_without_duplicate_cues(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "_cache_root", lambda: tmp_path)
    monkeypatch.setattr(media, "extract_audio", lambda *a, **k: tmp_path / "audio.wav")
    attempts = []
    def worker(request, *args):
        attempts.append(request["device"])
        if request["device"] == "cuda":
            return False, "CUDA missing DLL"
        Path(request["result"]).write_text(json.dumps([{"id": "a", "start": 180, "end": 181,
                                                       "text": "ok", "original_text": " ok"}]), encoding="utf-8")
        return True, ""
    monkeypatch.setattr(media, "_run_worker", worker)
    cues = media.transcribe(tmp_path / "video.mkv", start=180, duration=120)
    assert attempts == ["cuda", "cpu"]
    assert len(cues) == 1 and cues[0].original_text == " ok"


@pytest.mark.parametrize("avg_logprob,segment_end,uncertain", [(-1.2, 2.75, True), (-0.1, 20.0, True), (-0.1, 2.75, False)])
def test_worker_offsets_timestamps_and_keeps_raw_text(tmp_path, monkeypatch, avg_logprob, segment_end, uncertain):
    segment = SimpleNamespace(text="  佢喺邊？ ", start=1.25, end=segment_end,
                              avg_logprob=avg_logprob, no_speech_prob=0.1, compression_ratio=1.0)
    class Engine:
        supported_languages = ["yue"]
        def __init__(self, *a, **k):
            pass
        def transcribe(self, *a, **k):
            assert k["language"] == "yue" and k["task"] == "transcribe"
            return iter([segment]), SimpleNamespace(duration=120, language="yue")
    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace(get_cuda_device_count=lambda: 1))
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Engine))
    monkeypatch.setattr(media, "_register_cuda_dlls", lambda: None)
    monkeypatch.setattr(media, "_read_pcm_audio", lambda path: path)
    request = {"device": "cuda", "model": "large-v3-turbo", "model_cache": str(tmp_path),
               "language": "yue", "audio": "dummy.wav", "start": 180, "source": "test",
               "result": str(tmp_path / "result.json")}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf-8")
    assert media._asr_worker(path) == 0
    cue = json.loads(Path(request["result"]).read_text(encoding="utf-8"))[0]
    assert (cue["start"], cue["end"]) == (181.25, 180 + segment_end)
    assert cue["original_text"] == segment.text
    assert cue["text"] == "佢喺邊？" and cue["uncertain"] is uncertain
