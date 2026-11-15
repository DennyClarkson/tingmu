from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pytest

from tingmu.models import Cue, LanguagePair, StudyDocument
from tingmu.storage import Store, atomic_json
from tingmu.portable_paths import MANIFEST


def portable_store(tmp_path, monkeypatch):
    import tingmu.runtime as runtime
    root = [tmp_path / "Original"]
    root[0].mkdir()
    monkeypatch.setattr(runtime, "portable_root", lambda: root[0])
    return root, Store(root[0] / "data")


def test_internal_media_records_follow_two_folder_moves_external_records_unchanged(tmp_path, monkeypatch):
    from tingmu.batch import PreparationQueue
    root, store = portable_store(tmp_path, monkeypatch)
    movie = root[0] / "examples/01.mkv"
    movie.parent.mkdir()
    movie.write_bytes(b"video content")
    subtitle = movie.with_suffix(".srt")
    subtitle.write_text("subtitle", encoding="utf-8")
    external = tmp_path / "Original sibling/22.mkv"
    external.parent.mkdir()
    external.write_bytes(b"external video")
    cue = Cue("original-id", 2, 4, "唔該", original_text="raw original", explanation="ready")
    pair = LanguagePair()
    doc = StudyDocument(str(movie), pair, [cue], metadata={
        "transcript_spec": {"subtitle": str(subtitle)}, "asr_audit_path": str(root[0] / ".cache/audit.json"),
        "stage": "ready", "raw_display_text": {cue.id: str(movie), "path": str(movie)}})
    old_key = store.save_document(doc).name
    fingerprint = doc.metadata["media_fingerprint"].copy()
    external_doc = store.save_document(StudyDocument(str(external), pair, [cue]))
    external_bytes = external_doc.read_bytes()
    store.save_note(str(movie), cue, "internal note", pair)
    store.save_note(str(external), cue, "external note", pair)
    store.save_position(str(movie), 3210)
    store.save_position(str(external), 9876)
    chat = [{"role": "user", "content": "Keep the original words"}]
    store.save_conversation(str(movie), pair, chat)
    store.save_conversation(str(external), pair, chat)
    store.save_conversation("", pair, chat)
    settings = replace(store.load_settings(), library_path=str(movie.parent), cloud_text_enabled=False)
    store.save_settings(settings)
    queue = PreparationQueue(store)
    queue.enqueue([movie], settings, {str(movie): str(subtitle)})
    from tingmu.library import MediaLibrary
    MediaLibrary(store).add([movie, external])
    atomic_json(store.data_dir / "quick-layout.json", {"preferences": {"noteDrafts": {
        str(movie) + "|zh-CN|yue|original-id": "原样保留 " + str(movie),
        str(external) + "|zh-CN|yue|original-id": "外部草稿"}},
        "overlay": {"width": 500, "height": 300}})
    old_media = str(movie)

    for new_name in ("听幕 moved folder", "Second move"):
        destination = tmp_path / new_name
        root[0].rename(destination)
        root[0] = destination
        store = Store(destination / "data")
        movie = destination / "examples/01.mkv"
        subtitle = movie.with_suffix(".srt")
        restored = store.load_document(str(movie), pair)
        assert restored is not None
        assert restored.cues[0].id == "original-id"
        assert restored.cues[0].original_text == "raw original"
        assert restored.metadata["media_fingerprint"] == fingerprint
        assert restored.metadata["transcript_spec"]["subtitle"] == str(subtitle)
        assert restored.metadata["asr_audit_path"] == str(destination / ".cache/audit.json")
        assert restored.metadata["raw_display_text"][cue.id] == old_media  # User/raw text is never rewritten.
        assert restored.metadata["raw_display_text"]["path"] == old_media
        assert store.entries(str(movie))[0]["note"] == "internal note"
        assert store.load_position(str(movie)) == 3210
        assert store.load_conversation(str(movie), pair) == chat
        assert store.load_conversation("", pair) == chat
        assert store.entries(str(external))[0]["note"] == "external note"
        assert store.load_position(str(external)) == 9876
        assert store.load_conversation(str(external), pair) == chat
        assert (store.data_dir / "documents" / external_doc.name).read_bytes() == external_bytes
        assert store.load_settings().library_path == str(movie.parent)
        assert set(MediaLibrary(store).paths) == {str(movie), str(external)}
        layout = json.loads((store.data_dir / "quick-layout.json").read_text("utf-8"))
        assert layout["preferences"]["noteDrafts"][str(movie) + "|zh-CN|yue|original-id"] == "原样保留 " + old_media
        assert layout["preferences"]["noteDrafts"][str(external) + "|zh-CN|yue|original-id"] == "外部草稿"
        assert layout["overlay"] == {"width": 500, "height": 300}
        jobs = PreparationQueue(store)
        assert jobs.snapshot()[0]["media_path"] == str(movie)
        assert jobs.snapshot()[0]["subtitle_path"] == str(subtitle)
        assert jobs.enqueue([movie], store.load_settings(), {str(movie): str(subtitle)}) == []
        assert json.loads((store.data_dir / MANIFEST).read_text(encoding="utf-8"))["root"] == str(destination)
        assert not (store.data_dir / "documents" / old_key).exists()
        old_key = store._document_path(str(movie), pair).name
    backups = list((store.data_dir / "migration-backups").glob("*/study.db"))
    assert len(backups) == 2


