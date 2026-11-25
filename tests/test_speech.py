from pathlib import Path
from types import SimpleNamespace
import wave

import numpy as np
import pytest

from tingmu import media, speech
from tingmu.models import Cue


def pcm_file(path, samples, rate=16000):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(np.rint(samples).astype("<i2").tobytes())


def test_repetitive_background_preserves_clock_and_foreground(tmp_path):
    # A persistent musical tone and transient speech-band energy at another frequency.
    t = np.arange(160000) / 16000
    bgm = 3900 * np.sin(2*np.pi*250*t)
    voice = 3100 * np.sin(2*np.pi*875*t) * ((t % 1) < .18)
    source, dest = tmp_path / "mix.wav", tmp_path / "out.wav"
    pcm_file(source, bgm + voice)
    speech.suppress_repetitive_background(source, dest, lambda: None)
    with wave.open(str(dest), "rb") as stream:
        assert stream.getnframes() == len(t)
        result = np.frombuffer(stream.readframes(len(t)), "<i2")
    background_gain = np.dot(result, bgm) / np.dot(bgm, bgm)
    foreground_gain = np.dot(result, voice) / np.dot(voice, voice)
    assert 0 < background_gain < .4
    assert foreground_gain > .85


@pytest.mark.parametrize("keep_original", [False, True])
def test_source_separation_consumes_selected_slice_and_discards_temps(tmp_path, monkeypatch, keep_original):
    source = tmp_path / "media.mkv"
    source.write_bytes(b"private media")
    monkeypatch.setattr(media, "_cache_root", lambda: tmp_path)
    models = tmp_path / "speech-models/spleeter"
    models.mkdir(parents=True)
    monkeypatch.setattr(speech, "speech_model_status", lambda cache: {"bgm": "spleeter-2stems-fp16"})
    calls = []
    def extract(path, output, track, start, duration, cancelled, **fmt):
        calls.append((path, track, start, duration, fmt))
        output.write_bytes(b"mix only")
        return output
    def separate(source, dest, models, check, progress):
        assert source.read_bytes() == b"mix only"
        dest.write_bytes(b"voice only")
    def convert(cmd, cancelled):
        original = Path(cmd[-1]).name == "original.wav"
        assert Path(cmd[cmd.index("-i") + 1]).read_bytes() == (b"mix only" if original else b"voice only")
        pcm_file(Path(cmd[-1]), [100 if original else 0] * 16000)
    monkeypatch.setattr(media, "_extract_pcm", extract)
    monkeypatch.setattr(media, "_executable", lambda name: name)
    monkeypatch.setattr(speech, "isolate_vocals", separate)
    monkeypatch.setattr(media, "_run", convert)
    metadata = {}
    original = {} if keep_original else None
    with media.prepared_audio(source, audio_track=1, start=30, duration=1, metadata_out=metadata,
                              original_out=original) as path:
        assert path.is_file()
        if keep_original:
            assert original["path"].read_bytes() != path.read_bytes()
        assert calls == [(source, 1, 30, 1, {"channels": 2, "rate": 44100, "fmt": "s16le"})]
    assert metadata == {"bgm_method": "spleeter-2stems-fp16"}
    assert not path.parent.exists()
    assert source.read_bytes() == b"private media"


def test_prepared_audio_cancellation_cleanups(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "_cache_root", lambda: tmp_path)
    with pytest.raises(media.TranscriptionCancelled):
        with media.prepared_audio(tmp_path / "a.mkv", cancelled=lambda: True):
            pytest.fail("cancelled work entered caller")
    assert not list(tmp_path.glob("speech-*"))


def test_solo_changes_use_stable_exclusive_voices():
    labels = [0]*12 + [2]*40 + [5]*4 + [1]*45 + [2]*2 + [1]*15 + [0]*100 + [3]*30
    scores = np.eye(7)[labels]
    changes = list(speech._solo_changes(scores))
    assert len(changes) == 1
    assert .88 < changes[0] < 1.0


