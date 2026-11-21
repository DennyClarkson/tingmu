import json
from pathlib import Path

import pytest

from tingmu.models import Cue, LanguagePair, Token
from tingmu.notebooks import NotebookConflict, NotebookManager
from tingmu.storage import Store


@pytest.fixture
def library(tmp_path):
    store = Store(tmp_path)
    for target, text in (("yue", "唔該"), ("yue", "食飯"), ("en", "thank you")):
        store.save_word("private/movie.mkv", Cue(text, 1, 2, text), Token(text, "reading", "meaning"),
                        LanguagePair(target=target))
    store.save_note("private/movie.mkv", Cue("sentence", 3, 4, "食咗飯未？"), "用户收藏的笔记", LanguagePair())
    return store, NotebookManager(store)


def test_sync_idempotent_language_sources_and_manual_edits(library):
    store, books = library
    first = books.sync("yue")
    assert first == books.sync("yue")
    assert "thank you" not in first["markdown"]
    assert "唔該" not in books.sync("en")["markdown"]
    assert first["stats"]["total"] == first["stats"]["unorganized"] == 3
    assert first["stats"]["note_count"] == first["stats"]["organized"] == 0
    for entry in first["entries"]:
        assert f"tingmu://entry/{entry['id']}" not in first["markdown"]
    first = books.organize("yue", Organizer())
    saved = books.save("yue", first["markdown"] + "\n私人例句不能丢。\n", first["revision"])
    store.save_word("second.mkv", Cue("new", 5, 6, "好"), Token("好"), LanguagePair())
    synced = books.sync("yue")
    assert "私人例句不能丢。" in synced["markdown"]
    assert synced == books.sync("yue")
    edited = synced["markdown"].replace("**读音**：reading", "我的读音批注", 1)
    books.save("yue", edited, synced["revision"])
    final = books.sync("yue")
    assert "我的读音批注" in final["markdown"]
    assert final == books.sync("yue")
    backups = list(Path(saved["path"]).parent.glob("backups/*.md"))
    assert backups and any(p.read_text(encoding="utf-8") == first["markdown"] for p in backups)


def test_revision_conflict_external_editor_and_invalid_language(library):
    _, books = library
    first = books.sync("yue")
    Path(first["path"]).write_text(first["markdown"] + "\n外部编辑", encoding="utf-8")
    with pytest.raises(NotebookConflict):
        books.save("yue", "overwrite", first["revision"])
    assert "外部编辑" in books.snapshot("yue")["markdown"]
    with pytest.raises(ValueError):
        books.sync("../../outside")


class Organizer:
    provider = "test"

    def __init__(self, transform=lambda ids: ids, callback=None):
        self.transform = transform
        self.payloads = []
        self.callback = callback

    def _complete(self, prompt, payload, schema):
        self.payloads.append(payload)
        if self.callback:
            self.callback()
        return json.dumps({"topics": [{"title": "生活表达", "entry_ids": self.transform(
            [e["id"] for e in payload["entries"]]), "learning_note": "把表达放回交际场景，比较普通话的使用差异。",
            "entry_notes": [{"entry_id": e["id"], "comparison": "与普通话的表达习惯有差异。",
                             "usage": "结合上下文练习日常表达。", "grammar": "", "pitfalls": "不要逐字硬译。",
                             "example": "教学示例", "example_translation": "这是新造的示例，不是原片引文。",
                             "goal": "能根据场景选择表达。", "remark": e.get("entry_note", "")}
                            for e in payload["entries"]]}]})


def test_organize_preserves_manual_and_provenance(library):
    _, books = library
    first = books.sync("yue")
    books.save("yue", first["markdown"] + "\n自由创作", first["revision"])
    agent = Organizer()
    result = books.organize("yue", agent)
    assert result["topics"][0]["title"] == "生活表达"
    assert "自由创作" in result["markdown"]
    for entry in result["entries"]:
        assert f"tingmu://entry/{entry['id']}" in result["markdown"]
    assert "private" not in json.dumps(agent.payloads)
    assert books.sync("yue") == result