def test_document_collision_preserves_both_versions_and_database(tmp_path, monkeypatch):
    root, store = portable_store(tmp_path, monkeypatch)
    old_movie = root[0] / "clip.mkv"
    old_movie.write_bytes(b"clip")
    pair = LanguagePair()
    cue = Cue("a", 0, 1, "Original")
    original = store.save_document(StudyDocument(str(old_movie), pair, [cue]))
    original_bytes = original.read_bytes()
    store.save_note(str(old_movie), cue, "precious note", pair)
    moved = tmp_path / "New"
    root[0].rename(moved)
    root[0] = moved
    # Simulate an independently existing destination cache, without creating a Store yet.
    store.data_dir = moved / "data"
    target = store._document_path(str(moved / "clip.mkv"), pair)
    atomic_json(target, StudyDocument(str(moved / "clip.mkv"), pair, [Cue("b", 0, 1, "Other")]).to_dict())
    target_bytes = target.read_bytes()
    with pytest.raises(ValueError, match="冲突"):
        Store(moved / "data")
    assert (moved / "data/documents" / original.name).read_bytes() == original_bytes
    assert target.read_bytes() == target_bytes
    with sqlite3.connect(moved / "data/study.db") as db:
        assert db.execute("SELECT media_path,note FROM entries").fetchone() == (str(old_movie), "precious note")
    assert list((moved / "data/migration-backups").glob("*/documents/*.json"))


@pytest.mark.parametrize("failure_file", ["settings.json", MANIFEST])
def test_interrupted_json_migration_retries_without_losing_records(tmp_path, monkeypatch, failure_file):
    import tingmu.storage as storage
    root, store = portable_store(tmp_path, monkeypatch)
    movie = root[0] / "clip.mkv"
    movie.write_bytes(b"clip")
    cue = Cue("id", 0, 1, "Keep")
    pair = LanguagePair()
    store.save_document(StudyDocument(str(movie), pair, [cue]))
    store.save_position(str(movie), 456)
    store.save_settings(replace(store.load_settings(), library_path=str(root[0])))
    moved = tmp_path / "New"
    root[0].rename(moved)
    root[0] = moved
    original_atomic = storage.atomic_json

    def failing_write(path, value):
        if Path(path) == moved / "data" / failure_file:
            raise OSError("simulated disk interruption")
        original_atomic(path, value)

    monkeypatch.setattr(storage, "atomic_json", failing_write)
    with pytest.raises(OSError, match="interruption"):
        Store(moved / "data")
    monkeypatch.setattr(storage, "atomic_json", original_atomic)
    restored = Store(moved / "data")
    assert restored.load_document(str(moved / "clip.mkv"), pair).cues[0].id == "id"
    assert restored.load_position(str(moved / "clip.mkv")) == 456
    assert restored.load_settings().library_path == str(moved)


