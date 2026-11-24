"""Regression checks for batched reads and publication boundaries."""

from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from tingmu import batch
from tingmu.database import Database
from tingmu.conversations import ConversationStore
from tingmu.models import AppSettings, Cue, LanguagePair, StudyDocument
from tingmu.notebooks import NotebookManager
from tingmu.storage import Store
from tingmu.task_progress import Progress


def test_v1_upgrade_adds_hot_indexes_and_sets_each_lease_pragmas(tmp_path):
    path = tmp_path / "study.db"
    Database(path).close()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP INDEX entries_target_id")
        db.execute("PRAGMA user_version=1")
        db.execute("INSERT INTO positions VALUES ('kept',42)")
    with closing(Database(path)) as upgraded:
        for _ in range(2):
            with upgraded.connection() as db:
                assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
                assert db.execute("PRAGMA synchronous").fetchone()[0] == 1
                assert db.execute("PRAGMA temp_store").fetchone()[0] == 2
                assert db.execute("PRAGMA cache_size").fetchone()[0] == -16000
                assert db.execute("SELECT milliseconds FROM positions").fetchone()[0] == 42
                plan = db.execute("EXPLAIN QUERY PLAN SELECT * FROM entries WHERE target_language=? ORDER BY id",
                                  ("en",)).fetchall()
                assert any("entries_target_id" in row[3] for row in plan)


def test_sync_loads_entries_once_and_releases_batched_handle(tmp_path):
    with closing(Store(tmp_path)) as store:
        manager = NotebookManager(store)
        store.save_note("example.mkv", Cue("one", 1, 2, "hello"), "", LanguagePair())
        with (patch.object(manager, "_entries", wraps=manager._entries) as reads,
              patch("sqlite3.connect", wraps=sqlite3.connect) as connections):
            snapshot = manager.sync("yue")
        assert connections.call_count == reads.call_count == 1
        assert snapshot["stats"]["due"] == len(snapshot["practice"]) == 1
        assert snapshot["practice"] == manager.practice("yue")


def test_legacy_projection_preserves_order_with_one_python_message_serialization(tmp_path):
    with closing(Store(tmp_path)) as store:
        chats = ConversationStore(store)
        sid = chats.create(LanguagePair())["id"]
        messages = [{"role": "user", "content": '{"quoted":true}'},
                    {"role": "assistant", "content": "第一句\n⭐", "reasoning_text": "公开思考"}]
        with (patch("json.dumps", wraps=json.dumps) as dumps, patch("json.loads", wraps=json.loads) as loads):
            saved = chats.save_with_legacy(sid, messages)
            assert dumps.call_count == 1 and loads.call_count == 0
        assert saved["messages"][1]["reasoning_text"] == "公开思考"
        assert store.load_conversation("", LanguagePair()) == [
            {"role": item["role"], "content": item["content"]} for item in messages]
        chats.save_with_legacy(sid, [])
        assert store.load_conversation("", LanguagePair()) == []


def test_markdown_reuses_displayed_book_blocks_and_observes_external_edit(tmp_path):
    from tingmu import richtext
    from tingmu.quick.learning_controller import LearningController

    with closing(Store(tmp_path)) as store:
        manager = NotebookManager(store)
        initial = manager.sync("en")
        controller = LearningController(
            SimpleNamespace(language="en", notebooks={}),
            SimpleNamespace(is_closed=lambda: False, notebook_manager=lambda: manager, notebooksChanged=Mock(),
                            refresh_entries=Mock()),
        )
        with patch.object(richtext, "markdown_blocks", wraps=richtext.markdown_blocks) as parse:
            controller.refresh()
            controller.refresh()
            assert parse.call_count == 1
            Path(initial["path"]).write_text(initial["markdown"] + "\nexternal text", encoding="utf-8")
            controller.refresh()
            assert parse.call_count == 2
            assert any("external text" in block["text"] for block in controller.state.notebooks["blocks"])


def test_checkpoints_reuse_guard_until_another_store_replaces_document(tmp_path):
    with closing(Store(tmp_path)) as store, closing(Store(tmp_path)) as other:
        doc = StudyDocument(str(tmp_path / "clip.mkv"), LanguagePair(), [Cue("one", 0, 1, "Old")],
                            metadata={"stage": "ready"})
        path = store.save_document(doc)
        srt = path.with_suffix(".srt")
        published = srt.read_bytes()
        doc.cues[0].text = "New"
        doc.metadata["stage"] = "annotating"
        with (patch.object(store, "_render_srt", wraps=store._render_srt) as render,
              patch.object(Path, "read_bytes", side_effect=AssertionError("reread a known checkpoint"))):
            store.save_document(doc)
            assert render.call_count == 0
        assert srt.read_bytes() == published
        store.save_document(doc, publish_subtitles=True)
        assert "New" in srt.read_text(encoding="utf-8")
        corrected = deepcopy(doc)
        corrected.metadata["cue_corrections"] = {"one": {"revision": "other writer"}}
        other.save_document(corrected, publish_subtitles=True)
        with pytest.raises(ValueError, match="逐句修订"):
            store.save_document(doc)
        assert store.load_document(doc.media_path, doc.language).metadata == corrected.metadata


def test_plan_transition_and_unreadable_cache_retain_original_bytes(tmp_path):
    with closing(Store(tmp_path)) as store:
        doc = StudyDocument(str(tmp_path / "clip.mkv"), LanguagePair(), [], metadata={"stage": "transcribed"})
        path = store.save_document(doc)
        old = path.read_bytes()
        doc.metadata["subtitle_plan"] = {"identity": "source"}
        store.save_document(doc)
        path.write_bytes(b"interrupted external edit")
        store.save_document(doc)
        backups = {backup.read_bytes() for backup in (tmp_path / "material-backups").glob("*/*.json")}
        assert backups == {old, b"interrupted external edit"}


