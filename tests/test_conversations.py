from concurrent.futures import ThreadPoolExecutor
import json

import pytest

from tingmu.conversations import ConversationStore
from tingmu.models import LanguagePair
from tingmu.storage import Store


def test_sessions_scoping_metadata_and_restart(tmp_path):
    store = Store(tmp_path)
    chats = ConversationStore(store)
    first = chats.create(LanguagePair(), "movie.mkv")
    second = chats.create(LanguagePair(), title="另一个")
    english = chats.create(LanguagePair(target="en"))
    message = {"id": "m1", "role": "assistant", "content": "回答", "status": "cancelled",
               "model": "custom", "created_at": "2026-11-14T00:00:00Z", "api_key": "discard-me"}
    chats.save(first["id"], [message])
    restored = ConversationStore(Store(tmp_path))
    assert {s["id"] for s in restored.list_sessions("yue")} == {first["id"], second["id"]}
    assert restored.list_sessions("en")[0]["id"] == english["id"]
    assert restored.load(first["id"])["messages"] == [{k: v for k, v in message.items() if k != "api_key"}]
    restored.rename(first["id"], "新标题")
    assert restored.load(first["id"])["title"] == "新标题"
    restored.delete(second["id"])
    assert len(restored.list_sessions("yue")) == 1


def test_legacy_migration_once_concurrent_and_original_retained(tmp_path):
    store = Store(tmp_path)
    old = [{"role": "user", "content": "旧问题"}]
    store.save_conversation("movie.mkv", LanguagePair(), old)
    chats = ConversationStore(store)
    with ThreadPoolExecutor(max_workers=4) as pool:
        migrated = list(pool.map(lambda _: chats.migrate_legacy("movie.mkv", LanguagePair()), range(8)))
    assert len({s["id"] for s in migrated}) == 1
    assert len(chats.list_sessions("yue")) == 1
    assert store.load_conversation("movie.mkv", LanguagePair()) == old
    assert chats.migrate_legacy("movie.mkv", LanguagePair(target="en")) is None
    chats.delete(migrated[0]["id"])
    assert chats.migrate_legacy("movie.mkv", LanguagePair()) is None
    assert store.load_conversation("movie.mkv", LanguagePair()) == old


def test_invalid_save_is_atomic(tmp_path):
    chats = ConversationStore(Store(tmp_path))
    session = chats.create(LanguagePair())
    chats.save(session["id"], [{"role": "user", "content": "keep"}])
    old = chats.load(session["id"])
    with pytest.raises(ValueError):
        chats.save(session["id"], [{"role": "system", "content": "bad"}])
    with pytest.raises(ValueError):
        chats.save(session["id"], [{"id": "same", "role": "user", "content": "a"}] * 2)
    assert chats.load(session["id"]) == old


@pytest.mark.parametrize("kind", ["reasoning", "summary"])
@pytest.mark.parametrize("phase", ["connecting", "thinking", "acting", "answering"])
def test_public_reasoning_roundtrip_and_model_history_excludes_display_fields(tmp_path, kind, phase):
    from tingmu.companion import _bounded_history
    chats = ConversationStore(Store(tmp_path))
    session = chats.create(LanguagePair())
    messages = [{"role": "user", "content": "Explain this word."},
                {"role": "assistant", "content": "Visible answer.", "status": "stopped",
                 "reasoning_text": "Public provider text, separate from the answer.",
                 "reasoning_kind": kind, "reasoning_elapsed_ms": 123.5, "phase": phase}]
    chats.save(session["id"], messages)
    loaded = ConversationStore(Store(tmp_path)).load(session["id"])["messages"]
    assert all(loaded[1][key] == value for key, value in messages[1].items())
    assert not any(key.startswith("reasoning_") or key == "phase" for key in loaded[0])
    history, omitted = _bounded_history(loaded)
    assert omitted == 0
    assert history == [{"role": item["role"], "content": item["content"]} for item in messages]
    assert "Public provider text" not in json.dumps(history)


