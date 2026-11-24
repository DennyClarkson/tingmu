"""Complete drafts survive persistence, legacy import, and portable relocation."""

from contextlib import closing
from copy import deepcopy
import json
import shutil
import sqlite3

import pytest

from tingmu.drafts import DraftStorage
from tingmu.portable_paths import MANIFEST
from tingmu.storage import Store, atomic_json


def test_large_chinese_drafts_roundtrip_update_delete_and_workspace_isolation(tmp_path):
    store = Store(tmp_path / "one")
    drafts = DraftStorage(store.db_path)
    manuscript = {"text": "繁體與简体草稿，逐字保留。\n" * 30000, "revision": "before-edit"}
    cue_key = str(tmp_path / "外部影片.mkv") + "|zh-CN|yue|cue-1"
    cue_note = "一句手写备注，不截断。" * 5000
    drafts.write("notebookDrafts", "yue", manuscript)
    drafts.write("notebookDrafts", "en", {"text": "Other book", "revision": "r1"})
    drafts.write("noteDrafts", cue_key, cue_note)
    restored = DraftStorage(store.db_path)
    assert restored.load("notebookDrafts")["yue"] == manuscript
    assert restored.load("noteDrafts") == {cue_key: cue_note}
    assert DraftStorage(Store(tmp_path / "two").db_path).load("notebookDrafts") == {}
    manuscript["text"] += "最后一句\n"
    restored.write("notebookDrafts", "yue", manuscript)
    restored.delete("notebookDrafts", "en")
    restored.delete("noteDrafts", cue_key)
    assert DraftStorage(store.db_path).load("notebookDrafts") == {"yue": manuscript}
    assert restored.load("noteDrafts") == {}
    assert not (store.data_dir / "quick-layout.json").exists()


def test_layout_import_is_lossless_once_and_retains_recovery_snapshot(tmp_path):
    store = Store(tmp_path)
    drafts = DraftStorage(store.db_path)
    layout = {"version": 1, "preferences": {
        "notebookDrafts": {"yue": {"text": "长篇草稿\n" * 20000, "revision": "old"}},
        "noteDrafts": {"movie|zh-CN|yue|cue": "备注原文"},
        "player": {"displayImported": False}}, "workspace": {"width": 1200}}
    before = deepcopy(layout)
    clean = drafts.migrate_layout(layout)
    assert layout == before
    assert clean == {"version": 1, "preferences": {"player": {"displayImported": False}},
                     "workspace": {"width": 1200}}
    assert drafts.load("notebookDrafts") == before["preferences"]["notebookDrafts"]
    assert drafts.load("noteDrafts") == before["preferences"]["noteDrafts"]
    with closing(sqlite3.connect(store.db_path)) as db:
        original = db.execute("SELECT original FROM ui_draft_migrations").fetchone()[0]
        assert json.loads(original) == {key: before["preferences"][key]
                                       for key in ("notebookDrafts", "noteDrafts")}
    # Simulate the old layout surviving a crash after database commit.
    drafts.delete("noteDrafts", "movie|zh-CN|yue|cue")
    drafts.write("notebookDrafts", "yue", {"text": "后续编辑", "revision": "new"})
    assert DraftStorage(store.db_path).migrate_layout(layout) == clean
    assert drafts.load("noteDrafts") == {}
    assert drafts.load("notebookDrafts")["yue"]["text"] == "后续编辑"


