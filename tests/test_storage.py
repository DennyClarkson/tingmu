from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import csv

from tingmu.models import Cue, LanguagePair, StudyDocument, Token
from tingmu.storage import Store


def test_document_round_trip_invalidation_and_language_isolation(tmp_path):
    movie = tmp_path / "剧集.mkv"
    movie.write_bytes(b"sample")
    store = Store(tmp_path / "data")
    cue = Cue("c1", 1.2, 3.4, "唔該", tokens=[Token("唔該", "m4 goi1")])
    doc = StudyDocument(str(movie), LanguagePair(), [cue])
    store.save_document(doc)
    restored = store.load_document(str(movie), LanguagePair())
    assert restored.cues[0].tokens[0].reading == "m4 goi1"
    assert restored.cues[0].original_text == "唔該"
    assert store.load_document(str(movie), LanguagePair(target="en")) is None
    movie.write_bytes(b"replacement-media")
    assert store.load_document(str(movie), LanguagePair()) is None


def test_concurrent_saves_dedupe_and_note_update(tmp_path):
    store = Store(tmp_path / "data")
    cue = Cue("c1", 1, 3, "唔該晒")
    token = Token("唔該", "m4 goi1", "谢谢")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: store.save_word("movie.mkv", cue, token, LanguagePair()), range(20)))
    assert len(store.entries()) == 1
    store.save_note("movie.mkv", cue, "初稿", LanguagePair())
    store.save_note("movie.mkv", cue, "修改后", LanguagePair())
    assert len(store.entries()) == 2
    assert next(e for e in store.entries() if e["kind"] == "note")["note"] == "修改后"
    store.save_word("movie.mkv", cue, token, LanguagePair(source="en"))
    assert len(store.entries()) == 3
    store.delete_entry(store.entries()[0]["id"])
    assert len(store.entries()) == 2


def test_exports_and_resume(tmp_path):
    store = Store(tmp_path / "data")
    cue = Cue("c1", 10, 12, "食咗飯未？")
    store.save_note("movie.mkv", cue, "=1+1", LanguagePair())
    exported = store.export_csv(tmp_path / "notes.csv")
    with exported.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream))
    assert row["note"] == "'=1+1"
    assert "食咗飯未" in store.export_markdown(tmp_path / "notes.md").read_text(encoding="utf-8")
    store.save_position("movie.mkv", 1234)
    assert Store(tmp_path / "data").load_position("movie.mkv") == 1234


def test_settings_unknown_keys_are_ignored(tmp_path):
    store = Store(tmp_path)
    (tmp_path / "settings.json").write_text('{"target_language":"en","future_setting":true}', encoding="utf-8")
    assert store.load_settings().target_language == "en"
    store.save_settings(replace(store.load_settings(), source_language="en"))
    assert store.load_settings().source_language == "en"


def test_conversation_is_persistent_and_scoped_by_media_and_language(tmp_path):
    store = Store(tmp_path)
    messages = [{"role": "user", "content": "點解用咗？"}, {"role": "assistant", "content": "表示完成。"}]
    store.save_conversation("movie.mkv", LanguagePair(), messages)
    assert Store(tmp_path).load_conversation("movie.mkv", LanguagePair()) == messages
    assert store.load_conversation("other.mkv", LanguagePair()) == []
    assert store.load_conversation("movie.mkv", LanguagePair(target="en")) == []
    store.save_conversation("", LanguagePair(), [{"role": "user", "content": "怎么导入字幕？"}])
    assert store.load_conversation("", LanguagePair())[0]["content"] == "怎么导入字幕？"
