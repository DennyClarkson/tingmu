"""Versioned workspace migration, nested transactions, and bounded handle lifetime."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import sqlite3
import threading
from unittest.mock import patch

import pytest

from tingmu.conversations import ConversationStore
from tingmu.database import Database, SCHEMA_VERSION
from tingmu.drafts import DraftStorage
from tingmu.models import Cue, LanguagePair
from tingmu.notebooks import NotebookManager
from tingmu.storage import Store


TABLES = {
    "entries",
    "positions",
    "conversations",
    "word_forms",
    "notebook_state",
    "notebook_reviews",
    "notebook_collections",
    "notebook_notes",
    "notebook_v5_migrations",
    "notebook_sources",
    "notebook_entry_note_history",
    "chat_sessions",
    "chat_legacy_migrations",
    "ui_drafts",
    "ui_draft_migrations",
}
INDEXES = {"entries_word_lookup", "notebook_archived_word_lookup", "word_forms_key"}


def legacy_database(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("""CREATE TABLE entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, media_path TEXT NOT NULL,
            cue_id TEXT NOT NULL, start REAL NOT NULL, text TEXT NOT NULL, reading TEXT NOT NULL DEFAULT '',
            meaning TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '', sentence TEXT NOT NULL,
            source_language TEXT NOT NULL, target_language TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(kind,media_path,cue_id,text,source_language,target_language))""")
        db.execute(
            "INSERT INTO entries VALUES(1,'word','old.mkv','source',1.25,'Nice','','meaning','personal note',"
            "'Nice sentence','zh-CN','en','old-time')"
        )
        db.execute(
            "CREATE TABLE notebook_sources(entry_id INTEGER PRIMARY KEY,target_language TEXT NOT NULL,entry_json TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO notebook_sources VALUES(2,'en',?)",
            (json.dumps({"kind": "word", "text": "Archived"}),),
        )
        db.execute(
            "CREATE TABLE ui_drafts(kind TEXT NOT NULL,key TEXT NOT NULL,value TEXT NOT NULL,PRIMARY KEY(kind,key))"
        )
        db.execute(
            "INSERT INTO ui_drafts VALUES('noteDrafts','old.mkv|zh-CN|en|source',?)",
            (json.dumps("完整手写草稿"),),
        )
        db.execute("PRAGMA user_version=0")


def schema(db):
    return {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def test_empty_database_centralizes_schema_and_managers_share_owner(tmp_path):
    store = Store(tmp_path / "data")
    with store._connection() as db:
        assert schema(db) == TABLES
        assert INDEXES <= {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert ConversationStore(store).database is store.database
    assert NotebookManager(store).database is store.database
    assert DraftStorage(store).database is DraftStorage(store.database).database is store.database
    standalone = DraftStorage(store.db_path)
    standalone.write("notebookDrafts", "en", {"text": "shared database file"})
    assert DraftStorage(store).load("notebookDrafts")["en"]["text"] == "shared database file"
    standalone.close()
    assert not store.database.closed
    store.close()
    assert store.database.closed


def test_version_zero_upgrade_preserves_live_archived_and_draft_data_and_is_idempotent(tmp_path):
    path = tmp_path / "data/study.db"
    legacy_database(path)
    first = Store(path.parent)
    assert first.entries()[0]["note"] == "personal note"
    assert DraftStorage(first).load("noteDrafts") == {"old.mkv|zh-CN|en|source": "完整手写草稿"}
    with first._connection() as db:
        assert schema(db) == TABLES
        assert [tuple(row) for row in db.execute("SELECT text,word_key FROM word_forms ORDER BY text")] == [
            ("Archived", "archived"),
            ("Nice", "nice"),
        ]
        before = list(db.iterdump())
    second = Store(path.parent)
    with second._connection() as db:
        assert list(db.iterdump()) == before
    first.close()
    second.close()


def test_migration_failure_rolls_back_all_new_tables_data_and_version(tmp_path, monkeypatch):
    from tingmu import database

    path = tmp_path / "study.db"
    legacy_database(path)
    original = database._migrate_v1

    def failed(db):
        original(db)
        db.execute("UPDATE entries SET note='must roll back'")
        raise RuntimeError("synthetic interrupted migration")

    with monkeypatch.context() as patcher:
        patcher.setattr(database, "_migrate_v1", failed)
        with pytest.raises(RuntimeError, match="interrupted"):
            Database(path)
    with closing(sqlite3.connect(path)) as db:
        assert schema(db) == {"entries", "notebook_sources", "ui_drafts"}
        assert db.execute("PRAGMA user_version").fetchone()[0] == 0
        assert db.execute("SELECT note FROM entries").fetchone()[0] == "personal note"
    restored = Database(path)
    with restored.connection() as db:
        assert schema(db) == TABLES
    restored.close()


def test_newer_database_version_is_rejected_without_downgrade(tmp_path):
    path = tmp_path / "future.db"
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA user_version=999")
    original = path.read_bytes()
    with pytest.raises(RuntimeError, match="newer"):
        Database(path)
    assert path.read_bytes() == original


def test_nested_savepoints_reuse_connection_without_committing_outer_transaction(tmp_path):
    database = Database(tmp_path / "study.db")
    with database.connection(immediate=True) as outer:
        outer.execute("INSERT INTO positions VALUES('outer',1)")
        with pytest.raises(ValueError, match="inner failure"):
            with database.connection(immediate=True) as inner:
                assert inner is outer
                inner.execute("INSERT INTO positions VALUES('inner',2)")
                raise ValueError("inner failure")
        assert [row[0] for row in outer.execute("SELECT media_path FROM positions")] == ["outer"]
    with pytest.raises(ValueError, match="outer failure"):
        with database.connection(immediate=True) as outer:
            with database.connection() as inner:
                inner.execute("INSERT INTO positions VALUES('nested-success',3)")
            raise ValueError("outer failure")
    with database.connection() as db:
        assert [tuple(row) for row in db.execute("SELECT * FROM positions")] == [("outer", 1)]
    database.close()


def test_explicit_session_reuses_handle_but_transactions_and_idle_files_are_released(tmp_path):
    folder = tmp_path / "portable"
    database = Database(folder / "study.db")
    connect = sqlite3.connect
    with patch("tingmu.database.sqlite3.connect", wraps=connect) as opened:
        with database.session():
            with database.connection() as first:
                first.execute("INSERT INTO positions VALUES('first',1)")
            with database.session():
                with database.connection() as second:
                    assert first is second
                    assert second.in_transaction
                    second.execute("INSERT INTO positions VALUES('second',2)")
            assert opened.call_count == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            first.execute("SELECT 1")
    # No close call is required before moving an idle portable folder.
    moved = folder.rename(tmp_path / "moved")
    database.close()
    (moved / "study.db").unlink()


def test_bounded_leases_and_explicit_close_wait_for_worker_release(tmp_path):
    database = Database(tmp_path / "study.db", max_connections=2)
    first_ready, second_ready, third_ready, release = (threading.Event() for _ in range(4))

    def hold(ready):
        with database.connection() as db:
            db.execute("SELECT 1")
            ready.set()
            assert release.wait(3)

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(hold, first_ready)
        second = pool.submit(hold, second_ready)
        assert first_ready.wait(2) and second_ready.wait(2)
        third = pool.submit(hold, third_ready)
        assert not third_ready.wait(0.05)
        with pytest.raises(TimeoutError, match="still active"):
            database.close(timeout=0)
        with pytest.raises(RuntimeError, match="closed"):
            third.result(timeout=2)
        release.set()
        first.result(timeout=2)
        second.result(timeout=2)
    database.close()
    database.path.unlink()


def test_parallel_store_construction_and_writes_keep_unique_rows(tmp_path):
    directory = tmp_path / "data"

    def write(index):
        store = Store(directory)
        store.save_note(
            "synthetic.mkv",
            Cue(str(index), index, index + 1, f"row-{index}"),
            f"personal-{index}",
            LanguagePair(),
        )
        store.close()

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(12)))
    store = Store(directory)
    assert len(store.entries()) == 12
    store.close()


@pytest.mark.parametrize("use_wrapper", [False, True])
def test_chat_legacy_projection_and_marker_are_one_atomic_transaction(tmp_path, use_wrapper):
    store = Store(tmp_path / "data")
    chats = ConversationStore(store)
    session = chats.create(LanguagePair(), "synthetic.mkv")
    message = [{"role": "user", "content": "New message"}]

    def save():
        if use_wrapper:
            return chats.save_with_legacy(session["id"], message)
        return chats.save(session["id"], message, record_legacy_migration=True)

    with store._connection() as db:
        db.execute("""CREATE TRIGGER fail_marker BEFORE INSERT ON chat_legacy_migrations
                      BEGIN SELECT RAISE(ABORT,'synthetic marker failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        save()
    assert chats.load(session["id"])["messages"] == []
    assert store.load_conversation("synthetic.mkv", LanguagePair()) == []
    with store._connection() as db:
        db.execute("DROP TRIGGER fail_marker")
    save()
    assert store.load_conversation("synthetic.mkv", LanguagePair()) == message
    with store._connection() as db:
        assert db.execute("SELECT session_id FROM chat_legacy_migrations").fetchone()[0] == session["id"]
    store.close()


def test_legacy_save_wrapper_preserves_positional_hook_and_existing_migration_identity(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    chats = ConversationStore(store)
    first = chats.create(LanguagePair(), "synthetic.mkv")
    second = chats.create(LanguagePair(), "synthetic.mkv")
    observed = []
    original = chats.save

    def positional_hook(session_id, messages):
        observed.append((session_id, messages))
        return original(session_id, messages)

    monkeypatch.setattr(chats, "save", positional_hook)
    first_rows = [{"role": "user", "content": "First session"}]
    second_rows = [{"role": "user", "content": "Second session"}]
    assert chats.save_with_legacy(first["id"], first_rows)["messages"][0]["content"] == "First session"
    chats.save_with_legacy(second["id"], second_rows)
    assert observed == [(first["id"], first_rows), (second["id"], second_rows)]
    assert store.load_conversation("synthetic.mkv", LanguagePair()) == second_rows
    with store._connection() as db:
        assert db.execute("SELECT session_id FROM chat_legacy_migrations").fetchone()[0] == first["id"]
    store.close()
