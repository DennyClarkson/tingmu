import os
from pathlib import Path

import pytest

from tingmu import local_whisper
from tingmu.export_paths import export_path
from tingmu.models import AppSettings
from tingmu.pipeline import prepare_media
from tingmu.storage import Store


def test_existing_model_is_shared_across_workspaces(tmp_path, monkeypatch):
    from tingmu.models import Cue
    cache = tmp_path / "shared-cache"
    folder = cache / "models" / "custom-en"
    folder.mkdir(parents=True)
    for filename in ("model.bin", "config.json", "tokenizer.json"):
        (folder / filename).write_bytes(b"test")
    monkeypatch.setattr(local_whisper, "cache_root", lambda: cache)
    ref = local_whisper.model_reference(folder)
    assert ref == "local:custom-en"
    assert local_whisper.resolve_local_model(ref) == folder
    calls = []
    def transcribe(path, **options):
        calls.append(Path(options["model"]))
        return [Cue("a", 0, 2, "hello")]
    monkeypatch.setattr("tingmu.pipeline.transcribe", transcribe)
    movie = tmp_path / "lesson.mkv"
    movie.touch()
    for label in ("en", "fr"):
        store = Store(tmp_path / label)
        settings = AppSettings(asr_model=ref, target_language=label, text_models_enabled=False)
        prepare_media(movie, settings, store)
        assert not (store.data_dir / "models").exists()
    assert calls == [folder, folder]
    with pytest.raises(ValueError):
        local_whisper.resolve_local_model("local:../elsewhere")


def test_exports_stay_in_current_workspace(tmp_path):
    workspace = tmp_path / "study"
    dest = export_path(workspace, "学习记录.md", (".md",))
    assert dest == workspace / "exports/学习记录.md"
    outside = tmp_path / "private.md"
    outside.write_text("original")
    for path in (outside, workspace / "exports/../x.md", workspace / "exports/media.db"):
        with pytest.raises(ValueError):
            export_path(workspace, path, (".md",))
    os.link(outside, dest)
    with pytest.raises(ValueError):
        export_path(workspace, dest, (".md",))
    assert outside.read_text() == "original"
