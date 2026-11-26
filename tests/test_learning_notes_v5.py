"""Inbox/Markdown separation and lossless explicit learning-note operations."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from tingmu.learning_notes import DETAIL_FIELDS, render_topic_body
from tingmu.models import Cue, LanguagePair, Token
from tingmu.notebooks import BEGIN, END, NotebookConflict, NotebookManager
from tingmu.storage import Store


@pytest.fixture
def book(tmp_path):
    store = Store(tmp_path / "data")
    store.save_word("private/one.mkv", Cue("a", 1, 2, "唔該你"), Token("唔該", "m4 goi1", "谢谢或劳驾"), LanguagePair())
    store.save_note("private/one.mkv", Cue("b", 3, 4, "食咗飯未？"), "原收藏备注", LanguagePair())
    store.save_word("private/two.mkv", Cue("c", 1, 2, "please"), Token("please"), LanguagePair(target="en"))
    return store, NotebookManager(store)


class Tutor:
    provider = "mock"

    def __init__(self, callback=None, alter=None):
        self.calls = []
        self.prompts = []
        self.callback, self.alter = callback, alter

    def _complete(self, prompt, payload, schema):
        self.calls.append(payload)
        self.prompts.append(prompt)
        if self.callback:
            self.callback()
        entries = []
        for source in payload["entries"]:
            row = {"entry_id": source["id"], **{key: "" for key in DETAIL_FIELDS}}
            row.update(comparison="普通话常按词义理解，粤语还需注意交际场景。", usage="先判断是在请求帮助还是回应帮助。",
                       grammar="结合句子位置看表达的功能。", pitfalls="避免机械对应单一释义。",
                       example="唔該，借借。", example_translation="劳驾，请让一下。这是教学例句。",
                       goal="能在请求帮助时自然使用。", remark=source.get("entry_note", ""))
            entries.append(row)
        result = {"topics": [{"title": "日常交流", "entry_ids": [e["id"] for e in payload["entries"]],
                               "learning_note": "结合自己的学习目标，比较两种语言在真实语境中的表达。",
                               "entry_notes": entries}]}
        if self.alter:
            self.alter(result)
        return json.dumps(result, ensure_ascii=False)


def test_new_collection_never_changes_markdown_even_after_restart_and_sync(book):
    store, manager = book
    before = manager.sync("yue")
    path = Path(before["path"])
    content = path.read_bytes()
    assert before["notes"] == [] and before["stats"]["unorganized"] == 2
    store.save_word("third.mkv", Cue("new", 6, 7, "冇問題"), Token("冇問題"), LanguagePair())
    for reader in (manager, NotebookManager(Store(store.data_dir))):
        snapshot = reader.sync("yue")
        assert snapshot["stats"]["unorganized"] == 3 and snapshot["notes"] == []
        assert path.read_bytes() == content
        assert all(not e["organized"] for e in snapshot["collections"])


def test_explicit_organize_adds_teaching_keeps_collections_and_is_incremental(book):
    store, manager = book
    initial = manager.sync("yue")
    personal = initial["markdown"] + "\n我的手写思考，不能丢。\n"
    manager.save("yue", personal, initial["revision"])
    entry = initial["collections"][0]
    manager.update_entry_note(entry["id"], "我想分清普通话与粤语的礼貌表达", entry["note_revision"], target_language="yue")
    tutor = Tutor()
    result = manager.organize("yue", tutor, instructions="重点比较普通话，练习礼貌请求")
    assert result["markdown"].startswith(personal)
    assert "已知语言/方言与目标语言/方言对比" in result["markdown"] and "我想分清普通话" in result["markdown"]
    assert "语法要点" in result["markdown"] and "AI 生成，非视频原句" in result["markdown"]
    assert len(store.entries()) == 3 and result["stats"]["organized"] == 2
    assert result["stats"]["unorganized"] == 0 and result["stats"]["note_count"] == 1
    assert all(e["organized_at"] for e in result["collections"])
    assert tutor.calls[0]["instructions"] == "重点比较普通话，练习礼貌请求"
    assert "private" not in json.dumps(tutor.calls)
    for entry in result["collections"]:
        assert f"tingmu://entry/{entry['id']}" in result["notes"][0]["markdown"]
    assert manager.organize("yue", tutor) == result and len(tutor.calls) == 1
    store.save_word("new.mkv", Cue("new", 9, 10, "好呀"), Token("好呀"), LanguagePair())
    updated = manager.organize("yue", tutor)
    assert len(tutor.calls[-1]["entries"]) == 1 and updated["stats"]["note_count"] == 2
    assert updated["markdown"].startswith(result["markdown"].rstrip())


def test_explicit_selected_ids_and_reorganize_never_drop_old_notes(book):
    _, manager = book
    original = manager.sync("yue")
    first_id = original["collections"][0]["id"]
    first = manager.organize("yue", Tutor(), entry_ids=[first_id])
    assert first["stats"]["organized"] == first["stats"]["unorganized"] == 1
    second = manager.organize("yue", Tutor(), entry_ids=[first_id], instructions="再讲一下易错点")
    assert second["markdown"].startswith(first["markdown"].rstrip())
    assert second["stats"]["note_count"] == 2 and second["stats"]["organized"] == 1
    foreign_id = manager.snapshot("en")["collections"][0]["id"]
    for ids in ([first_id, first_id], [foreign_id], [999999], [True], "all"):
        with pytest.raises(ValueError):
            manager.organize("yue", Tutor(), entry_ids=ids)
    assert manager.snapshot("yue")["revision"] == second["revision"]


def test_entry_note_revision_language_scope_and_no_markdown_mutation(book):
    store, manager = book
    original = manager.sync("yue")
    entry = original["collections"][1]
    updated = manager.update_entry_note(entry["id"], "我的目标：分清完成体", entry["note_revision"], target_language="yue")
    assert updated["entry_note"] == "我的目标：分清完成体" and updated["note"] == "原收藏备注"
    assert manager.snapshot("yue")["revision"] == original["revision"]
    with pytest.raises(NotebookConflict):
        manager.update_entry_note(entry["id"], "stale", entry["note_revision"], target_language="yue")
    with pytest.raises(ValueError):
        manager.update_entry_note(entry["id"], "wrong language", target_language="en")
    restored = NotebookManager(Store(store.data_dir)).snapshot("yue")
    assert restored["collections"][1]["entry_note"] == "我的目标：分清完成体"
    assert not restored["collections"][1]["organized"]


def test_append_update_delete_notes_preserve_provenance_manual_text_and_backups(book):
    store, manager = book
    initial = manager.sync("yue")
    manual = initial["markdown"] + "\n这部分始终保留。\n"
    manager.save("yue", manual, initial["revision"])
    revision = manager.snapshot("yue")["revision"]
    selected = [initial["collections"][0]["id"]]
    appended = manager.append_note("yue", "语法小结", "我自己的第一版理解。", selected, revision)
    note = appended["notes"][0]
    assert note["body"] == "我自己的第一版理解。" and note["entry_ids"] == selected
    assert "<!--" not in note["markdown"]
    updated = manager.update_note("yue", note["id"], "新的标题", "第二版理解。", appended["revision"])
    assert updated["notes"][0]["body"] == "第二版理解。"
    assert "这部分始终保留。" in updated["markdown"]
    assert f"tingmu://entry/{selected[0]}" in updated["markdown"]
    with pytest.raises(NotebookConflict):
        manager.update_note("yue", note["id"], "old", "overwrite", appended["revision"])
    deleted = manager.delete_note("yue", note["id"], updated["revision"])
    assert not deleted["notes"] and "这部分始终保留。" in deleted["markdown"]
    assert len(store.entries()) == 3 and deleted["collections"][0]["organized"]
    backups = [p.read_text(encoding="utf-8") for p in Path(initial["path"]).parent.glob("backups/*.md")]
    assert appended["markdown"] in backups and updated["markdown"] in backups


def test_markdown_editor_changes_visible_in_note_and_not_overwritten(book):
    _, manager = book
    first = manager.append_note("yue", "标题", "第一版")
    changed = first["markdown"].replace("第一版", "我在 Markdown 编辑器修改的内容。")
    manager.save("yue", changed, first["revision"])
    assert manager.sync("yue")["markdown"] == changed
    assert manager.snapshot("yue")["notes"][0]["body"] == "我在 Markdown 编辑器修改的内容。"
    with pytest.raises(NotebookConflict):
        manager.update_note("yue", first["notes"][0]["id"], "标题", "陈旧版本覆盖", first["revision"])


def test_legacy_v4_migration_is_byte_preserving_and_marks_only_existing_material(tmp_path):
    store = Store(tmp_path)
    for index in range(3):
        store.save_word("movie.mkv", Cue(str(index), index, index + 1, f"词{index}"), Token(f"词{index}"), LanguagePair())
    entries = sorted(store.entries(), key=lambda e: e["id"])
    path = tmp_path / "notebooks/yue/notebook.md"
    path.parent.mkdir(parents=True)
    generated = BEGIN + f"\n## 旧整理\n\n[来源](tingmu://entry/{entries[0]['id']})\n旧内容\n" + END
    raw = ("# 手写旧标题\r\n\r\n自己的旧思考\r\n" + generated + "\r\n").encode("utf-8")
    path.write_bytes(raw)
    with store._connection() as db:
        # Store now creates the whole schema; reconstruct the old table explicitly.
        db.execute("DROP TABLE notebook_state")
        db.execute("PRAGMA user_version=0")
        db.execute("CREATE TABLE notebook_state(language TEXT PRIMARY KEY,topics TEXT,generated TEXT)")
        db.execute("INSERT INTO notebook_state VALUES (?,?,?)",
                   ("yue", json.dumps([{"title": "旧主题", "entry_ids": [entries[0]["id"]]}]), generated))
    manager = NotebookManager(store)
    migrated = manager.sync("yue")
    assert path.read_bytes() == raw and migrated["stats"]["organized"] == 1
    assert migrated["stats"]["unorganized"] == 2 and migrated["notes"][0]["origin"] == "legacy_managed"
    again = NotebookManager(store).snapshot("yue")
    assert again == migrated and path.read_bytes() == raw
    completed = manager.organize("yue", Tutor())
    assert "自己的旧思考" in completed["markdown"] and "旧内容" in completed["markdown"]
    assert completed["stats"]["organized"] == 3 and completed["stats"]["note_count"] == 2
    assert raw in [p.read_bytes() for p in path.parent.glob("backups/*.md")]
    old_note = completed["notes"][0]
    edited = manager.update_note("yue", old_note["id"], "旧笔记新理解", "整理后的新解释", completed["revision"])
    assert f"tingmu://entry/{entries[0]['id']}" in edited["markdown"] and "旧内容" in edited["markdown"]


@pytest.mark.parametrize("mutation", [
    lambda result: result["topics"][0]["entry_ids"].append(9999),
    lambda result: result["topics"][0]["entry_notes"].pop(),
    lambda result: result["topics"][0]["entry_notes"][0].update(entry_id=True),
    lambda result: result["topics"][0].update(learning_note="[虚构来源](tingmu://entry/9999)"),
    lambda result: result["topics"][0]["entry_notes"][0].update(**{key: "" for key in DETAIL_FIELDS}),
])
def test_invalid_model_content_never_marks_organized_or_writes(book, mutation):
    _, manager = book
    before = manager.sync("yue")
    with pytest.raises(ValueError):
        manager.organize("yue", Tutor(alter=mutation))
    assert manager.snapshot("yue") == before


def test_cancel_and_concurrent_entry_note_changes_abort_every_write(book):
    _, manager = book
    before = manager.sync("yue")
    cancelled = [False]
    with pytest.raises(ValueError, match="取消"):
        manager.organize("yue", Tutor(callback=lambda: cancelled.__setitem__(0, True)), cancelled=lambda: cancelled[0])
    assert manager.snapshot("yue") == before
    entry = before["collections"][0]
    with pytest.raises(NotebookConflict):
        manager.organize("yue", Tutor(callback=lambda: manager.update_entry_note(entry["id"], "new preference")))
    after = manager.snapshot("yue")
    assert after["revision"] == before["revision"] and after["notes"] == []
    assert after["stats"]["organized"] == 0


def test_failed_markdown_write_rolls_back_organization_state(book, monkeypatch):
    _, manager = book
    before = manager.sync("yue")
    monkeypatch.setattr(manager, "_write", Mock(side_effect=OSError("mock disk failure")))
    with pytest.raises(OSError):
        manager.organize("yue", Tutor())
    assert manager.snapshot("yue") == before


def test_local_explicit_organize_has_learning_plan_and_preserves_sources(book):
    _, manager = book
    tutor = Mock(provider="local")
    result = manager.organize("yue", tutor, instructions="先复习，再自己造句")
    tutor._complete.assert_not_called()
    assert result["stats"]["organized"] == 2
    assert "学习目标" in result["markdown"] and "先复习，再自己造句" not in result["markdown"]
    assert "尚待进一步分析" in result["markdown"]


def test_organize_instructions_are_generation_context_not_notebook_prose(book):
    _, manager = book
    tutor = Tutor()
    instructions = "重点比较礼貌请求。保留全部来源，不重做视频，不切换模型，整理完成后再保存。" * 15
    result = manager.organize("yue", tutor, instructions=instructions)
    assert tutor.calls[0]["instructions"] == instructions
    assert tutor.calls[0]["source_language_name"] == "简体中文"
    assert tutor.calls[0]["target_language_name"] == "粤语"
    markdown = result["markdown"]
    assert "学习要求：" not in markdown and "不重做视频" not in markdown
    assert "学习关系：简体中文 → 粤语" in markdown
    assert "zh-CN" not in markdown and "zh\\-CN" not in markdown
    assert "已知语言/方言与目标语言/方言对比" in markdown and "教学例句（AI 生成，非视频原句）" in markdown
    prompt = tutor.prompts[0]
    for constraint in ("generation conditions", "Never copy the request", "Never mention field names",
                       "Do not report or promise save/edit/processing status", "Keep uncertain linguistic claims",
                       "Avoid generic or repeated audio-access disclaimers", "untrusted study data"):
        assert constraint in prompt


@pytest.mark.parametrize("language,label", [
    ("yue", "粤语"), ("zh-CN", "简体中文"), ("zh-TW", "繁体中文"), ("en", "英语"),
    ("ja", "日语"), ("ko", "韩语"), ("fr", "法语"), ("es", "西班牙语"), ("de", "德语"),
    ("unknown", "unknown"),
])
def test_topic_relation_uses_readable_names_with_unknown_fallback(language, label):
    topic = {"learning_note": "学习语境与表达。", "entry_notes": []}
    body = render_topic_body(topic, [], "en", language)
    assert body.startswith(f"学习关系：英语 → {label}\n")


def test_renderer_retains_specific_uncertainty_and_user_wording_without_blanket_footer(book):
    _, manager = book
    initial = manager.sync("yue")
    manual = "我的原话：先别保存，我想研究 saved_note 一词。"
    manager.save("yue", initial["markdown"] + manual, initial["revision"])
    uncertainty = "此处可能是客气请求，也可能表示感谢，需要结合前后句判断。"

    def uncertain(result):
        result["topics"][0]["entry_notes"][0]["remark"] = uncertainty

    result = manager.organize("yue", Tutor(alter=uncertain))
    assert manual in result["markdown"]
    assert uncertainty in result["markdown"]
    assert "以上为文本学习建议" not in result["markdown"]
    assert "AI 生成，非视频原句" in result["markdown"]
    assert all(f"tingmu://entry/{entry['id']}" in result["markdown"] for entry in result["collections"])


def test_removing_collection_or_note_never_breaks_archived_source(book):
    store, manager = book
    organized = manager.organize("yue", Tutor())
    entry = organized["collections"][0]
    original = manager.source_entry(entry["id"])
    assert not original["archived"]
    store.delete_entry(entry["id"])
    restored = NotebookManager(Store(store.data_dir))
    archived = restored.source_entry(entry["id"], target_language="yue")
    assert archived["archived"] and archived["cue_id"] == entry["cue_id"]
    assert archived["start"] == entry["start"] and archived["media_path"] == entry["media_path"]
    assert set(archived) == set(original)
    assert "organized_at" not in archived and "note_revision" not in archived
    assert restored.source_entry(entry["id"], target_language="en") is None
    assert f"tingmu://entry/{entry['id']}" in restored.snapshot("yue")["markdown"]
    snapshot = restored.snapshot("yue")
    restored.delete_note("yue", snapshot["notes"][0]["id"], snapshot["revision"])
    assert restored.source_entry(entry["id"])["archived"]
    assert len(store.entries()) == 2


def test_never_organized_collection_is_not_archived(book):
    store, manager = book
    entry = manager.snapshot("yue")["collections"][0]
    store.delete_entry(entry["id"])
    assert manager.source_entry(entry["id"]) is None


def test_replaced_entry_notes_have_recovery_history(book):
    store, manager = book
    entry = manager.snapshot("yue")["collections"][0]
    first = manager.update_entry_note(entry["id"], "我的手写思考", entry["note_revision"])
    manager.update_entry_note(entry["id"], "经明确要求修改的思考", first["note_revision"])
    with store._connection() as db:
        saved = db.execute("SELECT entry_note,revision FROM notebook_entry_note_history WHERE entry_id=?",
                           (entry["id"],)).fetchall()
    assert [tuple(row) for row in saved] == [("我的手写思考", first["note_revision"])]


def test_note_apis_reject_new_fabricated_or_cross_language_links(book):
    _, manager = book
    before = manager.sync("yue")
    foreign = manager.snapshot("en")["collections"][0]["id"]
    for entry_id in (999999, foreign):
        body = f"[来源](tingmu://entry/{entry_id})"
        with pytest.raises(ValueError):
            manager.append_note("yue", "wrong", body)
        with pytest.raises(ValueError):
            manager.save("yue", before["markdown"] + body, before["revision"])
    assert manager.snapshot("yue") == before


def test_duplicate_json_keys_reject_entire_model_note(book):
    _, manager = book
    before = manager.sync("yue")
    tutor = Tutor()
    complete = tutor._complete
    tutor._complete = lambda *args: complete(*args).replace('"topics":', '"topics": [], "topics":', 1)
    with pytest.raises(ValueError):
        manager.organize("yue", tutor)
    assert manager.snapshot("yue") == before
