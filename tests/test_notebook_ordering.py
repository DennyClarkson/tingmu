from datetime import UTC, datetime
from itertools import count
from types import SimpleNamespace
from uuid import UUID

import pytest

from tingmu import notebooks, timestamps
from tingmu.models import Cue, LanguagePair, Token
from tingmu.notebooks import NotebookManager
from tingmu.storage import Store


LANGUAGE = LanguagePair()
WORD = Token("唔該")
FIRST = Cue("a", 0, 1, "唔該。")
NEXT = Cue("b", 1, 2, "唔該晒。")
LOCAL = SimpleNamespace(provider="local")


@pytest.fixture
def frozen_clock(monkeypatch):
    instant = datetime(2045, 1, 2, 3, 4, 5, tzinfo=UTC)
    monkeypatch.setattr(timestamps, "_utc_now", lambda: instant)
    monkeypatch.setattr(timestamps, "_last", None)
    # Newer note IDs sort before older ones; backups also receive distinct UUID prefixes.
    ids = count(0xFFFFFFFF, -1)
    monkeypatch.setattr(notebooks, "uuid", SimpleNamespace(uuid4=lambda: UUID(int=next(ids) << 96)))
    return instant


def test_each_organized_topic_keeps_its_position_across_batches_and_reorganization(tmp_path, frozen_clock):
    store = Store(tmp_path)
    for index in range(13):
        store.save_note("movie.mkv", Cue(str(index), index, index + 1, f"句子{index}"), "备注", LANGUAGE)
    book = NotebookManager(store)
    batch = book.organize("yue", LOCAL)
    source_ids = [entry["id"] for entry in batch["collections"]]
    book.organize("yue", LOCAL, entry_ids=[source_ids[0]])
    snapshot = NotebookManager(Store(tmp_path)).snapshot("yue")
    one, two, three = snapshot["notes"]
    assert [topic["id"] for topic in snapshot["topics"]] == [one["id"], two["id"], three["id"]]
    assert [note["entry_ids"] for note in snapshot["notes"]] == [source_ids[:12], source_ids[12:], source_ids[:1]]
    assert datetime.fromisoformat(one["created_at"]).replace(microsecond=0) == frozen_clock
    assert one["created_at"] < two["created_at"] < three["created_at"]
    assert one["id"] > two["id"] > three["id"]
    offsets = [snapshot["markdown"].index(note["id"]) for note in (one, two, three)]
    assert offsets == sorted(offsets)


def test_detached_note_stays_before_recollection_supplement(tmp_path, frozen_clock):
    store = Store(tmp_path)
    store.save_word("movie.mkv", FIRST, WORD, LANGUAGE)
    book = NotebookManager(store)
    original = book.append_note("yue", "原笔记", "手写说明", [store.entries()[0]["id"]])
    original_id = original["notes"][0]["id"]
    detached = original["markdown"].replace(f"{original_id}:start", "my-edited-marker")
    book.save("yue", detached, original["revision"])

    assert book.collect_word("movie.mkv", NEXT, WORD, LANGUAGE, confirm=True) == "referenced"
    snapshot = NotebookManager(Store(tmp_path)).snapshot("yue")
    old, supplement = snapshot["notes"]
    assert [topic["id"] for topic in snapshot["topics"]] == [original_id, supplement["id"]]
    assert old["id"] == original_id and old["detached"]
    assert snapshot["markdown"].startswith(detached)
    assert not supplement["detached"]
    assert supplement["entry_ids"] == [entry["id"] for entry in snapshot["collections"]]
    assert old["id"] > supplement["id"] and old["created_at"] < supplement["created_at"]
    assert datetime.fromisoformat(old["created_at"]).replace(microsecond=0) == frozen_clock


