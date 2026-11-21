import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import urllib.error
import urllib.request

import pytest

from tingmu.agent import LearningAgent, AgentError, ANNOTATION_VERSION, SYSTEM_PROMPT
from tingmu.credentials import protect, unprotect, CredentialError
from tingmu.providers import (ModelRegistry, NoRedirect, ProviderError, RequestCancelled,
                              request, validate_base_url, _wire_body)
from tingmu.review import TranscriptReviewer, REVIEW_PROMPT, REVIEW_VERSION


def profile(**changes):
    return {"provider": "custom", "protocol": "openai", "base_url": "https://example.invalid/v1",
            "model": "user-chosen-model", "key_source": "environment", "_key": "secret-test-key", **changes}


def response(**changes):
    return {"model": "actual-model", "usage": {"total_tokens": 7},
            "choices": [{"finish_reason": "stop", "message": {"content": '{"value": 1}'}}], **changes}


def sse(*events):
    return io.BytesIO("".join("data: " + (e if isinstance(e, str) else json.dumps(e)) + "\n\n" for e in events).encode())


@pytest.mark.parametrize("url", ["http://example.com/v1", "https://u:p@example.com/v1", "https://example.com/?key=x", "https://example.com/#x", "https://example.com:bad", "https://example.com:0", "https://example.com/\n", "https://example.com\\@evil.com", "https://{workspace}.example.com", "file:///tmp/x"])
def test_invalid_urls_rejected(url):
    with pytest.raises(ProviderError):
        validate_base_url(url)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "[::1]"])
def test_http_requires_loopback_and_no_key(host):
    value = f"http://{host}:11434/v1/"
    assert validate_base_url(value, key_source="none") == value.rstrip("/")
    with pytest.raises(ProviderError):
        validate_base_url(value)


def test_redirect_never_forwards_credentials():
    handler = NoRedirect()
    req = urllib.request.Request("https://first.example/v1", headers={"Authorization": "Bearer SECRET"})
    for target in ("https://evil.example", "https://first.example/new"):
        with pytest.raises(ProviderError, match="重定向") as exc:
            handler.redirect_request(req, None, 302, "", {}, target)
        assert "SECRET" not in str(exc.value)


def test_real_dpapi_roundtrip_and_no_plaintext():
    if os.name != "nt":
        with pytest.raises(CredentialError):
            protect("test-secret")
        return
    encrypted = protect("test-secret-测试")
    assert "test-secret" not in encrypted
    assert unprotect(encrypted) == "test-secret-测试"
    with pytest.raises(CredentialError):
        unprotect("corrupt")


def test_registry_never_persists_or_exposes_plaintext(tmp_path):
    registry = ModelRegistry(tmp_path)
    with patch("tingmu.providers.protect", return_value="cipher"), patch("tingmu.providers.unprotect", return_value="SENSITIVE"):
        public = registry.save_profile({"provider": "openai", "model": "mine", "label": "Personal", "apiKey": "DO-NOT-PERSIST"}, api_key="SENSITIVE")
        assert public["has_key"]
        serialized = json.dumps(public) + (tmp_path / "model-profiles.json").read_text()
        assert "SENSITIVE" not in serialized and "DO-NOT-PERSIST" not in serialized
        assert "_protected_key" not in public
        resolved = registry.resolve(public["id"])
        assert "SENSITIVE" not in repr(resolved)
        assert resolved["_secret"]() == "SENSITIVE"
        updated = registry.save_profile({"id": public["id"], "label": "Renamed"}, api_key="")
        assert updated["has_key"]
        with pytest.raises(ProviderError, match="重新输入"):
            registry.save_profile({"id": public["id"], "base_url": "https://another.example/v1"})
    with patch("tingmu.providers.unprotect", side_effect=CredentialError("请重新输入")):
        assert not registry.public_profiles()[0]["has_key"]
        with pytest.raises(ProviderError):
            registry.resolve(public["id"])["_secret"]()
    registry.delete_profile(public["id"])
    assert registry.public_profiles() == []


def test_environment_compatibility_and_threadsafe_persistence(tmp_path):
    registry = ModelRegistry(tmp_path)
    with patch("tingmu.agent._environment", return_value="env-secret") as env:
        p = registry.save_profile({"provider": "deepseek", "model": "my-model"})
        assert p["has_key"] and p["key_source"] == "environment"
        assert "DEEPSEEK" in env.call_args.args[0]
        assert "env-secret" not in (tmp_path / "model-profiles.json").read_text()
    def save(i):
        return ModelRegistry(tmp_path).save_profile({"id": f"id-{i}", "provider": "ollama", "model": f"local-{i}"})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(save, range(25)))
    assert len(registry.public_profiles()) == 26
    assert not list(tmp_path.glob("*.tmp"))


