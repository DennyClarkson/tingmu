import json
from unittest.mock import patch

import pytest

from tingmu.agent import AgentError
from tingmu.companion import CompanionAgent, LEARNING_TOOLS, FEATURE_GUIDE
from tingmu.models import LanguagePair, Cue
from tingmu.providers import RequestCancelled


def agent():
    return CompanionAgent(profile={"provider": "custom", "protocol": "openai", "model": "chosen-by-user", "base_url": "https://example.invalid/v1", "key_source": "none"})


def tool(name="read_notebook", properties=None, required=None):
    return {"type": "function", "function": {"name": name, "description": "Bounded learning tool", "parameters": {"type": "object", "properties": properties or {}, "required": required or [], "additionalProperties": False}}}


def answer(text="done", calls=None, finish=None):
    return {"model": "actual", "usage": {"total_tokens": 5, "secret": "DO NOT EXPOSE"}, "choices": [{"finish_reason": finish or ("tool_calls" if calls else "stop"), "message": {"content": text, "tool_calls": calls or []}}]}


def call(name="read_notebook", args=None, ident="c1"):
    return {"id": ident, "type": "function", "function": {"name": name, "arguments": json.dumps(args or {})}}


def test_tool_round_replays_real_results_and_safe_metadata():
    a = agent()
    bodies = []
    replies = iter([answer("先查看笔记", [call()]), answer("有三条收藏")])
    def complete(path, body):
        bodies.append(json.loads(json.dumps(body)))
        return next(replies)
    with patch.object(a._transport, "_request", side_effect=complete), patch("builtins.print") as output:
        executed = []
        result = a.reply("查看我的笔记", [], LanguagePair(), tools=[tool()], tool_executor=lambda n, v: executed.append((n, v)) or {"ok": True, "count": 3})
    assert result == "先查看笔记\n\n有三条收藏"
    assert executed == [("read_notebook", {})]
    assert bodies[1]["messages"][-1]["role"] == "tool"
    assert json.loads(bodies[1]["messages"][-1]["content"])["count"] == 3
    assert a.last_response_metadata["tool_calls"] == 1
    assert a.last_response_metadata["status"] == "completed"
    assert "DO NOT EXPOSE" not in json.dumps(a.last_response_metadata)
    output.assert_not_called()


def test_unlisted_tool_and_extra_arguments_execute_nothing():
    for calls in ([call("run_shell")], [call(args={"path": "C:/private"})], [call(), call(ident="c1")]):
        a = agent()
        with patch.object(a._transport, "_request", return_value=answer(calls=calls)), patch("builtins.print") as executor:
            with pytest.raises(AgentError):
                a.reply("学习", [], LanguagePair(), tools=[tool()], tool_executor=executor)
            executor.assert_not_called()
    with pytest.raises(AgentError, match="未授权"):
        agent().reply("x", [], LanguagePair(), tools=[tool("run_shell")])


def test_tool_schema_and_executor_failures_are_safe():
    a = agent()
    t = tool("collect_current_word", {"text": {"type": "string", "maxLength": 20}}, ["text"])
    with patch.object(a._transport, "_request", return_value=answer(calls=[call("collect_current_word", {"text": 123})])):
        with pytest.raises(AgentError):
            a.reply("save", [], LanguagePair(), tools=[t], tool_executor=lambda n, v: pytest.fail("must not execute"))
    bodies = []
    def complete(path, body):
        bodies.append(json.loads(json.dumps(body)))
        return answer(calls=[call()]) if len(bodies) == 1 else answer("未能读取")
    with patch.object(a._transport, "_request", side_effect=complete):
        a.reply("read", [], LanguagePair(), tools=[tool()], tool_executor=lambda n, v: (_ for _ in ()).throw(RuntimeError("C:/private SECRET")))
    assert "SECRET" not in json.dumps(bodies[-1])
    assert not json.loads(bodies[-1]["messages"][-1]["content"])["ok"]


def test_bounded_rounds_and_duplicate_ids_never_repeat_mutation():
    a = agent()
    a.max_tool_rounds = 2
    ids = iter(["one", "two", "three"])
    done = []
    with patch.object(a._transport, "_request", side_effect=lambda *x: answer(calls=[call(ident=next(ids))])):
        with pytest.raises(AgentError, match="轮数"):
            a.reply("read", [], LanguagePair(), tools=[tool()], tool_executor=lambda n, v: done.append(n) or {"ok": True})
    assert len(done) == 2
    a = agent()
    done = []
    with patch.object(a._transport, "_request", return_value=answer(calls=[call()])):
        with pytest.raises(AgentError):
            a.reply("read", [], LanguagePair(), tools=[tool()], tool_executor=lambda n, v: done.append(n) or {"ok": True})
    assert len(done) == 1


