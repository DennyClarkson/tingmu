from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace
from uuid import UUID

import pytest

from tingmu import conversations, timestamps
from tingmu.conversations import ConversationStore
from tingmu.models import LanguagePair
from tingmu.storage import Store


@pytest.fixture
def frozen_clock(monkeypatch):
    instant = datetime(2030, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(timestamps, "_last", None)
    monkeypatch.setattr(timestamps, "_utc_now", lambda: instant)


def fixed_uuids(monkeypatch, *numbers):
    ids = iter(UUID(int=n) for n in numbers)
    monkeypatch.setattr(conversations, "uuid", SimpleNamespace(uuid4=lambda: next(ids)))


def session_ids(chats):
    return [s["id"] for s in chats.list_sessions("yue")]


def test_latest_session_follows_each_write_when_clock_stands_still(tmp_path, monkeypatch, frozen_clock):
    fixed_uuids(monkeypatch, 2, 1, 4, 3)
    language = LanguagePair()
    with closing(Store(tmp_path)) as store:
        chats = ConversationStore(store)
        original = chats.create(language)
        second = chats.create(language)
        assert second["created_at"] > original["created_at"]
        assert session_ids(chats) == [second["id"], original["id"]]

        message = {"role": "user", "content": "question", "id": "m1", "created_at": original["created_at"]}
        saved = chats.save_with_legacy(original["id"], [message])
        assert saved["updated_at"] > second["updated_at"]
        assert saved["created_at"] == original["created_at"]
        assert session_ids(chats) == [original["id"], second["id"]]

        renamed = chats.rename(second["id"], "Renamed")
        assert renamed["updated_at"] > saved["updated_at"]
        assert session_ids(chats) == [second["id"], original["id"]]

        store.save_conversation("legacy.mkv", language, [{"role": "user", "content": "legacy"}])
        imported = chats.migrate_legacy("legacy.mkv", language)
        assert imported["created_at"] == imported["updated_at"] > renamed["updated_at"]
        assert chats.migrate_legacy("legacy.mkv", language) == imported
        assert session_ids(chats) == [imported["id"], second["id"], original["id"]]
        assert chats.load(original["id"]) == saved


def test_old_sessions_with_identical_times_use_insertion_order_on_reopen(tmp_path, monkeypatch):
    fixed_uuids(monkeypatch, 2, 1, 3)
    legacy_time = "2026-01-01T00:00:00+00:00"
    with closing(Store(tmp_path)) as store:
        chats = ConversationStore(store)
        ids = [chats.create(LanguagePair())["id"] for _ in range(3)]
        with store._connection() as db:
            db.execute("UPDATE chat_sessions SET created_at=?,updated_at=?", (legacy_time, legacy_time))

    with closing(Store(tmp_path)) as reopened:
        rows = ConversationStore(reopened).list_sessions("yue")
        assert [s["id"] for s in rows] == ids[::-1]
        assert all(s["created_at"] == s["updated_at"] == legacy_time for s in rows)


def test_session_created_by_delayed_writer_is_latest(tmp_path, monkeypatch, frozen_clock):
    ready, resume = Event(), Event()
    with closing(Store(tmp_path)) as waiting_store, closing(Store(tmp_path)) as active_store:
        connection = waiting_store._connection

        def wait_before_opening(*args, **kwargs):
            ready.set()
            assert resume.wait(5)
            return connection(*args, **kwargs)

        monkeypatch.setattr(waiting_store, "_connection", wait_before_opening)
        waiting, active = ConversationStore(waiting_store), ConversationStore(active_store)
        with ThreadPoolExecutor(max_workers=1) as worker:
            future = worker.submit(waiting.create, LanguagePair())
            try:
                assert ready.wait(5)
                first = active.create(LanguagePair())
            finally:
                resume.set()
            last = future.result(timeout=5)
        assert last["created_at"] > first["created_at"]
        assert session_ids(active) == [last["id"], first["id"]]
