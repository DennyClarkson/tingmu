"""Visible provider thinking is live, scoped, durable and absent from diagnostics."""
import threading

import pytest

from tingmu.diagnostics import register_secret, safe_fields
from tingmu.quick.bridge import Bridge
from tingmu.storage import Store


@pytest.fixture
def bridge(qapp, tmp_path):
    obj = Bridge(Store(tmp_path / "data"))
    yield obj
    obj.shutdown()


def test_thinking_then_tool_then_answer_remain_separate_and_reload(bridge, qtbot, monkeypatch):
    think_ready, tool_ready = threading.Event(), threading.Event()
    continue_tool, continue_answer = threading.Event(), threading.Event()
    original = bridge._active_session_id

    def reply(self, *args, on_reasoning, on_tool_event, on_delta, **kwargs):
        on_reasoning({"kind": "summary", "text": "Comparing the expressions."})
        think_ready.set()
        assert continue_tool.wait(5)
        on_tool_event({"name": "read_notebook", "label": "读取笔记", "status": "running"})
        tool_ready.set()
        assert continue_answer.wait(5)
        on_tool_event({"name": "read_notebook", "label": "读取笔记", "status": "completed"})
        on_delta("Final explanation.")
        return "Final explanation."

    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", reply)
    try:
        bridge._v4_send_chat("Compare these expressions")
        qtbot.waitUntil(think_ready.is_set)
        qtbot.waitUntil(lambda: bridge._messages[-1].get("phase") == "thinking")
        assert bridge._messages[-1]["content"] == ""
        assert bridge._messages[-1]["reasoning_text"] == "Comparing the expressions."
        qtbot.wait(120)
        continue_tool.set()
        qtbot.waitUntil(tool_ready.is_set)
        qtbot.waitUntil(lambda: bridge._messages[-1].get("phase") == "acting")
        assert bridge._messages[-1]["reasoning_elapsed_ms"] >= 100
        continue_answer.set()
        qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    finally:
        continue_tool.set()
        continue_answer.set()
    saved = bridge.conversations.load(original)["messages"][-1]
    assert saved["content"] == "Final explanation."
    assert saved["reasoning_text"] == "Comparing the expressions."
    assert saved["reasoning_kind"] == "summary" and saved["phase"] == "answering"
    bridge._feature_command("newChat", {})
    bridge._select_chat(original)
    assert bridge._messages[-1] == saved


def test_cancel_thinking_after_session_switch_preserves_only_original(bridge, qtbot, monkeypatch):
    ready, finish = threading.Event(), threading.Event()
    original = bridge._active_session_id

    def reply(self, *args, on_reasoning, cancelled, **kwargs):
        on_reasoning({"kind": "reasoning", "text": "Visible partial thought"})
        ready.set()
        assert finish.wait(5)
        assert cancelled()
        on_reasoning({"kind": "reasoning", "text": " must not append after cancellation"})
        raise RuntimeError("cancelled transport")

    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", reply)
    try:
        bridge._v4_send_chat("Question")
        qtbot.waitUntil(ready.is_set)
        qtbot.waitUntil(lambda: bool(bridge._messages[-1].get("reasoning_text")))
        bridge._feature_command("newChat", {})
        selected = bridge._active_session_id
        bridge._feature_command("stopChat", {})
    finally:
        finish.set()
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    assert bridge._active_session_id == selected and bridge._messages == []
    saved = bridge.conversations.load(original)["messages"][-1]
    assert saved["status"] == "stopped"
    assert saved["reasoning_text"] == "Visible partial thought"
    assert saved["content"] == ""


def test_thinking_is_bounded_and_redacted_before_display_and_save(bridge, qtbot, monkeypatch):
    secret = "synthetic-display-secret-for-thinking-12345"
    register_secret(secret)

    def reply(self, *args, on_reasoning, **kwargs):
        on_reasoning({"kind": "private", "text": "NEVER DISPLAY"})
        on_reasoning({"kind": "reasoning", "text": "bad\x00fragment"})
        on_reasoning({"kind": "reasoning", "text": secret[:20]})
        on_reasoning({"kind": "reasoning", "text": secret[20:] + "a" * 70000})
        return "Done"

    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", reply)
    bridge._v4_send_chat("Question")
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    saved = bridge.conversations.load(bridge._active_session_id)["messages"][-1]
    assert len(saved["reasoning_text"]) <= 60000
    assert saved["reasoning_text"].startswith("[REDACTED]")
    assert saved["phase"] == "answering"
    assert secret not in saved["reasoning_text"]
    assert "NEVER DISPLAY" not in saved["reasoning_text"] and "\x00" not in saved["reasoning_text"]
    assert bridge._messages[-1]["reasoning_text"] == saved["reasoning_text"]


def test_split_credential_never_reaches_first_display_or_checkpoint(bridge, qtbot, monkeypatch):
    ready, finish = threading.Event(), threading.Event()
    secret = "synthetic-credential-held-until-complete-987654"
    register_secret(secret)
    original = bridge._active_session_id

    def reply(self, *args, on_reasoning, **kwargs):
        on_reasoning({"kind": "reasoning", "text": "Checking " + secret[:-1]})
        ready.set()
        assert finish.wait(5)
        on_reasoning({"kind": "reasoning", "text": secret[-1:] + " safely."})
        return "Finished"

    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", reply)
    try:
        bridge._v4_send_chat("Question")
        qtbot.waitUntil(ready.is_set)
        qtbot.waitUntil(lambda: bridge._messages[-1].get("phase") == "thinking")
        assert bridge._messages[-1]["reasoning_text"] == "Checking "
        checkpoint = bridge.conversations.load(original)["messages"][-1]
        assert checkpoint["reasoning_text"] == "Checking "
    finally:
        finish.set()
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    final = bridge.conversations.load(original)["messages"][-1]
    assert final["reasoning_text"] == "Checking [REDACTED] safely."


def test_diagnostics_omit_visible_and_private_reasoning():
    fields = {key: "sensitive model text" for key in (
        "reasoning_text", "reasoning_content", "reasoning_summary", "thinking", "summary",
        "signature", "encrypted_content", "redacted_thinking", "_native_content", "_native_response", "_responses_output")}
    assert set(safe_fields(fields).values()) == {"[OMITTED]"}
