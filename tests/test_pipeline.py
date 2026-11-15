from dataclasses import replace
import json
import pytest

from tingmu.models import AppSettings, Cue, Token
from tingmu.storage import Store
import tingmu.pipeline as pipeline


def sample(tmp_path):
    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"sample")
    return movie, Store(tmp_path / "data")


def test_local_mode_never_calls_provider_and_reuses_transcript(tmp_path, monkeypatch):
    movie, store = sample(tmp_path)
    calls = []
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **k: calls.append(1) or [Cue("1", 0, 2, "Hello")])
    monkeypatch.setattr(pipeline, "annotate_local", lambda cues, lang: [replace(c, tokens=[Token(c.text)]) for c in cues])
    monkeypatch.setattr(pipeline, "LearningAgent", lambda **k: pytest.fail("Local mode attempted cloud API"))
    settings = AppSettings(provider="openai", cloud_text_enabled=False)
    doc = pipeline.prepare_media(movie, settings, store)
    assert doc.provider == "local"
    assert doc.metadata["stage"] == "ready"
    assert pipeline.prepare_media(movie, settings, store).cues[0].tokens[0].text == "Hello"
    assert len(calls) == 1


def test_cloud_failure_preserves_transcript_and_resumes_completed_chunks(tmp_path, monkeypatch):
    movie, store = sample(tmp_path)
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **k: [Cue("1", 0, 1, "A"), Cue("2", 1, 2, "B")])

    class FailingAgent:
        def __init__(self, **kwargs):
            pass

        def annotate(self, cues, language, **kwargs):
            kwargs["checkpoint"]([replace(cues[0], explanation="saved")])
            raise RuntimeError("simulated outage")

    monkeypatch.setattr(pipeline, "LearningAgent", FailingAgent)
    settings = AppSettings(review_enabled=False)
    with pytest.raises(RuntimeError):
        pipeline.prepare_media(movie, settings, store)
    partial = store.load_document(str(movie), settings.language)
    assert partial.metadata["stage"] == "partial"
    assert partial.cues[0].explanation == "saved"
    assert partial.cues[1].original_text == "B"

    class ResumeAgent(FailingAgent):
        def annotate(self, cues, language, **kwargs):
            assert [c.id for c in cues] == ["2"]
            return [replace(cues[0], explanation="resumed")]

    monkeypatch.setattr(pipeline, "LearningAgent", ResumeAgent)
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **k: pytest.fail("Transcription repeated"))
    result = pipeline.prepare_media(movie, settings, store)
    assert [c.explanation for c in result.cues] == ["saved", "resumed"]
    assert result.metadata["stage"] == "ready"


def test_audio_track_change_invalidates_cache(tmp_path, monkeypatch):
    movie, store = sample(tmp_path)
    tracks = []
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **k: tracks.append(k["audio_track"]) or [Cue("1", 0, 2, "Hello")])
    monkeypatch.setattr(pipeline, "annotate_local", lambda cues, lang: cues)
    settings = AppSettings(provider="local", review_enabled=False)
    pipeline.prepare_media(movie, settings, store)
    pipeline.prepare_media(movie, replace(settings, audio_track=1), store)
    assert tracks == [0, 1]


def test_fresh_asr_preserves_runtime_and_alignment_warning(tmp_path, monkeypatch):
    movie, store = sample(tmp_path)
    warning = {"start": 30.0, "reason": "No usable alignment"}

    def transcribe(*args, metadata_out, **kwargs):
        metadata_out.update(asr_device="cuda", asr_audit_path="local-audit.json",
                            alignment_warnings=[warning])
        return [Cue("1", 0, 2, "Hello")]

    monkeypatch.setattr(pipeline, "transcribe", transcribe)
    monkeypatch.setattr(pipeline, "annotate_local", lambda cues, lang: cues)
    result = pipeline.prepare_media(movie, AppSettings(provider="local", review_enabled=False), store)
    saved = store.load_document(str(movie), result.language)
    assert saved.metadata["asr_device"] == "cuda"
    assert saved.metadata["asr_audit_path"] == "local-audit.json"
    assert saved.metadata["alignment_warnings"] == [warning]


def test_sidecar_and_cancellation(tmp_path, monkeypatch):
    movie, store = sample(tmp_path)
    (tmp_path / "movie.yue.srt").write_text("1\n00:00:01,000 --> 00:00:03,000\n你好\n", encoding="utf-8")
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **k: pytest.fail("Sidecar ignored"))
    monkeypatch.setattr(pipeline, "annotate_local", lambda cues, lang: cues)
    result = pipeline.prepare_media(movie, AppSettings(provider="local", review_enabled=False), store)
    assert result.cues[0].text == "你好"
    with pytest.raises(pipeline.PreparationCancelled):
        pipeline.prepare_media(movie, AppSettings(provider="local", review_enabled=False), store, cancelled=lambda: True)


def test_model_change_failure_cannot_reuse_old_completed_ids(tmp_path, monkeypatch):
    movie, store = sample(tmp_path)
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **k: [Cue("1", 0, 2, "Hello")])
    monkeypatch.setattr(pipeline, "annotate_local", lambda cues, lang: cues)
    pipeline.prepare_media(movie, AppSettings(provider="local", review_enabled=False), store)
    attempts = []

    class NewAgent:
        def __init__(self, **kwargs):
            pass

        def annotate(self, cues, language, **kwargs):
            attempts.append([c.id for c in cues])
            if len(attempts) == 1:
                raise RuntimeError("provider outage before first checkpoint")
            return [replace(c, translation="你好") for c in cues]

    monkeypatch.setattr(pipeline, "LearningAgent", NewAgent)
    with pytest.raises(RuntimeError):
        pipeline.prepare_media(movie, AppSettings(review_enabled=False), store)
    partial = store.load_document(str(movie), AppSettings(review_enabled=False).language)
    assert partial.metadata["completed_ids"] == []
    assert partial.metadata["annotated_count"] == 0
    done = pipeline.prepare_media(movie, AppSettings(review_enabled=False), store)
    assert attempts == [["1"], ["1"]]
    assert done.cues[0].translation == "你好"


def test_actual_agent_metadata_contract_is_persisted(tmp_path, monkeypatch):
    """Exercise the real coordinator; fake only network completion, not agent shape."""
    movie, store = sample(tmp_path)
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **k: [Cue("1", 0, 2, "Hello")])

    def complete(self, system, payload, schema=None):
        return json.dumps({"cues": [{"id": c["id"], "text": c["text"], "translation": "你好",
                                    "explanation": "问候", "uncertain": False,
                                    "tokens": [{"text": c["text"], "reading": "", "meaning": "你好", "learn": True}]}
                                   for c in payload["cues"]]})

    monkeypatch.setattr(pipeline.LearningAgent, "_complete", complete)
    document = pipeline.prepare_media(movie, AppSettings(review_enabled=False), store)
    assert document.metadata["stage"] == "ready"
    assert document.metadata["completed_ids"] == ["1"]
    assert document.metadata["analysis_batches"][0]["status"] == "completed"
    assert document.metadata["checkpoint_events"][-1]["final_retry_attempted"] is False
    assert store.load_document(str(movie), AppSettings(review_enabled=False).language).cues[0].translation == "你好"
