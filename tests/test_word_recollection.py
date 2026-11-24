from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from tingmu.models import Cue, LanguagePair, Token
from tingmu.notebooks import NotebookManager
from tingmu.storage import Store


PAIR = LanguagePair()
WORD = Token("唔該", "m4 goi1", "劳驾")
ONE = Cue("1", 1, 2, "唔該晒。")
TWO = Cue("2", 12, 13, "唔該，借借。")


def test_recollect_organized_word_adds_one_real_link_and_requeues(tmp_path):
    store = Store(tmp_path)
    # This occurrence predates word-level collection status.
    store.save_word("one.mkv", ONE, WORD, PAIR)
    book = NotebookManager(store)
    original_id = store.entries()[0]["id"]
    organized = book.append_note("yue", "礼貌用语", "手写说明", [original_id])
    edited = organized["markdown"].replace("手写说明", "我在编辑器里写的说明。") + "\n手写结尾\n"
    book.save("yue", edited, organized["revision"])
    assert book.word_collection_states("two.mkv", TWO.id, [WORD.text, "借借"], PAIR) == {
        WORD.text: "organized", "借借": "unsaved",
    }
    assert book.collect_word("two.mkv", TWO, WORD, PAIR) == "confirm"
    assert len(store.entries()) == 1 and Path(organized["path"]).read_text("utf-8") == edited
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: book.collect_word("two.mkv", TWO, WORD, PAIR, confirm=True), range(12)))
    assert results.count("referenced") == 1 and results.count("pending") == 11
    restarted = NotebookManager(Store(tmp_path))
    snap = restarted.snapshot("yue")
    first, second = snap["collections"]
    assert first["organized"] and not second["organized"]
    assert restarted.word_collection_states("two.mkv", TWO.id, [WORD.text], PAIR)[WORD.text] == "pending"
    note, = snap["notes"]
    assert note["entry_ids"] == [original_id, second["id"]]
    assert note["body"] == "我在编辑器里写的说明。" and snap["markdown"].endswith("\n手写结尾\n")
    assert all(note["sources_markdown"].count(f"(tingmu://entry/{row['id']})") == 1 for row in (first, second))
    assert [s["sentence"] for s in second["word_sources"]] == [ONE.text, TWO.text]
    assert book.source_entry(second["id"])["start"] == TWO.start
    # Editing the prose must not consume the pending recollection.
    updated = restarted.update_note("yue", note["id"], "礼貌", "继续学", snap["revision"])
    assert updated["stats"]["unorganized"] == 1
    # Same organized original sentence can be requeued without inventing another citation.
    assert restarted.collect_word("one.mkv", ONE, WORD, PAIR) == "confirm"
    assert restarted.collect_word("one.mkv", ONE, WORD, PAIR, confirm=True) == "referenced"
    same = restarted.snapshot("yue")
    assert same["markdown"] == updated["markdown"] and same["stats"]["unorganized"] == 2
    assert len(store.entries()) == 2


def test_pending_occurrences_are_unique_and_scoped(tmp_path):
    store = Store(tmp_path / "a")
    book = NotebookManager(store)
    assert book.collect_word("one.mkv", ONE, WORD, PAIR) == "collected"
    assert book.collect_word("one.mkv", ONE, WORD, PAIR) == "pending"
    # Shared cue IDs in different videos are independent sources.
    assert book.collect_word("two.mkv", ONE, WORD, PAIR) == "pending_source"
    assert book.collect_word("two.mkv", ONE, WORD, PAIR) == "pending"
    assert len(book.snapshot("yue")["collections"][0]["word_sources"]) == 2
    for pair in (LanguagePair(source="en"), LanguagePair(target="en")):
        assert book.word_collection_states("one.mkv", ONE.id, [WORD.text], pair)[WORD.text] == "unsaved"
        assert book.collect_word("one.mkv", ONE, WORD, pair) == "collected"
    other = NotebookManager(Store(tmp_path / "b"))
    assert other.word_collection_states("one.mkv", ONE.id, [WORD.text], PAIR)[WORD.text] == "unsaved"
    assert book.snapshot("yue")["markdown"] == ""


def test_archived_word_source_keeps_id_and_remark_when_recollected(tmp_path):
    store = Store(tmp_path)
    book = NotebookManager(store)
    book.collect_word("one.mkv", ONE, WORD, PAIR)
    source_id = store.entries()[0]["id"]
    book.update_entry_note(source_id, "我的备注")
    organized = book.append_note("yue", "礼貌", "用法", [source_id])
    store.delete_entry(source_id)
    assert book.word_collection_states("one.mkv", ONE.id, [WORD.text], PAIR)[WORD.text] == "organized"
    assert book.collect_word("one.mkv", ONE, Token(WORD.text), PAIR, confirm=True) == "referenced"
    entry, = book.snapshot("yue")["collections"]
    assert entry["id"] == source_id and entry["entry_note"] == "我的备注" and entry["reading"] == WORD.reading
    assert not entry["organized"] and book.snapshot("yue")["markdown"] == organized["markdown"]