@pytest.mark.parametrize("failure", ["conflict", "database"])
def test_failed_legacy_import_keeps_layout_and_all_drafts(tmp_path, failure):
    store = Store(tmp_path)
    drafts = DraftStorage(store.db_path)
    layout = {"preferences": {"notebookDrafts": {
        "first": {"text": "must roll back", "revision": "1"},
        "bad": {"text": "old", "revision": "2"}}, "noteDrafts": {"x": "旧备注"}}}
    atomic_json(store.data_dir / "quick-layout.json", layout)
    original = (store.data_dir / "quick-layout.json").read_bytes()
    if failure == "conflict":
        drafts.write("notebookDrafts", "bad", {"text": "new", "revision": "3"})
        expected_error = ValueError
    else:
        with closing(sqlite3.connect(store.db_path)) as db, db:
            db.execute("CREATE TRIGGER fail_draft_insert BEFORE INSERT ON ui_drafts "
                       "WHEN NEW.key='bad' BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
        expected_error = sqlite3.IntegrityError
    before = {kind: drafts.load(kind) for kind in ("notebookDrafts", "noteDrafts")}
    with pytest.raises(expected_error):
        drafts.migrate_layout(layout)
    assert (store.data_dir / "quick-layout.json").read_bytes() == original
    assert json.loads(original) == layout
    assert {kind: drafts.load(kind) for kind in before} == before
    with closing(sqlite3.connect(store.db_path)) as db:
        assert db.execute("SELECT COUNT(*) FROM ui_draft_migrations").fetchone()[0] == 0


def _copied_portable(tmp_path, monkeypatch):
    import tingmu.runtime as runtime

    root = [tmp_path / "Old"]
    monkeypatch.setattr(runtime, "portable_root", lambda: root[0])
    store = Store(root[0] / "data")
    drafts = DraftStorage(store.db_path)
    return root, store, drafts


def test_portable_copy_remaps_internal_keys_only_and_preserves_original(tmp_path, monkeypatch):
    root, store, drafts = _copied_portable(tmp_path, monkeypatch)
    original_root = root[0]
    internal = str(original_root / "media" / "片段.mkv")
    external = str(tmp_path / "Old sibling" / "外部.mkv")
    suffix = "|zh-CN|yue|cue-1"
    body = "正文中的路径不能变：" + internal
    notebook = {"text": body, "revision": internal, "path": internal}
    drafts.write("noteDrafts", internal + suffix, body)
    drafts.write("noteDrafts", external + suffix, "外部备注")
    drafts.write("notebookDrafts", "yue", notebook)
    original_db = store.db_path.read_bytes()
    original_manifest = (store.data_dir / MANIFEST).read_bytes()
    for name in ("Moved", "Moved twice"):
        target = tmp_path / name
        shutil.copytree(root[0], target)
        root[0] = target
        moved_store = Store(target / "data")
        moved_drafts = DraftStorage(moved_store.db_path)
        assert moved_drafts.load("noteDrafts") == {
            str(target / "media" / "片段.mkv") + suffix: body, external + suffix: "外部备注"}
        assert moved_drafts.load("notebookDrafts") == {"yue": notebook}
    assert (original_root / "data" / "study.db").read_bytes() == original_db
    assert (original_root / "data" / MANIFEST).read_bytes() == original_manifest
    assert len(list((root[0] / "data" / "migration-backups").glob("*/study.db"))) == 2


@pytest.mark.parametrize("failure", ["draft_collision", "sql_collision", "json_write"])
def test_portable_failure_rolls_back_drafts_and_preserves_original(tmp_path, monkeypatch, failure):
    import tingmu.storage as storage

    root, store, drafts = _copied_portable(tmp_path, monkeypatch)
    original_root = root[0]
    moved_root = tmp_path / "Moved"
    old_media = str(original_root / "clip.mkv")
    new_media = str(moved_root / "clip.mkv")
    suffix = "|zh-CN|yue|cue"
    drafts.write("noteDrafts", old_media + suffix, "原草稿")
    store.save_position(old_media, 123)
    if failure == "draft_collision":
        drafts.write("noteDrafts", new_media + suffix, "不同草稿")
    elif failure == "sql_collision":
        store.save_position(new_media, 456)
    else:
        atomic_json(store.data_dir / "media-library.json", {"paths": [{"path": old_media}]})
    before = drafts.load("noteDrafts")
    original_db = store.db_path.read_bytes()
    original_manifest = (store.data_dir / MANIFEST).read_bytes()
    shutil.copytree(original_root, moved_root)
    root[0] = moved_root
    if failure == "json_write":
        actual_write = storage.atomic_json

        def fail_write(path, value):
            if path == moved_root / "data" / "media-library.json":
                raise OSError("simulated interruption")
            actual_write(path, value)

        monkeypatch.setattr(storage, "atomic_json", fail_write)
    with pytest.raises(OSError if failure == "json_write" else ValueError):
        Store(moved_root / "data")
    assert DraftStorage(moved_root / "data" / "study.db").load("noteDrafts") == before
    assert (moved_root / "data" / MANIFEST).read_bytes() == original_manifest
    assert (original_root / "data" / "study.db").read_bytes() == original_db
    assert (original_root / "data" / MANIFEST).read_bytes() == original_manifest
    with closing(sqlite3.connect(moved_root / "data" / "study.db")) as db:
        assert db.execute("SELECT milliseconds FROM positions WHERE media_path=?", (old_media,)).fetchone() == (123,)


def test_companion_drafts_survive_cue_switch_restart_and_save(qapp, qtbot, tmp_path):
    from PySide6.QtCore import QObject, QMetaObject, Qt

    from tingmu.models import Cue, LanguagePair, StudyDocument
    from tingmu.quick.main import create_qml_app

    media = str(tmp_path / "sample.mkv")
    document = StudyDocument(media, LanguagePair(), [Cue("one", 0, 1, "第一句"), Cue("two", 2, 3, "第二句")])
    draft = "尚未保存的中文备注。" * 400
    first_key, second_key = (media + "|zh-CN|yue|" + cue for cue in ("one", "two"))
    for restarting in (False, True):
        app, bridge, engine = create_qml_app(tmp_path / "data")
        window = engine.rootObjects()[0]
        try:
            bridge.timer.stop()
            bridge.media_path = media
            bridge._set_state(mediaPath=media)
            bridge._set_document(document)
            bridge._sync_cue(500)
            qtbot.wait(30)
            editor = window.findChild(QObject, "cueNoteDraftInput")
            assert editor is not None
            if restarting:
                assert editor.property("text") == draft
                save = window.findChild(QObject, "saveCueNoteButton")
                assert QMetaObject.invokeMethod(save, "clicked", Qt.ConnectionType.DirectConnection)
                assert first_key not in bridge.loadDrafts("noteDrafts")
                assert bridge.store.entries(media)[0]["note"] == draft
                bridge._sync_cue(2500)
                assert editor.property("text") == "第二句独立草稿"
            else:
                editor.setProperty("text", draft)
                assert QMetaObject.invokeMethod(editor, "textEdited", Qt.ConnectionType.DirectConnection)
                qtbot.waitUntil(lambda: bridge.loadDrafts("noteDrafts").get(first_key) == draft)
                assert bridge.loadDrafts("noteDrafts")[first_key] == draft
                bridge._sync_cue(2500)
                assert editor.property("text") == ""
                editor.setProperty("text", "第二句独立草稿")
                assert QMetaObject.invokeMethod(editor, "textEdited", Qt.ConnectionType.DirectConnection)
                qtbot.waitUntil(lambda: bridge.loadDrafts("noteDrafts").get(second_key) == "第二句独立草稿")
                assert bridge.loadDrafts("noteDrafts")[second_key] == "第二句独立草稿"
                bridge._sync_cue(500)
                assert editor.property("text") == draft
            assert engine._tingmu_warnings == []
        finally:
            window.close()
            bridge.shutdown()
            for child in app.topLevelWindows():
                child.hide()
            engine.deleteLater()
            qtbot.wait(20)