def test_streaming_cancel_retains_callback_text_without_tools():
    a = agent()
    deltas = []
    def stream(body, *, on_delta, cancelled):
        assert body["stream"]
        on_delta("partial")
        raise RequestCancelled("停止")
    with patch.object(a._transport, "_stream_request", side_effect=stream):
        with pytest.raises(RequestCancelled):
            a.reply("x", [], LanguagePair(), on_delta=deltas.append, cancelled=lambda: False)
    assert deltas == ["partial"]
    assert a.last_response_metadata["status"] == "cancelled"
    with patch.object(a._transport, "_stream_request") as transport:
        with pytest.raises(RequestCancelled):
            a.reply("x", [], LanguagePair(), cancelled=lambda: True)
        transport.assert_not_called()


def test_unclassified_400_and_schema_errors_never_silently_disable_tools():
    for category in (None, "invalid_request", "tool_schema", "reasoning_parameter"):
        a = agent()
        with patch.object(a._transport, "_request", side_effect=AgentError("bad request", status=400, category=category)) as request:
            with pytest.raises(AgentError):
                a.reply("read", [], LanguagePair(), tools=[tool()], tool_executor=lambda n, v: pytest.fail("must not execute"))
            assert request.call_count == 1
        assert a.last_response_metadata["status"] == "failed"


def plan(actions=None, finished=False):
    return answer(json.dumps({"actions": actions or [], "finished": finished}))


def test_json_action_fallback_executes_real_tool_then_streams_final_answer():
    a = agent()
    deltas, sent, executed = [], [], []
    t = tool("collect_current_word", {"text": {"type": "string"}}, ["text"])
    def stream(body, *, on_delta=None, cancelled=None):
        sent.append(json.loads(json.dumps(body)))
        if len(sent) == 1:
            raise AgentError("unsupported", status=400, category="tools_unsupported")
        if len(sent) == 2:
            assert on_delta is None  # internal JSON is never forwarded to the UI
            return plan([{"name": "collect_current_word", "arguments": {"text": "唔該"}}])
        if len(sent) == 3:
            assert on_delta is None
            assert "saved" in json.dumps(body)
            return plan(finished=True)
        assert "tools" not in body and "response_format" not in body
        assert "FINISHED" in body["messages"][0]["content"]
        on_delta("已收藏")
        on_delta("唔該")
        return answer("已收藏唔該")
    with patch.object(a._transport, "_stream_request", side_effect=stream):
        result = a.reply("收藏唔該", [], LanguagePair(), Cue("a", 0, 1, "唔該你。"), on_delta=deltas.append,
                         tools=[t], tool_executor=lambda n, v: executed.append((n, v)) or {"saved": True})
    assert executed == [("collect_current_word", {"text": "唔該"})]
    assert deltas == ["已收藏", "唔該"] and result == "已收藏唔該"
    assert a.last_response_metadata["tool_protocol"] == "json_actions"
    assert a.last_response_metadata["native_tools_unsupported"]
    assert a.last_response_metadata["final_streaming"]
    assert a.last_response_metadata["planning_requests"] == 2
    assert a.last_response_metadata["tool_calls"] == 1


def test_json_action_no_operation_question_still_streams_final():
    a = agent()
    deltas = []
    def stream(body, *, on_delta=None, cancelled=None):
        if body.get("tools"):
            raise AgentError("unsupported", status=400, category="tools_unsupported")
        if body.get("response_format"):
            return plan(finished=True)
        on_delta("解释")
        on_delta("语气")
        return answer("解释语气")
    with patch.object(a._transport, "_stream_request", side_effect=stream):
        assert a.reply("解释语气", [], LanguagePair(), on_delta=deltas.append, tools=[tool()], tool_executor=lambda n, v: pytest.fail("no operation requested")) == "解释语气"
    assert deltas == ["解释", "语气"]
    assert a.last_response_metadata["tool_calls"] == 0


@pytest.mark.parametrize("raw", [
    '{"actions":[],"finished":true,"extra":1}',
    '{"actions":[],"finished":false}',
    '{"actions":[{"name":"run_shell","arguments":{}}],"finished":false}',
    '{"actions":[{"name":"read_notebook","arguments":{"path":"private"}}],"finished":false}',
    '{"actions":[],"actions":[],"finished":true}',
    '{"actions":[],"finished":true',
])
def test_malformed_json_action_plan_never_executes(raw):
    a = agent()
    with patch.object(a._transport, "_request", side_effect=[AgentError("unsupported", status=400, category="tools_unsupported"), answer(raw)]):
        with pytest.raises(AgentError):
            a.reply("read", [], LanguagePair(), tools=[tool()], tool_executor=lambda n, v: pytest.fail("invalid plan must not execute"))