def test_detached_user_note_and_rollback_on_reference_write_failure(tmp_path, monkeypatch):
    store = Store(tmp_path)
    book = NotebookManager(store)
    book.collect_word("one.mkv", ONE, WORD, PAIR)
    source_id = store.entries()[0]["id"]
    original = book.append_note("yue", "礼貌", "用法", [source_id])
    detached = original["markdown"].replace(":start -->", ":我的标记 -->")
    book.save("yue", detached, original["revision"])
    with monkeypatch.context() as patch:
        def fail(*args):
            raise OSError("write failed")
        patch.setattr(book, "_write", fail)
        with pytest.raises(OSError):
            book.collect_word("two.mkv", TWO, WORD, PAIR, confirm=True)
    assert len(store.entries()) == 1 and book.snapshot("yue")["markdown"] == detached
    assert book.collect_word("two.mkv", TWO, WORD, PAIR, confirm=True) == "referenced"
    snap = book.snapshot("yue")
    assert snap["markdown"].startswith(detached)
    assert snap["notes"][0]["detached"] and len(snap["notes"][1]["entry_ids"]) == 2
    assert snap["stats"]["unorganized"] == 1


def test_old_spelling_variants_share_sources_without_losing_case_or_accents(tmp_path):
    pair = LanguagePair(target="en")
    first = Cue("a", 0, 1, "Nice to meet you.")
    second = Cue("b", 1, 2, "That's nice.")
    store = Store(tmp_path)
    store.save_word("sample.mkv", first, Token("Nice"), pair)
    before = NotebookManager(store)
    entry_id = store.entries()[0]["id"]
    before.append_note("en", "A greeting", "Written before the upgrade.", [entry_id])
    store.delete_entry(entry_id)
    # Simulate a pre-index database whose only spelling is in an archived source.
    with store._connection() as db:
        db.execute("DROP TABLE word_forms")
        db.execute("PRAGMA user_version=0")  # Model the pre-versioned schema, not damaged current schema.
    book = NotebookManager(Store(tmp_path))
    assert book.word_collection_states("sample.mkv", second.id, ["nice", "Nice", "NICE"], pair) == {
        "nice": "organized", "Nice": "organized", "NICE": "unsaved",
    }
    assert book.collect_word("sample.mkv", second, Token("nice"), pair, confirm=True) == "referenced"
    snap = book.snapshot("en")
    assert [s["text"] for s in snap["collections"][0]["word_sources"]] == ["Nice", "nice"]
    assert [book.source_entry(i)["text"] for i in snap["notes"][0]["entry_ids"]] == ["Nice", "nice"]
    assert [(s["text"], s["archived"], s["start"]) for s in snap["note_sources"]] == [
        ("Nice", True, first.start), ("nice", False, second.start),
    ]
    corrected = Cue("b", 1, 2, "Nice.")
    assert book.collect_word("sample.mkv", corrected, Token("Nice"), pair) == "pending"
    assert book.snapshot("en")["revision"] == snap["revision"]
    french = LanguagePair(target="fr")
    book.collect_word("sample.mkv", Cue("f", 2, 3, "Éclat"), Token("Éclat"), french)
    assert book.word_collection_states("sample.mkv", "g", ["e\u0301clat", "eclat"], french) == {
        "e\u0301clat": "pending", "eclat": "unsaved",
    }


def test_organizing_selected_word_includes_both_pending_contexts(tmp_path):
    pair = LanguagePair(target="en")
    book = NotebookManager(Store(tmp_path))
    assert book.collect_word("movie", Cue("c1", 1, 2, "Nice!"), Token("Nice"), pair) == "collected"
    assert book.collect_word("movie", Cue("c2", 3, 4, "How nice."), Token("nice"), pair) == "pending_source"
    ids = [e["id"] for e in book.snapshot("en")["collections"]]
    result = book.organize("en", SimpleNamespace(provider="local"), entry_ids=ids[:1])
    note, = result["notes"]
    assert note["entry_ids"] == ids and "How nice" in note["sources_markdown"]
    assert all(note["sources_markdown"].count(f"(tingmu://entry/{i})") == 1 for i in ids)
    assert [r["organized"] for r in result["collections"]] == [True, False]
    final = book.organize("en", SimpleNamespace(provider="local"))
    assert final["stats"]["unorganized"] == 0 and set(final["notes"][-1]["entry_ids"]) == set(ids)