@pytest.mark.parametrize("transform", [lambda ids: ids[:-1], lambda ids: ids + [ids[0]],
                                      lambda ids: ids + [9999], lambda ids: [str(i) for i in ids],
                                      lambda ids: [True] + ids[1:]])
def test_organize_rejects_invalid_ids_without_changes(library, transform):
    _, books = library
    first = books.sync("yue")
    with pytest.raises(ValueError):
        books.organize("yue", Organizer(transform))
    assert books.snapshot("yue") == first


def test_organize_detects_concurrent_editor_or_source_changes(library):
    store, books = library
    first = books.sync("yue")
    with pytest.raises(NotebookConflict):
        books.organize("yue", Organizer(callback=lambda: books.save(
            "yue", first["markdown"] + "\nchanged", first["revision"])))
    with pytest.raises(NotebookConflict):
        books.organize("yue", Organizer(callback=lambda: store.delete_entry(first["entries"][0]["id"])))


def test_review_persistence_and_languages(library):
    store, books = library
    cards = books.practice("yue")
    assert len(cards) == 3
    result = books.review(cards[0]["id"], "good")
    assert result["interval_days"] == 2
    restored = NotebookManager(Store(store.data_dir))
    assert len(restored.practice("yue")) == 2
    assert len(restored.practice("en")) == 1
    again = restored.review(cards[0]["id"], "again")
    assert again["repetitions"] == 2 and again["interval_days"] < 1
    with pytest.raises(ValueError):
        restored.review(99999, "easy")


def test_all_entries_organized_in_bounded_batches(library):
    store, books = library
    for n in range(90):
        store.save_note("movie.mkv", Cue(f"c{n}", n, n+1, f"句子{n}"), "说明", LanguagePair())
    agent = Organizer()
    result = books.organize("yue", agent)
    assert len(agent.payloads) == 8
    assert sum(len(t["entry_ids"]) for t in result["topics"]) == 93


def test_cancelled_organize_never_writes(library):
    _, books = library
    first = books.sync("yue")
    agent = Organizer()
    with pytest.raises(ValueError, match="取消"):
        books.organize("yue", agent, cancelled=lambda: True)
    assert not agent.payloads
    assert books.snapshot("yue") == first
    with pytest.raises(ValueError, match="取消"):
        books.organize("yue", agent, cancelled=lambda: bool(agent.payloads))
    assert books.snapshot("yue") == first


@pytest.mark.parametrize(("language", "title"), [
    ("yue", "粤语学习笔记"), ("zh-CN", "简体中文学习笔记"), ("zh-TW", "繁体中文学习笔记"),
    ("en", "英语学习笔记"), ("ja", "日语学习笔记"), ("ko", "韩语学习笔记"),
    ("fr", "法语学习笔记"), ("es", "西班牙语学习笔记"), ("de", "德语学习笔记"),
    ("it", "意大利语学习笔记"), ("x-study", "x-study 学习笔记"),
])
def test_localized_titles_keep_language_identity_and_paths(tmp_path, language, title):
    books = NotebookManager(Store(tmp_path))
    result = books.sync(language)
    assert result["title"] == title and result["markdown"].startswith(f"# {title}\n")
    assert result["language"] == language and Path(result["path"]).parent.name == language
    assert result["books"] == [{"language": language, "title": title, "count": 0}]


def test_localized_default_never_rewrites_existing_manual_title(library):
    _, books = library
    first = books.sync("yue")
    manual = first["markdown"].replace("# 粤语学习笔记", "# 我的粤语手帐", 1)
    books.save("yue", manual, first["revision"])
    assert books.sync("yue")["markdown"] == manual
    assert books.organize("yue", Organizer())["markdown"].startswith("# 我的粤语手帐\n")