def test_discovery_without_chosen_model_and_anthropic_pagination(tmp_path):
    registry = ModelRegistry(tmp_path)
    p = registry.save_profile({"provider": "anthropic", "model": ""})
    pages = [{"data": [{"id": "a"}], "has_more": True, "last_id": "a"},
             {"data": [{"id": "b"}, {"id": "a"}], "has_more": False}]
    with patch("tingmu.providers.request", side_effect=pages) as api:
        assert registry.discover_models(p["id"]) == ["a", "b"]
        assert api.call_args.args[1] == "/models?after_id=a"
    assert registry.public_profiles()[0]["models"] == ["a", "b"]
    with pytest.raises(ProviderError, match="模型 ID"):
        registry.resolve(p["id"])
    assert {p["provider"] for p in registry.presets()} == {"openai", "deepseek", "qwen", "gemini", "anthropic",
                                                          "ollama", "lmstudio", "local_compatible", "custom"}
    assert all(not p["model"] for p in registry.presets())


def test_anthropic_request_and_response_conversion():
    body = {"model": "chosen", "max_completion_tokens": 1000, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": "instructions"}, {"role": "user", "content": "text"}]}
    result = {"model": "chosen", "content": [{"type": "text", "text": "answer"}], "stop_reason": "end_turn", "usage": {"input_tokens": 5}}
    with patch("tingmu.providers._open", return_value=io.BytesIO(json.dumps(result).encode())) as opener:
        out = request(profile(provider="anthropic", protocol="anthropic"), "/chat/completions", body)
    req = opener.call_args.args[0]
    assert req.full_url.endswith("/messages")
    assert req.get_header("X-api-key") == "secret-test-key"
    assert req.get_header("Authorization") is None
    wire = json.loads(req.data)
    assert wire["system"] == "instructions" and wire["max_tokens"] == 1000
    assert "response_format" not in wire and "store" not in wire
    assert out["choices"][0]["message"]["content"] == "answer"
    assert out["choices"][0]["finish_reason"] == "stop"


def test_local_transport_does_not_send_key():
    with patch("tingmu.providers._open", return_value=io.BytesIO(b'{"data": []}')) as opener:
        request(profile(base_url="http://127.0.0.1:11434/v1", key_source="none"), "/models")
    assert opener.call_args.args[0].get_header("Authorization") is None


def test_http_errors_and_invalid_payloads_are_safe():
    error = urllib.error.HTTPError("https://secret.example", 401, "secret-key", {}, io.BytesIO(b"secret"))
    with patch("tingmu.providers._open", side_effect=error):
        with pytest.raises(ProviderError) as exc:
            request(profile(), "/models")
        assert exc.value.status == 401
        assert "密钥" in str(exc.value)
        assert "secret" not in str(exc.value)
    with patch("tingmu.providers._open", return_value=io.BytesIO(b'{"error":{"message":"secret"}}')):
        with pytest.raises(ProviderError) as exc:
            request(profile(), "/chat/completions", {"model": "x", "messages": []})
        assert "secret" not in str(exc.value)


def test_queue_rate_limit_reports_server_wait_without_resubmitting():
    from tingmu.batch import _failure_details
    error = urllib.error.HTTPError("https://example.invalid", 429, "secret", {"Retry-After": "45"},
                                   io.BytesIO(b"secret"))
    with patch("tingmu.providers._open", side_effect=error) as opened:
        with pytest.raises(ProviderError) as exc:
            request(profile(), "/models")
    opened.assert_called_once()
    message = _failure_details(exc.value, None)["error_message"]
    assert "45 秒" in message and "同时请求上限" in message and "secret" not in message


def test_openai_stream_deltas_tools_usage_and_completion():
    stream = sse({"model": "returned", "choices": [{"index": 0, "delta": {"content": "你"}}]},
                 {"choices": [{"delta": {"content": "好"}, "finish_reason": "stop"}]},
                 {"choices": [], "usage": {"total_tokens": 9, "body": "secret"}}, "[DONE]")
    deltas = []
    with patch("tingmu.providers._open", return_value=stream):
        out = request(profile(), "/chat/completions", {"model": "x", "messages": [], "stream": True}, on_delta=deltas.append)
    assert deltas == ["你", "好"]
    assert out["choices"][0]["message"]["content"] == "你好"
    assert out["usage"] == {"total_tokens": 9}


def test_anthropic_stream_tool_and_text_deltas():
    stream = sse({"type": "message_start", "message": {"model": "native", "usage": {"input_tokens": 2}}},
                 {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "call-1", "name": "read_notebook"}},
                 {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{}"}},
                 {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 3}},
                 {"type": "message_stop"})
    with patch("tingmu.providers._open", return_value=stream):
        result = request(profile(protocol="anthropic"), "/chat/completions", {"model": "x", "messages": [], "stream": True})
    choice = result["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"][0]["function"]["arguments"] == "{}"
    assert result["usage"] == {"input_tokens": 2, "output_tokens": 3}