def test_speaker_split_honors_acoustic_words_and_exact_raw():
    original = Cue("seg", 101, 105, "你嚟？我嚟。", " 你嚟？我嚟。 ")
    words = [SimpleNamespace(start=1., end=2., word="你嚟？"),
             SimpleNamespace(start=2.1, end=3., word="我嚟。")]
    parts = media._whisper_turn_cues(original, words, [2.03], 100)
    assert [(p.start, p.end) for p in parts] == [(101, 102), (102.1, 103)]
    assert "".join(p.original_text for p in parts) == original.original_text
    assert len({p.id for p in parts}) == 2
    assert not any(p.speaker for p in parts)
    assert media._whisper_turn_cues(original, words[:1], [2.03], 100) == [original]
    words[1].start = 1.8
    assert media._whisper_turn_cues(original, words, [2.03], 100) == [original]


def test_qwen_splits_only_at_aligned_turn_boundaries():
    units = [SimpleNamespace(text=c, start_time=t, end_time=t+.1)
             for c, t in zip("你好我好", (0.1, 0.25, 0.46, 0.67))]
    cues = media._alignment_to_cues("你好，我好", units, source="film", offset=11, duration=2,
                                    chunk_index=1, speaker_changes=[.4])
    assert [(c.start, c.end, c.original_text) for c in cues] == [(11.1, 11.35, "你好，"), (11.46, 11.77, "我好")]


def test_vtt_voice_metadata_and_multi_voice_event(tmp_path):
    f = tmp_path / "dialog.vtt"
    f.write_text("WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v 阿明>你好\n\n"
                 "00:00:03.000 --> 00:00:04.000\n<v 甲>係</v> <v 乙>咩</v>", encoding="utf-8")
    first, mixed = media.load_subtitles(f)
    assert (first.speaker, first.text, first.original_text) == ("阿明", "你好", "<v 阿明>你好")
    assert not mixed.speaker
    assert mixed.original_text == "<v 甲>係</v> <v 乙>咩</v>"


def test_speech_asset_setup_verifies_archive_and_cancellation(tmp_path, monkeypatch):
    import hashlib
    import io
    import tarfile
    from tingmu import speech_assets

    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:bz2") as archive:
        for file, content in {"model.onnx": b"test model", "LICENSE": b"license"}.items():
            member = tarfile.TarInfo("fixture/" + file)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    data = packed.getvalue()
    model_spec = ("pyannote", "tag", "fixture", hashlib.sha256(data).hexdigest(),
                  {"model.onnx": 10, "LICENSE": 7})
    monkeypatch.setattr(speech_assets, "MODELS", [model_spec])
    monkeypatch.setattr(speech_assets.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(data))
    messages = []
    with pytest.raises(InterruptedError):
        speech_assets.install(tmp_path, messages.append, cancelled=lambda: len(messages) > 0)
    assert not (tmp_path / "speech-models/pyannote/model.onnx").exists()
    monkeypatch.setattr(speech_assets.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(b"damaged"))
    with pytest.raises(ValueError, match="SHA256"):
        speech_assets.install(tmp_path, messages.append)
    monkeypatch.setattr(speech_assets.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(data))
    speech_assets.install(tmp_path, messages.append)
    assert (tmp_path / "speech-models/pyannote/model.onnx").read_bytes() == b"test model"
    assert (tmp_path / "speech-models/pyannote/LICENSE").read_bytes() == b"license"
    monkeypatch.setattr(speech_assets.urllib.request, "urlopen", lambda *a, **k: pytest.fail("redownload"))
    speech_assets.install(tmp_path, messages.append)


def test_model_status_rejects_truncated_files(tmp_path):
    models = tmp_path / "speech-models"
    for path in ("spleeter/vocals.fp16.onnx", "spleeter/accompaniment.fp16.onnx", "spleeter/LICENSE",
                 "pyannote/model.onnx", "pyannote/LICENSE"):
        f = models / path
        f.parent.mkdir(exist_ok=True, parents=True)
        f.write_bytes(b"partial")
    assert speech.speech_model_status(tmp_path) == {"bgm": "spectral-background", "speaker": "unavailable"}


