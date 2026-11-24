"""Draft failures remain recoverable and small commands retain ongoing status."""

from contextlib import closing
import sqlite3

import pytest
from PySide6.QtTest import QSignalSpy

from tingmu.drafts import DraftStorage
from tingmu.quick.bridge import Bridge
from tingmu.storage import Store, atomic_json


@pytest.fixture
def bridge(qapp, tmp_path):
    instance = Bridge(Store(tmp_path / "data"))
    instance.inbox_timer.stop()
    yield instance
    instance.shutdown()


def _draft_rows(store):
    with closing(sqlite3.connect(store.db_path)) as db:
        return (db.execute("SELECT * FROM ui_drafts ORDER BY kind,key").fetchall(),
                db.execute("SELECT * FROM ui_draft_migrations ORDER BY name").fetchall())


def _assert_learning_failure(bridge):
    assert bridge.learningState["error"]
    assert bridge.notificationState["error"] == bridge.learningState["error"]
    assert bridge.notificationState["status"] == bridge.learningState["error"]
    assert not bridge.mediaState["error"]
    assert not bridge.chatState["error"]


@pytest.mark.parametrize("failure", ["damaged", "conflict", "database"])
def test_bridge_starts_with_failed_legacy_drafts_and_keeps_recovery_data(qapp, tmp_path, failure):
    store = Store(tmp_path / "data")
    drafts = DraftStorage(store.db_path)
    drafts.write("notebookDrafts", "existing", {"text": "existing draft", "revision": "r2"})
    legacy = {"first": {"text": "must roll back", "revision": "r1"}}
    if failure == "damaged":
        legacy = ["invalid legacy draft map"]
    elif failure == "conflict":
        legacy["existing"] = {"text": "old conflicting draft", "revision": "r0"}
    else:
        with closing(sqlite3.connect(store.db_path)) as db, db:
            db.execute("CREATE TRIGGER fail_draft_insert BEFORE INSERT ON ui_drafts "
                       "BEGIN SELECT RAISE(ABORT, 'synthetic draft failure'); END")
    layout = {"workspace": {"width": 1234}, "preferences": {
        "notebookDrafts": legacy, "noteDrafts": {"cue": "recover this note"}}}
    path = store.data_dir / "quick-layout.json"
    atomic_json(path, layout)
    before_layout, before_rows = path.read_bytes(), _draft_rows(store)
    instance = Bridge(store)
    try:
        instance.inbox_timer.stop()
        assert instance.layout == layout
        assert path.read_bytes() == before_layout
        assert _draft_rows(store) == before_rows
        _assert_learning_failure(instance)
    finally:
        instance.shutdown()


@pytest.mark.parametrize("error_type", [OSError, ValueError, sqlite3.OperationalError])
def test_bridge_catches_legacy_migration_failures_on_startup(qapp, tmp_path, monkeypatch, error_type):
    store = Store(tmp_path / "data")
    drafts = DraftStorage(store.db_path)
    drafts.write("noteDrafts", "cue", "existing")
    layout = {"preferences": {"noteDrafts": {"other": "legacy"}}}
    path = store.data_dir / "quick-layout.json"
    atomic_json(path, layout)
    before_layout, before_rows = path.read_bytes(), _draft_rows(store)

    def fail(*args):
        raise error_type("synthetic startup draft failure")

    monkeypatch.setattr(DraftStorage, "migrate_layout", fail)
    instance = Bridge(store)
    try:
        instance.inbox_timer.stop()
        assert instance.layout == layout
        assert path.read_bytes() == before_layout
        assert _draft_rows(store) == before_rows
        _assert_learning_failure(instance)
    finally:
        instance.shutdown()


@pytest.mark.parametrize("error_type", [OSError, ValueError, sqlite3.OperationalError])
def test_save_layout_failure_preserves_saved_layout_and_drafts(bridge, monkeypatch, error_type):
    original = {"workspace": {"width": 1234}}
    assert bridge.saveLayout(original)
    bridge.drafts.write("noteDrafts", "cue", "existing")
    before_layout, before_rows = bridge._layout_path.read_bytes(), _draft_rows(bridge.store)
    changed = QSignalSpy(bridge.layoutChanged)

    def fail(*args):
        raise error_type("synthetic layout draft failure")

    monkeypatch.setattr(bridge.drafts, "migrate_layout", fail)
    assert bridge.saveLayout({"workspace": {"width": 1500}}) is False
    assert bridge.layout == original
    assert bridge._layout_path.read_bytes() == before_layout
    assert _draft_rows(bridge.store) == before_rows
    assert changed.count() == 0
    _assert_learning_failure(bridge)