def test_recollection_follows_latest_edit_and_preserves_creation_order(tmp_path, frozen_clock):
    store = Store(tmp_path)
    store.save_word("movie.mkv", FIRST, WORD, LANGUAGE)
    book = NotebookManager(store)
    source_id = store.entries()[0]["id"]
    first = book.append_note("yue", "第一条", "原文", [source_id])["notes"][0]
    before_edit = book.append_note("yue", "第二条", "原文", [source_id])
    second = before_edit["notes"][1]
    updated = book.update_note("yue", second["id"], "第二条", "先修订", before_edit["revision"])
    edited = book.update_note("yue", first["id"], "第一条", "后修订", updated["revision"])
    older, newer = edited["notes"]
    assert older["id"] == first["id"] and older["id"] > newer["id"]
    assert datetime.fromisoformat(older["created_at"]).replace(microsecond=0) == frozen_clock
    assert first["created_at"] == older["created_at"] < newer["created_at"]
    assert second["updated_at"] < newer["updated_at"] < older["updated_at"]

    assert book.collect_word("movie.mkv", NEXT, WORD, LANGUAGE, confirm=True) == "referenced"
    snapshot = book.snapshot("yue")
    extended, other = snapshot["notes"]
    assert extended["id"] == older["id"] and extended["created_at"] == older["created_at"]
    assert older["updated_at"] < extended["updated_at"]
    assert extended["entry_ids"] == [entry["id"] for entry in snapshot["collections"]]
    assert other == newer


def test_migrated_note_precedes_first_new_note_on_same_clock_tick(tmp_path, frozen_clock):
    store = Store(tmp_path)
    store.save_word("movie.mkv", FIRST, WORD, LANGUAGE)
    source_id = store.entries()[0]["id"]
    path = tmp_path / "notebooks" / "yue" / "notebook.md"
    path.parent.mkdir(parents=True)
    legacy = f"# 手写笔记\n\n[来源](tingmu://entry/{source_id})\n"
    path.write_text(legacy, encoding="utf-8")
    book = NotebookManager(store)
    migrated = book.snapshot("yue")
    old, = migrated["notes"]
    assert migrated["markdown"] == legacy and old["origin"] == "legacy_plain"
    assert datetime.fromisoformat(old["created_at"]).replace(microsecond=0) == frozen_clock

    snapshot = book.organize("yue", LOCAL, entry_ids=[source_id])
    original, new = snapshot["notes"]
    assert [topic["id"] for topic in snapshot["topics"]] == [old["id"], new["id"]]
    assert original == old and snapshot["markdown"].startswith(legacy)
    assert old["id"] > new["id"] and old["created_at"] < new["created_at"]
    assert NotebookManager(Store(tmp_path)).snapshot("yue") == snapshot


def test_existing_equal_timestamps_use_insertion_order_for_display_and_latest_reference(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.save_word("movie.mkv", FIRST, WORD, LANGUAGE)
    source_id = store.entries()[0]["id"]
    book = NotebookManager(store)
    # The last inserted ID is neither the lexically first nor last.
    ids = [UUID(int=i) for i in (9, 1, 5)]
    for identifier in ids:
        monkeypatch.setattr(notebooks, "uuid", SimpleNamespace(uuid4=lambda i=identifier: i))
        book.append_note("yue", identifier.hex, "已有笔记", [source_id])
    legacy_time = "2024-01-02T03:04:05+00:00"
    with store._connection(immediate=True) as db:
        db.execute("UPDATE notebook_notes SET created_at=?,updated_at=?", (legacy_time, legacy_time))

    reopened = NotebookManager(Store(tmp_path))
    notes = reopened.snapshot("yue")["notes"]
    assert [note["id"] for note in notes] == [identifier.hex for identifier in ids]
    assert all(note["created_at"] == note["updated_at"] == legacy_time for note in notes)
    assert reopened.collect_word("movie.mkv", NEXT, WORD, LANGUAGE, confirm=True) == "referenced"
    snapshot = reopened.snapshot("yue")
    assert snapshot["notes"][:2] == notes[:2]
    latest = snapshot["notes"][2]
    assert latest["id"] == ids[-1].hex and latest["created_at"] == legacy_time
    assert latest["entry_ids"] == [entry["id"] for entry in snapshot["collections"]]