@pytest.mark.parametrize("field,value", [
    ("reasoning_text", None), ("reasoning_text", 123), ("reasoning_text", {"text": "not a block API"}),
    ("reasoning_text", ["text"]), ("reasoning_text", "bad\x00text"), ("reasoning_text", "x" * 60001),
    ("reasoning_kind", "thinking"), ("reasoning_kind", None), ("reasoning_kind", []),
    ("phase", "complete"), ("phase", None), ("phase", {}),
    ("reasoning_elapsed_ms", True), ("reasoning_elapsed_ms", "12"), ("reasoning_elapsed_ms", None),
    ("reasoning_elapsed_ms", -1), ("reasoning_elapsed_ms", float("nan")),
    ("reasoning_elapsed_ms", float("inf")), ("reasoning_elapsed_ms", float("-inf")),
    pytest.param("reasoning_elapsed_ms", 1 << 4096, id="elapsed_integer_overflow"),
])
def test_invalid_public_reasoning_save_is_atomic(tmp_path, field, value):
    chats = ConversationStore(Store(tmp_path))
    session = chats.create(LanguagePair())
    before = chats.save(session["id"], [{"role": "assistant", "content": "Keep this answer."}])
    with pytest.raises(ValueError):
        chats.save(session["id"], [{"role": "assistant", "content": "Replace", field: value}])
    assert chats.load(session["id"]) == before


def test_public_reasoning_limit_empty_and_incremental_checkpoint(tmp_path):
    chats = ConversationStore(Store(tmp_path))
    session = chats.create(LanguagePair())
    row = {"id": "answer", "role": "assistant", "content": "", "status": "pending",
           "reasoning_text": "", "reasoning_kind": "summary", "reasoning_elapsed_ms": 0,
           "phase": "thinking"}
    assert chats.save(session["id"], [row])["messages"][0]["reasoning_text"] == ""
    row["reasoning_text"] = "思" * 60000
    stored = chats.save(session["id"], [row])
    assert stored["messages"][0]["reasoning_text"] == row["reasoning_text"]
    row["reasoning_text"] += "考"
    with pytest.raises(ValueError):
        chats.save(session["id"], [row])
    assert chats.load(session["id"]) == stored


def test_public_reasoning_redacts_credentials_and_discards_private_blocks(tmp_path, monkeypatch):
    from tingmu import diagnostics
    monkeypatch.setattr(diagnostics, "_known_secrets", lambda: ("synthetic-configured-credential",))
    store = Store(tmp_path)
    chats = ConversationStore(store)
    session = chats.create(LanguagePair())
    row = {"role": "assistant", "content": "Answer.",
           "reasoning_text": "synthetic-configured-credential sk-testcredential123 Bearer synthetic-token password=fake-pass",
           "reasoning_kind": "summary", "reasoning_content": "private-top-level",
           "signature": "native-signature", "encrypted_content": "ciphertext",
           "_native_content": [{"type": "thinking", "text": "private-native"}],
           "tool_events": [{"name": "read_notebook", "result": {"saved": True, "signature": "nested-signature",
                             "reasoning_content": "private-nested", "encrypted_content": "nested-ciphertext",
                             "blocks": [{"type": "thinking", "text": "native-thought", "signature": "signature"},
                                        {"type": "redacted_thinking", "data": "private-redacted"},
                                        {"type": "reasoning", "content": "private-block"}]}}]}
    result = chats.save(session["id"], [row])["messages"][0]
    assert result["reasoning_text"].count("[REDACTED]") == 4
    assert result["tool_events"][0]["result"]["saved"] is True
    with store._connection() as db:
        raw = db.execute("SELECT messages FROM chat_sessions WHERE id=?", (session["id"],)).fetchone()[0]
    for value in ("synthetic-configured-credential", "sk-testcredential123", "synthetic-token", "fake-pass",
                  "private-top-level", "native-signature", "ciphertext", "private-native", "nested-signature",
                  "private-nested", "native-thought", "private-redacted", "private-block"):
        assert value not in raw


def test_reasoning_redaction_cannot_exceed_accumulated_limit(tmp_path, monkeypatch):
    from tingmu import diagnostics
    monkeypatch.setattr(diagnostics, "redact_text", lambda text: "[REDACTED]" * 7000)
    chats = ConversationStore(Store(tmp_path))
    session = chats.create(LanguagePair())
    with pytest.raises(ValueError):
        chats.save(session["id"], [{"role": "assistant", "content": "", "reasoning_text": "short"}])
    assert chats.load(session["id"])["messages"] == []
