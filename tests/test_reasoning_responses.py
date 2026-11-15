"""Public reasoning callbacks and opt-in Responses transport; no real network."""
import io
import json
from unittest.mock import patch

import pytest

from tingmu.agent import LearningAgent
from tingmu.companion import CompanionAgent
from tingmu.models import LanguagePair
from tingmu.providers import ModelRegistry, ProviderError, RequestCancelled, _wire_body, request


def config(**overrides):
    return {"provider": "openai", "protocol": "responses", "model": "chosen",
            "base_url": "https://example.invalid/v1", "key_source": "environment", "_key": "test-secret-key", **overrides}


def sse(*events):
    return io.BytesIO("".join("data: " + (e if isinstance(e, str) else json.dumps(e)) + "\n\n" for e in events).encode())


def result(output, **changes):
    return {"id": "response-id", "model": "returned", "status": "completed", "error": None,
            "output": output, "usage": {"input_tokens": 4, "output_tokens": 8}, **changes}


def message(text="answer", ident="message-id"):
    return {"id": ident, "type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": []}]}


def reasoning(text="public summary"):
    return {"id": "reason-id", "type": "reasoning", "summary": [{"type": "summary_text", "text": text}], "encrypted_content": "PRIVATE_ENCRYPTED"}


def function_call():
    return {"type": "function_call", "id": "function-item", "call_id": "call-one", "name": "read_notebook", "arguments": "{}"}