def test_cancel_after_delta_and_truncated_or_error_stream():
    deltas = []
    stream = sse({"choices": [{"delta": {"content": "partial"}}]}, "[DONE]")
    with patch("tingmu.providers._open", return_value=stream):
        with pytest.raises(RequestCancelled):
            request(profile(), "/chat/completions", {"model": "x", "messages": [], "stream": True}, on_delta=deltas.append, cancelled=lambda: bool(deltas))
    assert deltas == ["partial"]
    for stream in (sse({"choices": [{"delta": {"content": "partial"}}]}), sse({"error": {"message": "SECRET"}}), sse("bad-json")):
        with patch("tingmu.providers._open", return_value=stream):
            with pytest.raises(ProviderError) as exc:
                request(profile(), "/chat/completions", {"model": "x", "messages": [], "stream": True})
            assert "SECRET" not in str(exc.value)


def test_schema_fallback_keeps_validation_contract_and_model():
    agent = LearningAgent(profile=profile(provider="openai", max_tokens=2000))
    sent = []
    def complete(path, body):
        sent.append(json.loads(json.dumps(body)))
        if len(sent) < 3:
            raise AgentError("unsupported format", status=400)
        return response()
    with patch.object(agent, "_request", side_effect=complete):
        assert agent._complete("instructions", {}, {"type": "object"}) == '{"value": 1}'
    assert [b.get("response_format", {}).get("type") for b in sent] == ["json_schema", "json_object", None]
    assert all("Required JSON schema" in b["messages"][0]["content"] for b in sent)
    assert all(b["model"] == "user-chosen-model" for b in sent)
    assert agent.last_response_metadata["annotation_version"] == ANNOTATION_VERSION
    assert TranscriptReviewer(profile=profile()).model == "user-chosen-model"
    assert "v4" in REVIEW_VERSION and "v4" in ANNOTATION_VERSION
    assert "particles" in REVIEW_PROMPT and "Traditional" in SYSTEM_PROMPT


def test_native_tool_results_replayed_as_user_blocks():
    body = {"model": "x", "messages": [{"role": "system", "content": "sys"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "a", "function": {"name": "read_notebook", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "a", "content": '{"ok": true}'}]}
    wire = _wire_body(profile(protocol="anthropic"), body)
    assert wire["messages"][0]["content"][0]["type"] == "tool_use"
    assert wire["messages"][1]["content"][0]["type"] == "tool_result"
    assert wire["messages"][1]["role"] == "user"


def test_qwen_discovery_uses_documented_same_origin_native_api(tmp_path):
    registry = ModelRegistry(tmp_path)
    p = registry.save_profile({"provider": "qwen", "model": "", "base_url": "https://workspace.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"})
    pages = [{"success": True, "output": {"total": 2, "models": [{"model": "first"}]}},
             {"success": True, "output": {"total": 2, "models": [{"model": "second"}]}}]
    with patch("tingmu.providers.request", side_effect=pages) as api:
        assert registry.discover_models(p["id"]) == ["first", "second"]
        assert api.call_args.args[0]["base_url"] == "https://workspace.cn-beijing.maas.aliyuncs.com/api/v1"
        assert "page_no=2" in api.call_args.args[1]


def test_transport_installs_redirect_guard_on_real_loopback_server():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/forwarded")
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(ProviderError, match="重定向"):
            request(profile(base_url=f"http://127.0.0.1:{server.server_port}", key_source="none"), "/models")
        assert received == ["/models"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("message,category", [
    ("This model does not support tools or functions.", "tools_unsupported"),
    ("Tools are not supported for this connection.", "tools_unsupported"),
    ("Invalid tools schema: required must include every property.", "tool_schema"),
    ("Tools schema contains an unsupported keyword.", "tool_schema"),
    ("Unknown model or invalid request.", "invalid_request"),
    ("reasoning_effort is not supported", "reasoning_parameter"),
])
def test_error_classification_does_not_expose_provider_body(message, category):
    body = json.dumps({"error": {"message": message + " SECRET_DO_NOT_SHOW"}}).encode()
    error = urllib.error.HTTPError("https://example.invalid", 400, "bad", {}, io.BytesIO(body))
    with patch("tingmu.providers._open", side_effect=error):
        with pytest.raises(ProviderError) as exc:
            request(profile(), "/chat/completions", {"model": "x", "messages": []})
    assert exc.value.category == category
    assert "SECRET" not in str(exc.value)
    assert "SECRET" not in repr(exc.value.__dict__)