def test_queue_keeps_counter_updates_live_and_fsyncs_stage_and_terminal_states(tmp_path, monkeypatch):
    with closing(Store(tmp_path / "study")) as store:
        queue = batch.PreparationQueue(store)
        media = tmp_path / "clip.mkv"
        media.touch()
        queue.enqueue([media], AppSettings())

        def durable():
            return json.loads(queue.path.read_bytes())["jobs"][0]

        def prepare(path, settings, store, *, progress, **kwargs):
            assert durable()["status"] == "running"
            progress(Progress("stage", stage_code="teaching", done=0, total=9, unit="句"))
            assert durable()["stage_code"] == "teaching"
            progress(Progress("working", done=7, total=9, unit="句"))
            assert queue.snapshot()[0]["stage_done"] == 7 and durable()["stage_done"] == 0
            raise InterruptedError("synthetic failure")

        monkeypatch.setattr(batch, "prepare_media", prepare)
        with patch("os.fsync", wraps=os.fsync) as fsync:
            queue.run()
        assert fsync.call_count >= 3
        assert durable()["status"] == "failed"
        assert durable()["stage_done"] == 7


@pytest.mark.parametrize("previous_track", [True, False])
def test_failed_publish_keeps_prior_tracks_across_intermediate_checkpoints(tmp_path, monkeypatch, previous_track):
    from tingmu import storage

    with closing(Store(tmp_path)) as store:
        doc = StudyDocument(str(tmp_path / "clip.mkv"), LanguagePair(), [Cue("a", 0, 1, "A")],
                            metadata={"stage": "ready" if previous_track else "annotating"})
        path = store.save_document(doc)
        srt = path.with_suffix(".srt")
        translated = path.with_suffix(".translation.srt")
        if previous_track:
            translated.write_bytes(b"previously published translation")
        pubs = {p: p.read_bytes() for p in (srt, translated) if p.exists()}
        doc.metadata["stage"] = "annotating"
        doc.cues[0].text = "Unpublished B"
        store.save_document(doc)
        checkpoint = path.read_bytes()
        doc.metadata["stage"] = "ready"
        doc.cues[0].text = "Publish C"
        original = storage.atomic_json

        def fail(destination, value):
            if destination == path:
                raise OSError("document replace failed")
            original(destination, value)

        monkeypatch.setattr(storage, "atomic_json", fail)
        with pytest.raises(OSError, match="document replace failed"):
            store.save_document(doc)
        assert path.read_bytes() == checkpoint
        assert {p: p.read_bytes() for p in (srt, translated) if p.exists()} == pubs
        assert set(path.parent.iterdir()) == {path, *pubs}


@pytest.mark.parametrize("committed", [False, True])
def test_interrupted_publication_recovers_at_json_commit_boundary(tmp_path, monkeypatch, committed):
    from tingmu import storage

    class Interruption(BaseException):
        pass

    with closing(Store(tmp_path)) as store:
        doc = StudyDocument(str(tmp_path / "clip.mkv"), LanguagePair(), [Cue("a", 0, 1, "Published A")],
                            metadata={"stage": "ready"})
        path = store.save_document(doc)
        srt = path.with_suffix(".srt")
        doc.metadata["stage"] = "annotating"
        doc.cues[0].text = "Unpublished B"
        store.save_document(doc)
        doc.metadata["stage"] = "ready"
        doc.cues[0].text = "Published C"
        original = storage.atomic_json

        def interrupt(destination, value):
            if destination != path or committed:
                original(destination, value)
            if destination == path:
                raise Interruption

        with monkeypatch.context() as patch:
            patch.setattr(storage, "atomic_json", interrupt)
            with pytest.raises(Interruption):
                store.save_document(doc)
        assert path.with_suffix(".pending").exists()
        assert "Published A" in srt.read_text("utf-8")
    with closing(Store(tmp_path)) as reopened:
        recovered = reopened.load_document(doc.media_path, doc.language)
        assert recovered.cues[0].text == ("Published C" if committed else "Unpublished B")
        assert ("Published C" if committed else "Published A") in srt.read_text("utf-8")
        assert set(path.parent.iterdir()) == {path, srt}


def test_in_place_correction_preserving_size_and_timestamp_blocks_stale_save(tmp_path):
    with closing(Store(tmp_path)) as store:
        doc = StudyDocument(str(tmp_path / "clip.mkv"), LanguagePair(), [Cue("one", 0, 1, "A")],
                            metadata={"stage": "partial", "cue_corrections": {"one": {"revision": "abc"}}})
        path = store.save_document(doc)
        info = path.stat()
        revised = path.read_bytes().replace(b'"revision":"abc"', b'"revision":"xyz"')
        path.write_bytes(revised)
        os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns))
        with pytest.raises(ValueError, match="逐句修订"):
            store.save_document(doc)
        assert path.read_bytes() == revised


def test_workspace_named_documents_keeps_editable_json_indented(tmp_path):
    from tingmu.storage import atomic_json

    folder = tmp_path / "documents"
    with closing(Store(folder)) as store:
        store.save_settings(AppSettings())
        path = folder / "workspaces.json"
        atomic_json(path, {"workspaces": []})
        assert b'\n  "' in (folder / "settings.json").read_bytes()
        assert b'\n  "workspaces"' in path.read_bytes()
