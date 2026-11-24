"""Public transport composition, isolated request accounting, and headless use."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import io
import json
import subprocess
import sys
from unittest.mock import Mock, patch

import pytest

from tingmu.llm_client import LlmClient
from tingmu.models import Cue, LanguagePair, Token
from tingmu.providers import ProviderError, RequestCancelled


def profile(**changes):
    return {"provider": "openai", "protocol": "openai", "base_url": "https://example.invalid/v1",
            "model": "requested-model", "key_source": "environment", **changes}


def response(content="answer", *, model="returned-model", tokens=7, finish="stop"):
    return {"model": model, "usage": {"total_tokens": tokens},
            "choices": [{"finish_reason": finish, "message": {"content": content}}]}


def test_profile_secret_stays_lazy_through_construction_config_and_fork():
    resolve = Mock(return_value="synthetic-client-key")

    def secret():
        return resolve()

    client = LlmClient(profile=profile(_secret=secret))
    cloned = client.fork()
    assert client.transport_config()["_secret"] is secret
    assert cloned.transport_config()["_secret"] is secret
    resolve.assert_not_called()
    with patch("tingmu.providers._open", return_value=io.BytesIO(json.dumps(response()).encode())) as opened:
        assert cloned.request("/chat/completions", {"model": cloned.model, "messages": []})["model"] == "returned-model"
    resolve.assert_called_once_with()
    assert opened.call_count == 1
    assert "synthetic-client-key" not in repr(client.last_response_metadata)


def test_fork_owns_nested_profile_and_concurrent_accounting():
    configuration = profile(extra={"labels": ["source"]})
    parent = LlmClient(profile=configuration)
    parent.timeout = 17
    parent.max_completion_tokens = 321
    parent.reasoning_effort = "high"
    parent.complete("system", {}, request=lambda *args: response("parent"))
    first, second = parent.fork(), parent.fork()
    assert first.response_metadata == second.response_metadata == []
    assert first.last_response_metadata == second.last_response_metadata == {}
    assert (first.timeout, first.max_completion_tokens, first.reasoning_effort) == (17, 321, "high")
    configuration["extra"]["labels"].append("external mutation")
    first._profile["extra"]["labels"].append("first only")
    assert parent._profile["extra"]["labels"] == second._profile["extra"]["labels"] == ["source"]
    wire_response = response()

    def run(client, content):
        return client.complete("system", {"content": content}, request=lambda *args: wire_response)

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(lambda pair: run(*pair), [(first, "one"), (second, "two")])) == ["answer", "answer"]
    wire_response["usage"]["total_tokens"] = 999
    first.response_metadata[0]["usage"]["total_tokens"] = 123
    assert second.response_metadata[0]["usage"]["total_tokens"] == 7
    assert parent.response_metadata[0]["usage"]["total_tokens"] == 7
    assert len(first.response_metadata) == len(second.response_metadata) == len(parent.response_metadata) == 1


def test_complete_format_fallback_preserves_exact_schema_input_and_identity():
    client = LlmClient(profile=profile(max_tokens=888))
    schema = {"type": "object", "properties": {"原文": {"type": "string"}}, "required": ["原文"],
              "additionalProperties": False}
    payload = {"原文": "  聽清楚。\u00a0"}
    original = deepcopy((schema, payload))
    sent = []

    def request(path, body):
        assert path == "/chat/completions"
        sent.append(deepcopy(body))
        if len(sent) < 3:
            raise ProviderError("synthetic unsupported format", status=422)
        return response('{"原文":"聽清楚。"}')

    assert client.complete("trusted instructions", payload, schema, request=request) == '{"原文":"聽清楚。"}'
    assert [body.get("response_format", {}).get("type") for body in sent] == ["json_schema", "json_object", None]
    assert (schema, payload) == original
    for body in sent:
        assert body["model"] == "requested-model"
        assert json.loads(body["messages"][1]["content"]) == payload
        instruction = body["messages"][0]["content"]
        assert instruction.startswith("trusted instructions")
        assert json.loads(instruction.split("Required JSON schema: ", 1)[1]) == schema
    assert client.last_response_metadata["requested_model"] == "requested-model"
    assert client.last_response_metadata["returned_model"] == "returned-model"
    assert client.last_response_metadata["response_format"] == "prompt"
    assert client.last_response_metadata["usage"] == {"total_tokens": 7}
    assert "annotation_version" not in client.last_response_metadata


@pytest.mark.parametrize("status,category", [(400, "tool_schema"), (422, "reasoning_parameter"),
                                             (401, "authentication"), (429, "rate_limit"), (503, "server")])
def test_complete_never_downgrades_unrelated_errors(status, category):
    client = LlmClient(profile=profile())
    error = ProviderError("synthetic provider failure", status=status, category=category)
    request = Mock(side_effect=error)
    with pytest.raises(ProviderError) as caught:
        client.complete("system", {}, {"type": "object"}, request=request)
    assert caught.value is error
    assert request.call_count == 1


@pytest.mark.parametrize("finish,content", [("length", "partial"), ("content_filter", ""), ("stop", "  ")])
def test_response_accounting_retains_actual_model_when_output_is_rejected(finish, content):
    client = LlmClient(profile=profile())
    with pytest.raises(ProviderError):
        client.complete("system", {}, request=lambda *args: response(content, finish=finish, tokens=19))
    assert len(client.response_metadata) == 1
    metadata = client.response_metadata[0]
    assert metadata["requested_model"] == "requested-model"
    assert metadata["returned_model"] == "returned-model"
    assert metadata["usage"] == {"total_tokens": 19}
    assert metadata["finish_reason"] == finish


def test_public_stream_forwards_callbacks_and_cancellation_without_secret_lookup():
    secret = Mock(return_value="unused-key")
    client = LlmClient(profile=profile(_secret=secret))
    delta, reasoning = [], []
    with patch("tingmu.providers._open") as opened:
        with pytest.raises(RequestCancelled):
            client.stream_request({"model": client.model, "messages": [], "stream": True},
                                  on_delta=delta.append, on_reasoning=reasoning.append, cancelled=lambda: True)
    assert delta == reasoning == []
    opened.assert_not_called()
    secret.assert_not_called()


def test_companion_uses_public_client_without_constructing_learning_agent():
    from tingmu.agent import LearningAgent
    from tingmu.companion import CompanionAgent

    with patch.object(LearningAgent, "__init__", side_effect=AssertionError("teacher must not be a transport")):
        companion = CompanionAgent(profile=profile())
    assert isinstance(companion.client, LlmClient)
    assert companion._transport.client is companion.client
    with patch.object(companion.client, "request", return_value=response("Public answer")) as request:
        assert companion.reply("Explain this", [], LanguagePair("zh-CN", "en")) == "Public answer"
    assert request.call_count == 1
    assert companion.last_response_metadata["returned_model"] == "returned-model"


def test_notebook_organizer_accepts_public_complete_without_private_teacher_methods(tmp_path):
    from tingmu.notebooks import NotebookManager
    from tingmu.storage import Store

    store = Store(tmp_path / "data")
    store.save_word("synthetic.mkv", Cue("one", 1, 2, "hello"), Token("hello", "", "greeting"),
                    LanguagePair("zh-CN", "en"))
    calls = []

    class PublicClient:
        provider = "custom"

        def complete(self, prompt, payload, schema):
            calls.append((prompt, payload, schema))
            entries = payload["entries"]
            return json.dumps({"topics": [{"title": "Greetings", "entry_ids": [e["id"] for e in entries],
                "learning_note": "Use greetings in their context.", "entry_notes": [
                    {"entry_id": e["id"], "comparison": "Compare polite greetings.", "usage": "Meeting someone.",
                     "grammar": "", "pitfalls": "", "example": "Hello, friend.", "example_translation": "你好，朋友。",
                     "goal": "Greet a friend.", "remark": ""} for e in entries]}]})

    book = NotebookManager(store).organize("en", PublicClient())
    assert len(calls) == 1
    assert book["topics"][0]["title"] == "Greetings"
    assert "tingmu://entry/" in book["markdown"]


def test_reviewer_is_standalone_and_retains_raw_identity():
    from tingmu.agent import LearningAgent
    from tingmu.review import TranscriptReviewer

    assert not issubclass(TranscriptReviewer, LearningAgent)
    source = Cue("source", 1.25, 3.75, "Hello, friend.", "Original ASR")
    with patch.object(LearningAgent, "__init__", side_effect=AssertionError("reviewer must not construct a teacher")):
        reviewer = TranscriptReviewer(profile=profile())
    answer = json.dumps({"cues": [{"id": "source", "text": source.text, "uncertain": False,
                                   "reason": "", "segmented_texts": [source.text]}]})
    with patch.object(reviewer, "_request", return_value=response(answer)):
        result = reviewer.review([source], LanguagePair("zh-CN", "en"))
    assert len(result) == 1
    assert (result[0].id, result[0].start, result[0].end, result[0].original_text) == (
        source.id, source.start, source.end, source.original_text)
    assert reviewer.review_metadata["source"]["state"] == "reviewed_unchanged"


def test_transport_review_and_unicode_helpers_import_without_qt():
    program = r'''
import importlib.abc
import sys
class RejectQt(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PySide6" or fullname.startswith("PySide6."):
            raise AssertionError("pure preparation imported Qt: " + fullname)
sys.meta_path.insert(0, RejectQt())
from tingmu.llm_client import LlmClient
from tingmu.review import TranscriptReviewer
from tingmu.timeline import display_width, split_display_text
assert display_width("A漢ｅe\u0301") == 6
text = "Hello,\u00a0friend.  漢字，e\u0301！ " * 9
parts = split_display_text(text)
assert "".join(parts) == text
assert all(display_width(part) <= 42 for part in parts)
assert not any(name.startswith("PySide6") for name in sys.modules)
'''
    result = subprocess.run([sys.executable, "-c", program], text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