def tool():
    return {"type": "function", "function": {"name": "read_notebook", "description": "read", "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}


def test_responses_is_explicit_profile_option_preserving_existing_default(tmp_path):
    registry = ModelRegistry(tmp_path)
    old = registry.save_profile({"provider": "openai", "model": "old"})
    assert old["protocol"] == "openai"
    new = registry.save_profile({"provider": "openai", "protocol": "responses", "model": "chosen"})
    assert registry.resolve(new["id"])["protocol"] == "responses"
    assert registry.resolve(old["id"])["protocol"] == "openai"


@pytest.mark.parametrize("fmt,expected", [
    ({"type": "json_object"}, {"type": "json_object"}),
    ({"type": "json_schema", "json_schema": {"name": "contract", "strict": True, "schema": {"type": "object"}}},
     {"type": "json_schema", "name": "contract", "strict": True, "schema": {"type": "object"}}),
])
def test_responses_request_mapping_tools_constraints_and_private_roundtrip(fmt, expected):
    outputs = [reasoning(), function_call()]
    body = {"model": "chosen", "max_completion_tokens": 2048, "reasoning_effort": "low", "stream": True,
            "messages": [{"role": "system", "content": "rules"}, {"role": "user", "content": "request"},
                         {"role": "assistant", "content": None, "_responses_output": outputs},
                         {"role": "tool", "tool_call_id": "call-one", "content": '{"count":1}'}],
            "tools": [tool()], "tool_choice": {"type": "function", "function": {"name": "read_notebook"}}, "response_format": fmt}
    wire = _wire_body(config(), body)
    assert wire["reasoning"] == {"summary": "auto", "effort": "low"}
    assert wire["include"] == ["reasoning.encrypted_content"] and wire["store"] is False
    assert wire["max_output_tokens"] == 2048 and wire["text"]["format"] == expected
    assert "messages" not in wire and "response_format" not in wire
    assert wire["input"][2:4] == outputs
    assert wire["input"][-1] == {"type": "function_call_output", "call_id": "call-one", "output": '{"count":1}'}
    assert wire["tools"][0]["name"] == "read_notebook" and wire["tools"][0]["strict"] is False
    assert wire["tool_choice"] == {"type": "function", "name": "read_notebook"}


def test_normal_responses_summary_visible_opaque_state_private_and_correct_url():
    events = []
    payload = result([reasoning(), message()])
    with patch("tingmu.providers._open", return_value=io.BytesIO(json.dumps(payload).encode())) as opener:
        answer = request(config(), "/chat/completions", {"model": "chosen", "messages": []}, on_reasoning=events.append)
    assert opener.call_args.args[0].full_url == "https://example.invalid/v1/responses"
    assert events == [{"kind": "summary", "text": "public summary"}]
    assert answer["choices"][0]["message"]["content"] == "answer"
    assert answer["choices"][0]["message"]["_responses_output"] == payload["output"]
    assert "PRIVATE_ENCRYPTED" not in json.dumps(events)


def test_responses_sse_summary_is_live_and_final_summary_is_not_duplicated():
    public, text, order = [], [], []
    def callback(event):
        public.append(event)
        order.append("summary")
    def delta(value):
        text.append(value)
        order.append("answer")
    stream = sse({"type": "response.created", "response": {"status": "in_progress"}},
                 {"type": "response.reasoning_summary_text.delta", "output_index": 0, "summary_index": 0, "delta": "public "},
                 {"type": "response.reasoning_summary_text.delta", "output_index": 0, "summary_index": 0, "delta": "summary"},
                 {"type": "response.reasoning_text.delta", "delta": "PRIVATE_RAW"},
                 {"type": "response.reasoning_summary_text.done", "output_index": 0, "summary_index": 0, "text": "public summary"},
                 {"type": "response.output_text.delta", "output_index": 1, "content_index": 0, "delta": "ans"},
                 {"type": "response.output_text.delta", "output_index": 1, "content_index": 0, "delta": "wer"},
                 {"type": "response.completed", "response": result([reasoning(), message()])})
    with patch("tingmu.providers._open", return_value=stream):
        out = request(config(), "/chat/completions", {"model": "chosen", "messages": [], "stream": True}, on_delta=delta, on_reasoning=callback)
    assert public == [{"kind": "summary", "text": "public "}, {"kind": "summary", "text": "summary"}]
    assert text == ["ans", "wer"] and order == ["summary", "summary", "answer", "answer"]
    assert out["choices"][0]["finish_reason"] == "stop"
    assert "PRIVATE_" not in json.dumps(public)


def test_responses_sse_function_calls_normalized_with_call_id_and_private_items():
    call = function_call()
    stream = sse({"type": "response.output_item.added", "output_index": 0, "item": {**call, "arguments": ""}},
                 {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": "{"},
                 {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": "}"},
                 {"type": "response.function_call_arguments.done", "output_index": 0, "arguments": "{}"},
                 {"type": "response.output_item.done", "output_index": 0, "item": call},
                 {"type": "response.completed", "response": result([call])})
    with patch("tingmu.providers._open", return_value=stream):
        out = request(config(), "/chat/completions", {"model": "chosen", "messages": [], "stream": True})
    choice = out["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"] == [{"id": "call-one", "type": "function", "function": {"name": "read_notebook", "arguments": "{}"}}]
    assert choice["message"]["_responses_output"] == [call]


@pytest.mark.parametrize("events", [
    [{"type": "response.failed", "response": {"error": {"message": "PRIVATE_ERROR"}}}],
    [{"type": "error", "message": "PRIVATE_ERROR"}],
    [{"type": "response.output_text.delta", "delta": "partial"}],
])
def test_responses_failure_and_truncation_are_safe(events):
    with patch("tingmu.providers._open", return_value=sse(*events)):
        with pytest.raises(ProviderError) as error:
            request(config(), "/chat/completions", {"model": "chosen", "messages": [], "stream": True})
    assert "PRIVATE_ERROR" not in str(error.value)


def test_responses_incomplete_refusal_and_cancel_never_become_tool_success():
    for payload, finish in [(result([function_call()], status="incomplete"), "length"),
                            (result([{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]), "content_filter")]:
        with patch("tingmu.providers._open", return_value=io.BytesIO(json.dumps(payload).encode())):
            out = request(config(), "/chat/completions", {"model": "chosen", "messages": []})
        assert out["choices"][0]["finish_reason"] == finish
    events = []
    with patch("tingmu.providers._open", return_value=sse({"type": "response.reasoning_summary_text.delta", "delta": "partial"}, {"type": "response.completed", "response": result([message()])})):
        with pytest.raises(RequestCancelled):
            request(config(), "/chat/completions", {"model": "chosen", "messages": [], "stream": True}, on_reasoning=events.append, cancelled=lambda: bool(events))
    assert events == [{"kind": "summary", "text": "partial"}]


def test_deepseek_public_reasoning_live_separate_and_retained_for_native_tools():
    events, text = [], []
    stream = sse({"choices": [{"delta": {"reasoning_content": "step one "}}]},
                 {"choices": [{"delta": {"reasoning_content": "step two", "encrypted_content": "PRIVATE"}}]},
                 {"choices": [{"delta": {"content": "answer"}, "finish_reason": "stop"}]}, "[DONE]")
    with patch("tingmu.providers._open", return_value=stream):
        out = request(config(provider="deepseek", protocol="openai"), "/chat/completions", {"model": "chosen", "messages": [], "stream": True}, on_reasoning=events.append, on_delta=text.append)
    assert events == [{"kind": "reasoning", "text": "step one "}, {"kind": "reasoning", "text": "step two"}]
    assert text == ["answer"] and out["choices"][0]["message"]["reasoning_content"] == "step one step two"


def test_anthropic_public_thinking_only_and_signed_blocks_retained():
    events = []
    stream = sse({"type": "message_start", "message": {"model": "claude"}},
                 {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "start "}},
                 {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "continue"}},
                 {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "PRIVATE_SIGNATURE"}},
                 {"type": "content_block_start", "index": 1, "content_block": {"type": "redacted_thinking", "data": "PRIVATE_REDACTED", "thinking": "PRIVATE_FAKE_TEXT"}},
                 {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}, {"type": "message_stop"})
    with patch("tingmu.providers._open", return_value=stream):
        out = request(config(protocol="anthropic"), "/chat/completions", {"model": "claude", "messages": [], "stream": True}, on_reasoning=events.append)
    assert events == [{"kind": "reasoning", "text": "start "}, {"kind": "reasoning", "text": "continue"}]
    assert "PRIVATE_" not in json.dumps(events)
    assert out["choices"][0]["message"]["_native_content"][0]["signature"] == "PRIVATE_SIGNATURE"


def test_reasoning_is_bounded_and_known_secret_redacted():
    events = []
    stream = sse({"choices": [{"delta": {"reasoning_content": "test-secret-key " + "x" * 65000}}]},
                 {"choices": [{"delta": {"reasoning_content": "ignored"}, "finish_reason": "stop"}]}, "[DONE]")
    with patch("tingmu.providers._open", return_value=stream):
        request(config(protocol="openai"), "/chat/completions", {"model": "chosen", "messages": [], "stream": True}, on_reasoning=events.append)
    assert sum(len(e["text"]) for e in events) == 60000
    assert "test-secret-key" not in json.dumps(events)


def test_companion_responses_native_tool_loop_preserves_opaque_only_in_wire():
    a = CompanionAgent(profile=config())
    first = result([reasoning("planning summary"), function_call()])
    last = result([reasoning("answer summary"), message("finished")])
    replies = [sse({"type": "response.reasoning_summary_text.delta", "delta": "planning summary"}, {"type": "response.completed", "response": first}),
               sse({"type": "response.reasoning_summary_text.delta", "delta": "answer summary"}, {"type": "response.completed", "response": last})]
    visible, requests = [], []
    def open_request(req, timeout):
        requests.append(json.loads(req.data))
        return replies.pop(0)
    with patch("tingmu.providers._open", side_effect=open_request):
        answer = a.reply("read notes", [], LanguagePair(), tools=[tool()], tool_executor=lambda *args: {"ok": True}, on_reasoning=visible.append)
    assert answer == "finished" and visible == [{"kind": "summary", "text": "planning summary"}, {"kind": "summary", "text": "answer summary"}]
    assert any(item.get("encrypted_content") == "PRIVATE_ENCRYPTED" for item in requests[1]["input"])
    assert "PRIVATE_" not in json.dumps([visible, a.last_response_metadata])
    assert all("planning summary" not in item.get("content", "") for item in requests[1]["input"] if item.get("role") == "user")


def test_json_planner_reasoning_is_streamed_before_validated_actions_and_final():
    a = CompanionAgent(profile=config(protocol="openai"))
    visible, steps, answer_deltas = [], [], []
    def stream(body, *, on_delta=None, cancelled=None, on_reasoning=None):
        if body.get("tools"):
            raise ProviderError("tools unsupported", status=400, category="tools_unsupported")
        if body.get("response_format"):
            on_reasoning({"kind": "reasoning", "text": "planning"})
            steps.append("thinking")
            plan = {"actions": [{"name": "read_notebook", "arguments": {}}], "finished": False} if len(steps) == 1 else {"actions": [], "finished": True}
            assert body["stream"] and on_delta is None
            content = json.dumps(plan)
        else:
            on_reasoning({"kind": "reasoning", "text": "final thinking"})
            on_delta("done")
            content = "done"
        return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
    def execute(*args):
        steps.append("executed")
        return {"ok": True}
    with patch.object(a._transport, "_stream_request", side_effect=stream):
        assert a.reply("read", [], LanguagePair(), tools=[tool()], tool_executor=execute, on_reasoning=visible.append, on_delta=answer_deltas.append) == "done"
    assert steps == ["thinking", "executed", "thinking"]
    assert [v["text"] for v in visible] == ["planning", "planning", "final thinking"]
    assert answer_deltas == ["done"]


def test_learning_agent_forwards_public_callback_without_changing_completion_api():
    a = LearningAgent(profile=config(protocol="openai"))
    seen = []
    payload = {"choices": [{"finish_reason": "stop", "message": {"content": "answer", "reasoning_content": "public"}}]}
    with patch("tingmu.providers._open", return_value=io.BytesIO(json.dumps(payload).encode())):
        assert a._request("/chat/completions", {"model": "chosen", "messages": []}, on_reasoning=seen.append)["choices"][0]["message"]["content"] == "answer"
    assert seen == [{"kind": "reasoning", "text": "public"}]


@pytest.mark.parametrize("ending", ["completed", "cancelled"])
def test_reasoning_callback_never_exposes_split_registered_key_prefix(ending):
    from tingmu.diagnostics import register_secret
    from tingmu.providers import reasoning_callback
    key = "CUSTOM-PRIVATE-CREDENTIAL-12345"
    register_secret(key)
    events, cancelled = [], [False]
    emit = reasoning_callback(events.append, lambda: cancelled[0])
    emit("reasoning", "Visible. " + key[:12])
    assert events == [{"kind": "reasoning", "text": "Visible. "}]
    if ending == "cancelled":
        cancelled[0] = True
        emit.finish()
        assert len(events) == 1
    else:
        emit("reasoning", key[12:] + " Continue.")
        emit.finish()
        combined = "".join(e["text"] for e in events)
        assert combined == "Visible. [REDACTED] Continue."
    assert key[:12] not in json.dumps(events)


def test_unknown_sk_prefix_is_held_until_stable_redaction():
    from tingmu.providers import reasoning_callback
    events = []
    emit = reasoning_callback(events.append)
    for part in ("visible s", "k-abc", "defghijkl", " after"):
        emit("summary", part)
        assert "sk-abc" not in json.dumps(events)
    emit.finish()
    assert "".join(e["text"] for e in events) == "visible [REDACTED] after"


def test_terminal_summary_callback_cancellation_prevents_same_event_answer_callback():
    cancelled, summaries, answers = [False], [], []
    def summary(event):
        summaries.append(event)
        cancelled[0] = True
    stream = sse({"type": "response.completed", "response": result([reasoning(), message()])})
    with patch("tingmu.providers._open", return_value=stream):
        with pytest.raises(RequestCancelled):
            request(config(), "/chat/completions", {"model": "chosen", "messages": [], "stream": True},
                    on_reasoning=summary, on_delta=answers.append, cancelled=lambda: cancelled[0])
    assert summaries == [{"kind": "summary", "text": "public summary"}]
    assert answers == []


def test_nonstream_reasoning_callback_cancellation_does_not_return_success():
    cancelled, summaries = [False], []
    def summary(event):
        summaries.append(event)
        cancelled[0] = True
    with patch("tingmu.providers._open", return_value=io.BytesIO(json.dumps(result([reasoning(), message()])).encode())):
        with pytest.raises(RequestCancelled):
            request(config(), "/chat/completions", {"model": "chosen", "messages": []},
                    on_reasoning=summary, cancelled=lambda: cancelled[0])
    assert summaries == [{"kind": "summary", "text": "public summary"}]