def test_json_action_rejects_nonexistent_source_word_and_duplicate_mutations():
    t = tool("collect_current_word", {"text": {"type": "string"}}, ["text"])
    for actions in ([{"name": "collect_current_word", "arguments": {"text": "不存在"}}],
                    [{"name": "collect_current_word", "arguments": {"text": "唔該"}}] * 2):
        a = agent()
        with patch.object(a._transport, "_request", side_effect=[AgentError("unsupported", status=400, category="tools_unsupported"), plan(actions)]):
            with pytest.raises(AgentError):
                a.reply("收藏唔該", [], LanguagePair(), Cue("a", 0, 1, "唔該你。"), tools=[t],
                        tool_executor=lambda n, v: pytest.fail("invalid plan must not mutate"))


def test_json_action_cancellation_during_planning_executes_nothing():
    a = agent()
    deltas = []
    with patch.object(a._transport, "_stream_request", side_effect=[AgentError("unsupported", status=400, category="tools_unsupported"), RequestCancelled("stopped")]):
        with pytest.raises(RequestCancelled):
            a.reply("read", [], LanguagePair(), tools=[tool()], on_delta=deltas.append, cancelled=lambda: False,
                    tool_executor=lambda n, v: pytest.fail("cancelled plan must not execute"))
    assert deltas == [] and a.last_response_metadata["status"] == "cancelled"


def test_tool_scope_contract():
    assert LEARNING_TOOLS >= {"read_notebook", "collect_current_word", "collect_current_sentence", "organize_notebook", "append_learning_note", "practice_words"}
    assert {"process_videos", "control_playback", "save_notebook", "set_teaching_preferences"} <= LEARNING_TOOLS
    assert not {"run_shell", "read_file", "save_api_key"} & LEARNING_TOOLS
    assert "never automatically reprocesses" in FEATURE_GUIDE


def test_json_action_failed_result_reaches_final_without_false_success_or_retry():
    a = agent()
    sent, executed = [], []
    replies = iter([AgentError("unsupported", status=400, category="tools_unsupported"),
                    plan([{"name": "read_notebook", "arguments": {}}]), plan(finished=True), answer("读取失败，请稍后重试。")])
    def complete(path, body):
        sent.append(json.loads(json.dumps(body)))
        result = next(replies)
        if isinstance(result, Exception):
            raise result
        return result
    def executor(name, args):
        executed.append(name)
        raise RuntimeError("SECRET internal path")
    with patch.object(a._transport, "_request", side_effect=complete):
        result = a.reply("读取笔记", [], LanguagePair(), tools=[tool()], tool_executor=executor)
    assert result == "读取失败，请稍后重试。"
    assert executed == ["read_notebook"]
    assert "SECRET" not in json.dumps(sent)
    assert '"ok": false' in sent[-1]["messages"][-1]["content"]
    assert a.last_response_metadata["tool_calls"] == 1


def test_json_action_repeated_mutation_across_rounds_is_blocked():
    a = agent()
    t = tool("append_learning_note", {"text": {"type": "string"}}, ["text"])
    operation = [{"name": "append_learning_note", "arguments": {"text": "Keep this note"}}]
    executed = []
    with patch.object(a._transport, "_request", side_effect=[AgentError("unsupported", status=400, category="tools_unsupported"), plan(operation), plan(operation)]):
        with pytest.raises(AgentError, match="重复"):
            a.reply("记下这条笔记", [], LanguagePair(), tools=[t], tool_executor=lambda n, v: executed.append(n) or {"ok": True})
    assert executed == ["append_learning_note"]


def test_v5_native_private_thinking_survives_only_tool_round_and_reports_events():
    from tingmu.app_actions import action_tools
    a = agent()
    first = answer("", [call("set_teaching_preferences", {"preferences": "more details"})])
    private = [{"type": "thinking", "thinking": "PRIVATE_THOUGHT", "signature": "PRIVATE_SIGNATURE"}]
    first["choices"][0]["message"].update(_native_content=private, reasoning_content="PRIVATE_REASONING")
    bodies, events = [], []
    replies = iter([first, answer("Saved for future materials")])
    def request(path, body):
        bodies.append(json.loads(json.dumps(body)))
        return next(replies)
    with patch.object(a._transport, "_request", side_effect=request):
        result = a.reply("Use more detail", [], LanguagePair(), tools=action_tools(),
                         tool_executor=lambda n, v: {"saved": True, "existing_materials_regenerated": False}, on_tool_event=events.append)
    assistant = bodies[1]["messages"][-2]
    assert assistant["_native_content"] == private and assistant["reasoning_content"] == "PRIVATE_REASONING"
    assert [e["status"] for e in events] == ["running", "success"]
    assert "PRIVATE_" not in json.dumps([result, events, a.last_response_metadata])