def test_worker_exports_numpy_alignment_and_confidence_as_json(tmp_path, monkeypatch):
    import sys
    import json
    words = [SimpleNamespace(start=np.float32(1), end=np.float32(1.5), word="你？"),
             SimpleNamespace(start=np.float32(2), end=np.float32(2.5), word="好。")]
    seg = SimpleNamespace(start=words[0].start, end=words[-1].end, words=words, text="你？好。",
                          avg_logprob=np.float32(-1.1), no_speech_prob=.2, compression_ratio=1)
    class Engine:
        supported_languages = ["yue"]

        def __init__(self, *a, **k):
            pass

        def transcribe(self, audio, *, word_timestamps, **kwargs):
            assert word_timestamps
            return iter([seg]), SimpleNamespace(duration=3, language="yue")

    monkeypatch.setitem(sys.modules, "ctranslate2", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Engine))
    monkeypatch.setattr(media, "_read_pcm_audio", lambda path: None)
    monkeypatch.setattr(media, "_register_cuda_dlls", lambda: None)
    request = {"device": "cpu", "model": "large-v3-turbo", "model_cache": str(tmp_path), "language": "yue",
               "audio": "dummy", "start": 180, "source": "fixture", "result": str(tmp_path / "result.json"),
               "speaker_changes": [1.7], "raw_output": str(tmp_path / "raw.json")}
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request), encoding="utf8")
    assert media._asr_worker(path) == 0
    cues = json.loads((tmp_path / "result.json").read_text("utf8"))
    audit = json.loads((tmp_path / "raw.json").read_text("utf8"))["segments"]
    assert [c["uncertain"] for c in cues] == [True, True]
    assert [(c["start"], c["end"]) for c in cues] == [(181, 181.5), (182, 182.5)]
    assert audit[0]["text"] == "你？好。" and audit[0]["cue_ids"] == [c["id"] for c in cues]


@pytest.mark.parametrize("ext", ["vtt", "ass", "ssa"])
def test_export_roles_roundtrip_preserves_separate_events(tmp_path, ext):
    path = tmp_path / ("roles." + ext)
    role = "Alice & <Bob>" if ext != "vtt" else "Alice,<Bob> & {A}\\nB"
    words = "係 & <v 假名>呀 {符號}。\n好！" if ext == "vtt" else "係呀。\n好！"
    media.save_subtitles([Cue("a", 1, 2, words, speaker=role), Cue("b", 2, 3, "再見", speaker="乙")], path)
    result = media.load_subtitles(path)
    assert [(x.speaker, x.text, x.start, x.end) for x in result] == [
        (role, words, 1, 2), ("乙", "再見", 2, 3)]
    if ext == "vtt":
        assert "&lt;Bob&gt;" in path.read_text("utf8")


@pytest.mark.parametrize(("ext", "speaker"), [("ass", "夫妻,朋友"), ("ssa", "甲\nDialogue:"),
                                               ("vtt", "阿明\n00:00:01.000 -->")])
def test_invalid_role_keeps_export_target_intact(tmp_path, ext, speaker):
    path = tmp_path / ("roles." + ext)
    path.write_bytes(b"previous file")
    with pytest.raises(media.MediaError):
        media.save_subtitles([Cue("a", 1, 2, "你好", speaker=speaker)], path)
    assert path.read_bytes() == b"previous file"


def test_concurrent_model_install_downloads_once_across_processes(tmp_path):
    import hashlib
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import io
    import subprocess
    import sys
    import tarfile
    import threading
    import time

    packed = io.BytesIO()
    with tarfile.open(fileobj=packed, mode="w:bz2") as archive:
        for name, content in [("model.onnx", b"fixture"), ("LICENSE", b"license")]:
            member = tarfile.TarInfo("fixture/" + name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    data = packed.getvalue()
    reqs = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            pass

        def do_GET(self):
            reqs.append(self.path)
            self.send_response(200)
            self.end_headers()
            time.sleep(.35)
            self.wfile.write(data)

    code = """
import sys
from pathlib import Path
from tingmu import speech_assets as s
s.RELEASE = sys.argv[2]
s.MODELS = [('pyannote', 'release', 'fixture', sys.argv[3], {'model.onnx': 7, 'LICENSE': 7})]
s.install(Path(sys.argv[1]), progress=lambda message: None)
"""
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        args = [sys.executable, "-c", code, str(tmp_path), f"http://127.0.0.1:{server.server_port}/",
                hashlib.sha256(data).hexdigest()]
        try:
            with subprocess.Popen(args) as first, subprocess.Popen(args) as second:
                assert first.wait(timeout=15) == second.wait(timeout=15) == 0
        finally:
            server.shutdown()
            thread.join(timeout=5)
    assert reqs == ["/release/fixture.tar.bz2"]
    assert (tmp_path / "speech-models/pyannote/model.onnx").read_bytes() == b"fixture"