def test_layout_write_failure_does_not_publish_unsaved_layout(bridge, monkeypatch):
    original = {"workspace": {"width": 1234}}
    assert bridge.saveLayout(original)
    before = bridge._layout_path.read_bytes()
    changed = QSignalSpy(bridge.layoutChanged)

    def fail(*args):
        raise OSError("synthetic layout write failure")

    monkeypatch.setattr("tingmu.quick.bridge.atomic_json", fail)
    assert bridge.saveLayout({"workspace": {"width": 1500}}) is False
    assert bridge.layout == original
    assert bridge._layout_path.read_bytes() == before
    assert changed.count() == 0
    _assert_learning_failure(bridge)


@pytest.mark.parametrize("error_type", [OSError, ValueError, sqlite3.OperationalError])
def test_load_drafts_failure_returns_empty_map_and_local_error(bridge, monkeypatch, error_type):
    bridge.drafts.write("noteDrafts", "cue", "existing")
    before = _draft_rows(bridge.store)

    def fail(*args):
        raise error_type("synthetic draft read failure")

    monkeypatch.setattr(bridge.drafts, "load", fail)
    assert bridge.loadDrafts("noteDrafts") == {}
    assert _draft_rows(bridge.store) == before
    _assert_learning_failure(bridge)


@pytest.mark.parametrize(("name", "payload"), [
    ("seek", {"position": 123}), ("setVolume", {"value": 0.4}), ("copyText", {"text": "synthetic"}),
])
def test_lightweight_commands_keep_ongoing_status_and_unrelated_errors(bridge, name, payload):
    bridge._error("Previous chat problem", domain="chat")
    bridge._set_state(domain="queue", status="Preparing synthetic subtitles")
    chat_changes = QSignalSpy(bridge.chatStateChanged)
    bridge.command(name, payload)
    assert bridge.notificationState["status"] == "Preparing synthetic subtitles"
    assert bridge.queueState["status"] == "Preparing synthetic subtitles"
    assert bridge.state["status"] == "Preparing synthetic subtitles"
    assert bridge.chatState["error"] == "Previous chat problem"
    assert chat_changes.count() == 0


@pytest.mark.parametrize(("name", "payload"), [
    ("saveFrame", {}), ("loopB", {}), ("setAudioTrack", {"index": 10000}),
    ("setSubtitleTrack", {"index": 10000}), ("setAudioOutput", {"id": "missing-synthetic-device"}),
    ("setSelectedMedia", {"paths": ["not-in-library"]}),
])
def test_actual_media_commands_route_rejections_to_media(bridge, name, payload):
    bridge.command(name, payload)
    assert bridge.mediaState["error"]
    assert bridge.notificationState["error"] == bridge.mediaState["error"]
    assert not bridge.chatState["error"]
    assert not bridge.learningState["error"]


def test_clear_chat_busy_rejection_is_owned_by_chat(bridge):
    # A membership-only sentinel exercises the real guard without running a reply.
    bridge._workers["chat"] = None
    try:
        bridge.command("clearChat", {})
        assert bridge.chatState["error"]
        assert bridge.notificationState["error"] == bridge.chatState["error"]
        assert not bridge.mediaState["error"]
    finally:
        bridge._workers.pop("chat")


@pytest.mark.parametrize("name", ["removeMedia", "scanWorkspaceMedia", "removeDisplaySubtitle"])
def test_media_storage_failures_use_media_domain(bridge, monkeypatch, name):
    def fail(*args, **kwargs):
        raise OSError("synthetic media operation failure")

    if name == "removeMedia":
        monkeypatch.setattr(bridge.catalog, "remove", fail)
    elif name == "scanWorkspaceMedia":
        monkeypatch.setattr(bridge, "_scan_media_inbox", fail)
    else:
        monkeypatch.setattr(bridge.sources, "remove", fail)
    bridge.command(name, {})
    assert bridge.mediaState["error"] == "synthetic media operation failure"
    assert bridge.notificationState["error"] == bridge.mediaState["error"]