def test_v5_executor_cancel_does_not_become_tool_failure_or_continue():
    a = agent()
    with patch.object(a._transport, "_request", return_value=answer(calls=[call()])) as request:
        with pytest.raises(RequestCancelled):
            a.reply("read", [], LanguagePair(), tools=[tool()],
                    tool_executor=lambda n, v: (_ for _ in ()).throw(RequestCancelled("stop")))
    assert request.call_count == 1 and a.last_response_metadata["status"] == "cancelled"


def test_v5_json_actions_arrays_numbers_multistep_and_final_sse():
    from tingmu.app_actions import action_tools
    a = agent()
    sent, executed, deltas = [], [], []
    plans = iter([
        plan([{"name": "manage_queue", "arguments": {"action": "status"}}]),
        plan([{"name": "manage_queue", "arguments": {"action": "retry", "job_ids": ["failed-job"]}},
              {"name": "control_playback", "arguments": {"action": "seek", "value": 12.5}}]),
        plan(finished=True),
    ])
    def stream(body, *, on_delta=None, cancelled=None):
        sent.append(json.loads(json.dumps(body)))
        if body.get("tools"):
            raise AgentError("unsupported", status=400, category="tools_unsupported")
        if body.get("response_format"):
            assert on_delta is None
            return next(plans)
        on_delta("已重新排队，尚未完成。")
        return answer("已重新排队，尚未完成。")
    def execute(name, args):
        executed.append((name, args))
        return {"jobs": [{"id": "failed-job", "status": "failed" if len(executed) == 1 else "queued"}], "processing_completed": False}
    with patch.object(a._transport, "_stream_request", side_effect=stream):
        result = a.reply("重试失败任务并跳到12.5秒", [], LanguagePair(), tools=action_tools(), tool_executor=execute, on_delta=deltas.append)
    assert [n for n, _ in executed] == ["manage_queue", "manage_queue", "control_playback"]
    assert executed[-1][1]["value"] == 12.5
    assert deltas == [result] and a.last_response_metadata["tool_calls"] == 3


def test_v5_reasoning_override_keeps_profile_default_when_requested():
    profile = {"provider": "custom", "protocol": "openai", "model": "chosen", "base_url": "https://example.invalid/v1", "key_source": "none", "reasoning_effort": "low"}
    assert CompanionAgent(profile=profile).reasoning_effort == "low"
    a = CompanionAgent(profile=profile, reasoning_effort="high")
    with patch.object(a._transport, "_request", return_value=answer()) as request:
        a.reply("question", [], LanguagePair())
    assert request.call_args.args[1]["reasoning_effort"] == "high"
    assert a.last_response_metadata["reasoning_effort"] == "high"


@pytest.mark.parametrize("raises", [False, True])
def test_failed_mutation_can_retry_with_new_call_id_and_success_remains_deduplicated(raises):
    from tingmu.app_actions import ActionError, action_tools
    a = agent()
    attempts = []
    replies = [answer("", [call("set_appearance", {"theme": "paper"}, ident="first")]),
               answer("", [call("set_appearance", {"theme": "paper"}, ident="retry")]), answer("saved")]
    def execute(name, args):
        attempts.append((name, args))
        if len(attempts) == 1:
            if raises:
                raise ActionError("temporary failure")
            return {"ok": False, "error": "temporary failure"}
        return {"saved": True}
    with patch.object(a._transport, "_request", side_effect=replies):
        assert a.reply("change theme", [], LanguagePair(), tools=action_tools(), tool_executor=execute) == "saved"
    assert len(attempts) == 2
    a = agent()
    attempts.clear()
    duplicate = [answer("", [call("set_appearance", {"theme": "paper"}, ident=str(i))]) for i in range(2)]
    with patch.object(a._transport, "_request", side_effect=duplicate):
        with pytest.raises(AgentError, match="重复"):
            a.reply("change theme", [], LanguagePair(), tools=action_tools(), tool_executor=lambda n, v: attempts.append(n) or {"saved": True})
    assert len(attempts) == 1
