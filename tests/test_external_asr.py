from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import wave

import pytest

from tingmu import external_asr, media
from tingmu.batch import PreparationQueue
from tingmu.models import AppSettings
from tingmu.pipeline import prepare_media
from tingmu.providers import ProviderError
from tingmu.storage import Store


@pytest.fixture
def server():
    posts = []

    class Receiver(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/v1/models":
                self.send_error(404)
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"data":[{"id":"local-speech-model"}]}')

        def do_POST(self):
            posts.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"segments": [
                {"start": 0.0, "end": 0.1, "text": "Hello.", "speaker": "A"},
                {"start": 0.1, "end": 0.2, "text": "Hi.", "speaker": "B"}]}).encode())

        def log_message(self, *args):
            pass

    service = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    worker = threading.Thread(target=service.serve_forever, daemon=True)
    worker.start()
    yield f"http://127.0.0.1:{service.server_port}/v1/audio/transcriptions", posts
    service.shutdown()
    worker.join()
    service.server_close()


def test_external_pipeline_sends_only_prepared_audio_and_keeps_turns(tmp_path, monkeypatch, server):
    endpoint, posts = server
    movie = tmp_path / "private-name.mkv"
    movie.write_bytes(b"PRIVATE-VIDEO-TRACK")
    wav = tmp_path / "speech.wav"
    with wave.open(str(wav), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(bytes(16000 * 2))
    seen = []

    @contextmanager
    def prepared(path, **kwargs):
        seen.append(kwargs)
        yield wav

    monkeypatch.setattr(media, "prepared_audio", prepared, raising=False)
    store = Store(tmp_path / "workspace")
    settings = AppSettings(asr_backend="external", asr_endpoint=endpoint, allow_audio_upload=True,
                           asr_key_env="", external_asr_model="test-diarize", asr_response_format="diarized_json",
                           target_language="en", cloud_text_enabled=False)
    doc = prepare_media(movie, settings, store, start=8, duration=1)
    assert len(posts) == 1 and seen[0]["suppress_bgm"]
    assert b"RIFF" in posts[0] and b"diarized_json" in posts[0] and b"chunking_strategy" in posts[0]
    assert movie.name.encode() not in posts[0] and movie.read_bytes() not in posts[0]
    assert [(c.start, c.end, c.speaker) for c in doc.cues] == [(8, 8.1, "A"), (8.1, 8.2, "B")]
    assert store.load_document(str(movie), settings.language).cues[1].speaker == "B"
    prepare_media(movie, settings, store, start=8, duration=1)
    assert len(posts) == 1
    queue = PreparationQueue(store)
    queue.enqueue([movie], settings)
    store.save_settings(replace(settings, allow_audio_upload=False))
    result = queue.run()
    assert result[0]["status"] == "failed" and "允许" in result[0]["error_message"]
    assert len(posts) == 1 and len(seen) == 1


def test_chunks_preserve_samples_and_timeline(tmp_path, monkeypatch):
    import io
    import numpy as np

    path = tmp_path / "long.wav"
    pcm = np.random.default_rng(42).integers(-32000, 32000, size=16000 * 3, dtype=np.int16).tobytes()
    with wave.open(str(path), "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        writer.writeframes(pcm)
    monkeypatch.setattr(external_asr, "CHUNK_SECONDS", 1)
    received = bytearray()
    for start, seconds, blob, multiple in external_asr._wav_chunks(path):
        assert multiple and start == len(received) / 32000 and seconds <= 1
        with wave.open(io.BytesIO(blob)) as reader:
            received.extend(reader.readframes(reader.getnframes()))
    assert received == pcm


@pytest.mark.parametrize("backend", ["local_api", "external"])
def test_api_chunk_cut_uses_only_original_for_timing_and_only_prepared_for_upload(tmp_path, monkeypatch, backend):
    import io
    import numpy as np

    rate = 16000
    source = tmp_path / "original.wav"
    payload = tmp_path / "foreground.wav"
    mix = np.full(rate * 12, 11000, dtype="<i2")
    mix[int(6.8*rate):int(7.4*rate)] = 0
    mix[int(7.85*rate):int(7.90*rate)] = 0  # An isolated 50 ms dip is not a pause.
    masked = np.full_like(mix, 2500)
    for path, samples in [(source, mix), (payload, masked)]:
        with wave.open(str(path), "wb") as writer:
            writer.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            writer.writeframes(samples.tobytes())

    @contextmanager
    def prepare(_path, **kwargs):
        assert kwargs["suppress_bgm"] and not kwargs["cancelled"]()
        kwargs["original_out"]["path"] = source
        yield payload

    received = []
    def post(_endpoint, _key, _fields, audio):
        assert len(audio) <= external_asr.MAX_WAV_BYTES
        with wave.open(io.BytesIO(audio)) as reader:
            received.append(reader.readframes(reader.getnframes()))
        return {"segments": [{"start": 0., "end": .2, "text": " Hi! ", "speaker": "A"},
                             {"start": .2, "end": .4, "text": " ", "speaker": "A"}]}

    monkeypatch.setattr(media, "prepared_audio", prepare)
    monkeypatch.setattr(external_asr, "_post", post)
    monkeypatch.setattr(external_asr, "MAX_WAV_BYTES", 44 + rate * 2 * 8)
    settings = AppSettings(asr_backend=backend, asr_endpoint="https://example.invalid/v1/audio/transcriptions",
                           allow_audio_upload=backend == "external", asr_key_env="", local_asr_key_env="",
                           local_asr_response_format="diarized_json", asr_response_format="diarized_json",
                           suppress_bgm=True)
    metadata = {}
    rows = external_asr.transcribe_external(source, settings, tmp_path, start=100, metadata_out=metadata)
    assert len(received) == len(rows) == 2 and b"".join(received) == masked.tobytes()
    assert 7.0 <= len(received[0]) / (2 * rate) <= 7.2
    assert [r.speaker for r in rows] == ["1·A", "2·A"]
    assert (rows[1].start, rows[1].original_text) == (100 + len(received[0]) / (2 * rate), " Hi! ")
    assert metadata["asr_segment_sources"][rows[1].id] == {
        "chunk": 2, "segment_index": 0, "offset": rows[1].start, "reported_start": 0., "reported_end": .2}
    assert metadata["asr_quiet_intervals"] == [[106.8, 107.4]]
    assert len(metadata["asr_blank_segments"]) == 2


def test_wav_chunk_tail_and_truncated_data(tmp_path, monkeypatch):
    path = tmp_path / "tail.wav"
    with wave.open(str(path), "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        writer.writeframes(bytes(16001*2))
    monkeypatch.setattr(external_asr, "MAX_WAV_BYTES", 32044)
    chunks = list(external_asr._wav_chunks(path))
    assert len(chunks) == 2 and sum(len(wav)-44 for _, _, wav, _ in chunks) == 32002
    assert all(44 < len(wav) <= 32044 for _, _, wav, _ in chunks)
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(ProviderError, match="不完整"):
        list(external_asr._wav_chunks(path))


def test_audio_permission_revoked_stops_following_uploads(tmp_path, monkeypatch):
    source = tmp_path / "track.wav"
    with wave.open(str(source), "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        writer.writeframes(bytes(3*32000))

    @contextmanager
    def prepare(*args, **kwargs):
        yield source

    posts = []
    monkeypatch.setattr(media, "prepared_audio", prepare)
    monkeypatch.setattr(external_asr, "CHUNK_SECONDS", 1)
    monkeypatch.setattr(external_asr, "_post", lambda *args: posts.append(True) or {"segments": []})
    settings = AppSettings(asr_backend="external", asr_endpoint="https://example.invalid/v1/audio/transcriptions",
                           allow_audio_upload=True, asr_key_env="")
    with pytest.raises(ProviderError, match="允许"):
        external_asr.transcribe_external(source, settings, tmp_path, permission=lambda: not posts)
    assert posts == [True]


def test_local_api_without_remote_permission_or_proxy(tmp_path, monkeypatch, server):
    endpoint, posts = server
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    settings = AppSettings(asr_backend="local_api", local_asr_endpoint=endpoint,
                           local_asr_api_model="local-speech-model", local_asr_response_format="diarized_json",
                           allow_audio_upload=False, cloud_text_enabled=False)
    assert external_asr.list_models(settings, tmp_path) == ["local-speech-model"]
    wav = tmp_path / "speech.wav"
    with wave.open(str(wav), "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        writer.writeframes(bytes(32000))
    @contextmanager
    def processed(*args, **kwargs):
        yield wav
    monkeypatch.setattr(media, "prepared_audio", processed)
    result = external_asr.transcribe_external(wav, settings, tmp_path)
    assert [cue.speaker for cue in result] == ["A", "B"] and len(posts) == 1
    assert b"local-speech-model" in posts[0]
    with pytest.raises(ValueError, match="本机"):
        external_asr.list_models(replace(settings, local_asr_endpoint="https://example.org/v1/audio/transcriptions"), tmp_path)


@pytest.mark.parametrize("payload", [
    {"text": "missing times"}, {"segments": [{"start": True, "end": 1, "text": "x"}]},
    {"segments": [{"start": 1, "end": 80, "text": "x"}]},
    {"segments": [{"start": 0, "end": 1, "text": "x"}]},
])
def test_bad_timing_or_missing_requested_speakers_fails(payload):
    with pytest.raises(ProviderError):
        external_asr._segments(payload, source="x", offset=5, duration=3, namespace="", diarized=True)


def test_endpoint_and_secret_validation(tmp_path):
    with pytest.raises(ValueError):
        external_asr.validate_speech_setting("asr_endpoint", "https://user:secret@service.test/asr")
    with pytest.raises(ValueError):
        external_asr.validate_speech_setting("asr_key_env", "test api key")
    with pytest.raises(ProviderError):
        external_asr.save_audio_key(tmp_path, "http://127.0.0.1/asr", "key")
    assert not (tmp_path / "audio-credential.json").exists()


@pytest.mark.parametrize("seconds,expected_display", [
    (2, ["Come ", "on!"]), (1, ["Come on!"]),
])
def test_teaching_only_reuses_user_correction_without_audio_upload(tmp_path, monkeypatch, seconds, expected_display):
    from tingmu.timeline import SubtitleTimeline

    movie = tmp_path / "lesson.mp4"
    movie.write_bytes(b"video")
    sidecar = movie.with_suffix(".srt")
    sidecar.write_text(f"1\n00:00:00,000 --> 00:00:0{seconds},000\nCome in!\n", encoding="utf-8")
    store = Store(tmp_path / "workspace")
    settings = AppSettings(asr_backend="external", allow_audio_upload=False, cloud_text_enabled=False,
                           target_language="en")
    doc = prepare_media(movie, settings, store)
    cue = doc.cues[0]
    doc.cues[0] = replace(cue, text="Come on!", segmented_texts=["Come ", "on!"])
    doc.metadata["cue_corrections"] = {cue.id: {"text": "Come on!", "segmented_texts": ["Come ", "on!"]}}
    store.save_document(doc)
    queue = PreparationQueue(store)
    job = queue.enqueue([movie], settings)[0]
    queue.reset([job["id"]], "teaching", requeue=True, settings=replace(settings, asr_backend="local"))
    sidecar.unlink()
    monkeypatch.setattr(external_asr, "transcribe_external", lambda *a, **k: pytest.fail("unexpected upload"))
    assert queue.run()[0]["status"] == "completed"
    updated = store.load_document(str(movie), settings.language)
    assert updated.cues[0].original_text == "Come in!"
    assert updated.cues[0].text == "Come on!"
    assert updated.cues[0].segmented_texts == ["Come ", "on!"]
    assert [c.text for c in SubtitleTimeline(updated.cues).cues] == expected_display
