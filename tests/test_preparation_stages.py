"""Stage extraction retains request isolation and failure checkpoint boundaries."""

import pytest

from tingmu import pipeline
from tingmu.models import AppSettings, Cue
from tingmu.storage import Store


def test_preparation_freezes_settings_before_acquisition(tmp_path, monkeypatch):
    media = tmp_path / "clip.mkv"
    media.touch()
    settings = AppSettings(provider="local", review_enabled=False, target_language="en", suppress_bgm=False)
    store = Store(tmp_path / "study")

    def transcribe(*args, **kwargs):
        assert kwargs["language"] == "en" and not kwargs["suppress_bgm"]
        settings.target_language = "yue"
        settings.suppress_bgm = True
        return [Cue("raw-one", 1.25, 2.75, "Original text")]

    monkeypatch.setattr(pipeline, "transcribe", transcribe)
    monkeypatch.setattr(pipeline, "annotate_local", lambda cues, language: cues)
    document = pipeline.prepare_media(media, settings, store)
    assert document.language.target == "en"
    assert document.metadata["transcript_spec"]["target"] == "en"
    assert not document.metadata["transcript_spec"]["suppress_bgm"]
    assert document.metadata["annotation_spec"]["language"]["target"] == "en"
    assert settings.target_language == "yue" and settings.suppress_bgm


def test_initial_teaching_checkpoint_failure_preserves_original_exception(tmp_path, monkeypatch):
    media = tmp_path / "clip.mkv"
    media.touch()
    store = Store(tmp_path / "study")
    settings = AppSettings(provider="local", review_enabled=False)
    monkeypatch.setattr(pipeline, "transcribe", lambda *args, **kwargs: [Cue("raw-one", 1, 2, "Hello")])
    original = store.save_document
    error = OSError("checkpoint unavailable")
    saves = []

    def save(document, **kwargs):
        saves.append(document.metadata["stage"])
        if len(saves) == 2:
            raise error
        return original(document, **kwargs)

    monkeypatch.setattr(store, "save_document", save)
    with pytest.raises(OSError) as caught:
        pipeline.prepare_media(media, settings, store)
    assert caught.value is error
    assert saves == ["transcribed", "annotating"]
    saved = store.load_document(str(media), settings.language)
    assert saved.metadata["stage"] == "transcribed"
    assert [(c.id, c.start, c.end, c.original_text) for c in saved.cues] == [("raw-one", 1, 2, "Hello")]


def test_local_teaching_failure_checkpoints_source_and_stage(tmp_path, monkeypatch):
    media = tmp_path / "clip.mkv"
    media.touch()
    store = Store(tmp_path / "study")
    settings = AppSettings(provider="local", review_enabled=False)
    monkeypatch.setattr(pipeline, "transcribe", lambda *args, **kwargs: [Cue("raw-one", 1, 2, "Hello")])

    def fail(cues, language):
        raise ValueError("synthetic annotation failure")

    monkeypatch.setattr(pipeline, "annotate_local", fail)
    with pytest.raises(ValueError, match="synthetic annotation failure"):
        pipeline.prepare_media(media, settings, store)
    saved = store.load_document(str(media), settings.language)
    assert saved.metadata["stage"] == "partial"
    assert saved.metadata["stage_code"] == "local"
    assert saved.metadata["error"] == "ValueError"
    assert saved.metadata["raw_display_text"] == {"raw-one": "Hello"}
    assert [(c.id, c.start, c.end, c.original_text) for c in saved.cues] == [("raw-one", 1, 2, "Hello")]
