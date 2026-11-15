import io
import json

import pytest

from tingmu.agent import LearningAgent, AnnotationValidationError
from tingmu.conversations import ConversationStore
from tingmu.models import AppSettings, Cue, LanguagePair
from tingmu.providers import _wire_body, _stream_response, ProviderError
from tingmu.storage import Store


def wire(provider, effort, protocol="openai"):
    return _wire_body({"provider": provider, "protocol": protocol},
        {"model": "test-model", "messages": [], "max_completion_tokens": 4096,
         "reasoning_effort": effort, "thinking": {"type": "disabled"}})


@pytest.mark.parametrize("provider", ["openai", "custom", "gemini", "ollama"])
def test_reasoning_is_sent_for_compatible_services(provider):
    assert wire(provider, "high")["reasoning_effort"] == "high"


def test_provider_specific_reasoning_is_not_silently_discarded():
    deepseek = wire("deepseek", "high")
    assert deepseek["thinking"] == {"type": "enabled"}
    assert deepseek["reasoning_effort"] == "high"
    assert "reasoning_effort" not in wire("deepseek", "none")
    assert wire("deepseek", "none")["thinking"] == {"type": "disabled"}
    qwen = wire("qwen", "high")
    assert qwen["enable_thinking"] and qwen["thinking_budget"] == 16384
    assert "reasoning_effort" not in qwen
    assert wire("qwen", "none")["enable_thinking"] is False
    claude = wire("anthropic", "high", "anthropic")
    assert claude["thinking"] == {"type": "adaptive"}
    assert claude["output_config"] == {"effort": "high"}
    assert wire("anthropic", "none", "anthropic")["thinking"] == {"type": "disabled"}
    with pytest.raises(ProviderError):
        wire("custom", "invented")


def test_native_thinking_signatures_survive_tool_rounds_without_display():
    events = [
        {"type": "message_start", "message": {"model": "native"}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "private reasoning"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "signed"}},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": "Visible"}},
        {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "tool-1", "name": "read_notebook", "input": {}}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": "{}"}},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    ]
    stream = io.BytesIO("".join("data: " + json.dumps(e) + "\n\n" for e in events).encode())
    displayed = []
    result = _stream_response({"protocol": "anthropic"}, stream, displayed.append, None)
    message = result["choices"][0]["message"]
    assert "".join(displayed) == message["content"] == "Visible"
    outbound = _wire_body({"protocol": "anthropic"}, {"model": "native", "messages": [message]})
    assert outbound["messages"][0]["content"][0] == {
        "type": "thinking", "thinking": "private reasoning", "signature": "signed"}
    assert outbound["messages"][0]["content"][2]["input"] == {}


def test_teaching_preferences_reach_teacher_without_weakening_preservation(monkeypatch):
    agent = LearningAgent("local")
    agent.teaching_preferences = "详细比较母语与目标语言，提供例句。"
    agent.preserve_text = True
    cue = Cue("source", 1, 2, "你好")
    calls = []

    def complete(system, payload, schema):
        calls.append((system, payload))
        agent.last_response_metadata = {}
        return json.dumps({"cues": [{"id": "source", "text": "被改寫", "translation": "hello",
            "explanation": "explanation", "uncertain": False,
            "tokens": [{"text": "被改寫", "reading": "", "meaning": "", "learn": False}]}]})

    monkeypatch.setattr(agent, "_complete", complete)
    with pytest.raises(AnnotationValidationError, match="已复核正文"):
        agent._annotate_batch([cue], LanguagePair(), 0, 1, lambda: False, [cue.text], {cue.id: 0})
    assert len(calls) == 2
    assert all(p["learner_teaching_preferences"] == agent.teaching_preferences for _, p in calls)
    assert all("never authorize changing subtitle IDs" in s for s, _ in calls)


def test_settings_and_safe_chat_context_survive_restart(tmp_path):
    store = Store(tmp_path)
    settings = AppSettings(companion_reasoning_effort="high", teaching_preferences="详细讲解" * 100)
    store.save_settings(settings)
    assert Store(tmp_path).load_settings() == settings
    chats = ConversationStore(store)
    session = chats.create(settings.language)
    chats.save(session["id"], [{"role": "user", "content": "整理收藏", "reasoning_effort": "high",
        "request_model": {"id": "chosen", "model": "model", "_key": "secret", "api_key": "secret"},
        "request_context": {"language": {"source": "zh-CN", "target": "yue"}, "cue": {"id": "a", "text": "你好"}},
        "tool_events": [{"name": "organize_notebook", "status": "success"}]}])
    saved = ConversationStore(Store(tmp_path)).load(session["id"])["messages"][0]
    assert saved["reasoning_effort"] == "high"
    assert saved["request_context"]["cue"]["id"] == "a"
    assert saved["tool_events"][0]["status"] == "success"
    assert saved["request_model"] == {"id": "chosen", "model": "model"}