def test_explicit_nonportable_data_store_is_not_migrated(tmp_path, monkeypatch):
    root, _ = portable_store(tmp_path, monkeypatch)
    other = Store(tmp_path / "external-user-data")
    assert not (other.data_dir / MANIFEST).exists()


def test_database_collision_rolls_back_every_table_and_preserves_json(tmp_path, monkeypatch):
    root, store = portable_store(tmp_path, monkeypatch)
    old_movie = root[0] / "clip.mkv"
    old_movie.write_bytes(b"clip")
    cue = Cue("id", 0, 1, "Keep")
    pair = LanguagePair()
    doc = store.save_document(StudyDocument(str(old_movie), pair, [cue]))
    store.save_note(str(old_movie), cue, "note", pair)
    store.save_position(str(old_movie), 111)
    new_root = tmp_path / "Moved"
    store.save_position(str(new_root / "clip.mkv"), 222)
    root[0].rename(new_root)
    root[0] = new_root
    with pytest.raises(ValueError, match="记录冲突"):
        Store(new_root / "data")
    with sqlite3.connect(new_root / "data/study.db") as db:
        assert db.execute("SELECT media_path FROM entries").fetchone()[0] == str(old_movie)
        assert len(db.execute("SELECT * FROM positions").fetchall()) == 2
    assert (new_root / "data/documents" / doc.name).exists()


def test_case_only_folder_rename_rekeys_document(tmp_path, monkeypatch):
    root, store = portable_store(tmp_path, monkeypatch)
    movie = root[0] / "clip.mkv"
    movie.write_bytes(b"clip")
    store.save_document(StudyDocument(str(movie), LanguagePair(), [Cue("id", 0, 1, "Keep")]))
    renamed = root[0].with_name(root[0].name.lower())
    root[0].rename(renamed)
    root[0] = renamed
    moved_store = Store(renamed / "data")
    assert moved_store.load_document(str(renamed / "clip.mkv"), LanguagePair()).cues[0].id == "id"


def test_archived_notebook_source_and_frozen_chat_context_follow_internal_media(tmp_path, monkeypatch):
    from tingmu.conversations import ConversationStore
    from tingmu.notebooks import NotebookManager
    root, store = portable_store(tmp_path, monkeypatch)
    movie = root[0] / "clip.mkv"
    movie.touch()
    external = tmp_path / "external.mkv"
    external.touch()
    language = LanguagePair()
    cue = Cue("source", 0, 1, "原文")
    store.save_note(str(movie), cue, "个人备注", language)
    entry = store.entries()[0]
    notebook = NotebookManager(store)
    notebook.append_note("yue", "来源笔记", "路径字面量不改：" + str(movie), entry_ids=[entry["id"]])
    before_markdown = notebook.snapshot("yue")["markdown"]
    store.delete_entry(entry["id"])
    chats = ConversationStore(store)
    session = chats.create(language, str(movie))
    chats.save(session["id"], [{"role": "user", "content": "保留文本 " + str(movie),
        "request_context": {"media_path": str(movie), "selected_media": [str(movie), str(external)]}}])
    new_root = tmp_path / "Moved"
    root[0].rename(new_root)
    root[0] = new_root
    moved = Store(new_root / "data")
    after = NotebookManager(moved)
    assert after.source_entry(entry["id"])["media_path"] == str(new_root / "clip.mkv")
    assert after.snapshot("yue")["markdown"] == before_markdown
    saved = ConversationStore(moved).load(session["id"])["messages"][0]
    assert saved["content"] == "保留文本 " + str(movie)
    assert saved["request_context"] == {"media_path": str(new_root / "clip.mkv"),
                                        "selected_media": [str(new_root / "clip.mkv"), str(external)]}
