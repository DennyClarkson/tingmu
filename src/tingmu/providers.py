"""Validated model profiles and bounded text-only provider transports.

No profile exported to the UI contains a key. Resolve returns a private lazy resolver,
so credentials are decrypted only immediately before an HTTP request.
"""
from __future__ import annotations

import copy
import ipaddress
import http.client
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import urlsplit, quote

from .credentials import CredentialError, protect, unprotect


class ProviderError(RuntimeError):
    """Safe provider error: never includes a response body, headers or URL."""

    def __init__(self, message, *, status=None, category=None):
        super().__init__(message)
        self.status = status
        self.category = category


class RequestCancelled(ProviderError):
    pass


PRESETS = [
    {"provider": "openai", "label": "OpenAI", "protocol": "openai", "base_url": "https://api.openai.com/v1", "env_var": "OPENAI_API_KEY", "model": ""},
    {"provider": "deepseek", "label": "DeepSeek", "protocol": "openai", "base_url": "https://api.deepseek.com", "env_var": "DEEPSEEK_API_KEY", "model": ""},
    {"provider": "qwen", "label": "通义千问（北京）", "protocol": "openai", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "env_var": "DASHSCOPE_API_KEY", "model": ""},
    {"provider": "gemini", "label": "Gemini（OpenAI 兼容）", "protocol": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "env_var": "GEMINI_API_KEY", "model": ""},
    {"provider": "anthropic", "label": "Anthropic", "protocol": "anthropic", "base_url": "https://api.anthropic.com/v1", "env_var": "ANTHROPIC_API_KEY", "model": ""},
    {"provider": "ollama", "label": "Ollama（本机）", "protocol": "openai", "base_url": "http://127.0.0.1:11434/v1", "env_var": "", "key_source": "none", "model": ""},
    {"provider": "custom", "label": "自定义 OpenAI 兼容", "protocol": "openai", "base_url": "", "env_var": "", "model": ""},
]
_LOCKS = {}
_LOCKS_LOCK = threading.Lock()


def validate_base_url(value: str, *, key_source="environment") -> str:
    if not isinstance(value, str) or len(value) > 2000 or any(c.isspace() or ord(c) < 32 for c in value):
        raise ProviderError("API 地址无效。")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (not parsed.hostname or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or "\\" in value or "%" in parsed.netloc
                or any(c in parsed.netloc for c in "{}")):
            raise ValueError()
        local = parsed.hostname.lower() == "localhost"
        try:
            local = local or ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            pass
        if parsed.scheme != "https" and not (parsed.scheme == "http" and local and key_source == "none"):
            raise ValueError()
        if port == 0:
            raise ValueError()
    except (ValueError, TypeError):
        raise ProviderError("API 地址须为 HTTPS；仅无密钥的本机服务允许 HTTP。") from None
    return value.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Refuse even same-origin redirects: no key can escape to another endpoint.
        raise ProviderError("API 重定向已拒绝；请直接填写服务的最终地址。", status=code)


def _open(request, timeout):
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)


def _check_cancel(cancelled):
    if cancelled and cancelled():
        raise RequestCancelled("回答已停止。")


def _safe_usage(value):
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if k in {
        "prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens",
        "cache_creation_input_tokens", "cache_read_input_tokens",
    } and type(v) is int and v >= 0}


def safe_metadata(result):
    model = result.get("model")
    if isinstance(model, str):
        from .diagnostics import redact_text
        model = redact_text(model)[:200]
    return {"returned_model": model if isinstance(model, str) else None,
            "usage": _safe_usage(result.get("usage"))}


def reasoning_callback(callback, cancelled=None, *, limit=60000):
    """Emit stable, bounded public deltas; retain potential credential tails privately."""
    remaining, raw, published = limit, {}, {}
    def publish(kind, *, final=False):
        nonlocal remaining
        from .diagnostics import redact_stream_text
        value = raw.get(kind, "")
        # Unknown sk-style credentials also need a stable prefix: the general
        # redactor recognizes them only after eight token characters arrive.
        pending = re.search(r"\b(?:s|sk|sk-[A-Za-z0-9_-]*)$", value)
        if pending and not final:
            value = value[:pending.start()]
        elif pending and final and value[pending.start():].startswith("sk-"):
            value = value[:pending.start()] + "[REDACTED]"
        safe = redact_stream_text(value, final=final)
        previous = published.get(kind, "")
        if not safe.startswith(previous):
            return  # A redaction changed earlier text; never append an unstable suffix.
        delta = safe[len(previous):][:remaining]
        if delta:
            published[kind] = previous + delta
            remaining -= len(delta)
            callback({"kind": kind, "text": delta})
            _check_cancel(cancelled)
    def emit(kind, value):
        _check_cancel(cancelled)
        if callback is None or kind not in ("reasoning", "summary") or not isinstance(value, str) or not value or not remaining:
            return
        raw[kind] = raw.get(kind, "") + value.replace("\x00", "")
        publish(kind)
    def finish():
        if callback is None or not remaining or (cancelled and cancelled()):
            return
        for kind in raw:
            publish(kind, final=True)
    emit.finish = finish
    return emit


def _responses_body(result, effort):
    inputs = []
    for message in result.get("messages", []):
        role = message["role"]
        if role == "assistant" and isinstance(message.get("_responses_output"), list):
            # Native output includes opaque state needed for stateless tool loops.
            inputs.extend(copy.deepcopy(message["_responses_output"]))
        elif role == "tool":
            inputs.append({"type": "function_call_output", "call_id": message["tool_call_id"], "output": message["content"]})
        else:
            if message.get("content") is not None:
                inputs.append({"role": role, "content": message["content"]})
            if role == "assistant":
                for call in message.get("tool_calls", []):
                    inputs.append({"type": "function_call", "call_id": call["id"],
                                   "name": call["function"]["name"], "arguments": call["function"]["arguments"]})
    wire = {"model": result["model"], "input": inputs, "store": False,
            "max_output_tokens": result.get("max_completion_tokens", result.get("max_tokens", 6000)),
            "reasoning": {"summary": "auto"}, "include": ["reasoning.encrypted_content"]}
    if effort:
        wire["reasoning"]["effort"] = effort
    if result.get("stream"):
        wire["stream"] = True
    if result.get("tools"):
        wire["tools"] = [{"type": "function", "name": t["function"]["name"],
                          "description": t["function"].get("description", ""),
                          "parameters": t["function"]["parameters"], "strict": False} for t in result["tools"]]
    if "tool_choice" in result:
        choice = result["tool_choice"]
        wire["tool_choice"] = {"type": "function", "name": choice["function"]["name"]} if isinstance(choice, dict) else choice
    fmt = result.get("response_format")
    if fmt:
        if fmt.get("type") == "json_schema":
            schema = fmt["json_schema"]
            wire["text"] = {"format": {"type": "json_schema", "name": schema["name"],
                                        "schema": schema["schema"], "strict": schema.get("strict", False)}}
        elif fmt.get("type") in ("json_object", "text"):
            wire["text"] = {"format": {"type": fmt["type"]}}
        else:
            raise ProviderError("Responses 输出约束格式无效。")
    return wire


def _responses_response(result, emit):
    if result.get("error") or result.get("status") not in ("completed", "incomplete"):
        raise ProviderError("Responses 请求失败或未完整结束。")
    output = result.get("output")
    if not isinstance(output, list):
        raise ProviderError("Responses 输出格式无效。")
    texts, calls, refused = [], [], False
    for item in output:
        if not isinstance(item, dict):
            raise ProviderError("Responses 输出条目无效。")
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
                elif part.get("type") == "refusal":
                    refused = True
        elif item.get("type") == "function_call":
            if not all(isinstance(item.get(k), str) for k in ("call_id", "name", "arguments")):
                raise ProviderError("Responses 工具调用格式无效。")
            calls.append({"id": item["call_id"], "type": "function",
                          "function": {"name": item["name"], "arguments": item["arguments"]}})
        elif item.get("type") == "reasoning":
            for part in item.get("summary", []):
                if part.get("type") == "summary_text":
                    emit("summary", part.get("text"))
    finish = "length" if result["status"] == "incomplete" else "content_filter" if refused else "tool_calls" if calls else "stop"
    return {"model": result.get("model"), "usage": _safe_usage(result.get("usage")),
            "choices": [{"finish_reason": finish, "message": {"role": "assistant", "content": "".join(texts),
                         "tool_calls": calls, "_responses_output": copy.deepcopy(output)}}]}


def _stream_responses(response, on_delta, cancelled, emit):
    items, shown_summaries, shown_text = {}, {}, {}
    def public_summary(index, part_index, value, *, delta=False):
        _check_cancel(cancelled)
        key = (index, part_index)
        previous = shown_summaries.get(key, "")
        if not isinstance(value, str):
            return
        addition = value if delta else value[len(previous):] if value.startswith(previous) else ""
        if addition:
            emit("summary", addition)
            shown_summaries[key] = previous + addition
    def public_text(index, part_index, value, *, delta=False):
        _check_cancel(cancelled)
        key = (index, part_index)
        previous = shown_text.get(key, "")
        if not isinstance(value, str):
            raise ProviderError("Responses 流式文本格式无效。")
        addition = value if delta else value[len(previous):] if value.startswith(previous) else ""
        if addition:
            shown_text[key] = previous + addition
            if on_delta:
                on_delta(addition)
    for raw in _sse_events(response, cancelled):
        if raw == "[DONE]":
            break
        event = json.loads(raw)
        if not isinstance(event, dict) or event.get("error") or event.get("type") in ("error", "response.failed"):
            raise ProviderError("Responses 流式响应中断。")
        kind, index = event.get("type"), event.get("output_index", 0)
        if kind == "response.output_item.added":
            items[index] = copy.deepcopy(event["item"])
        elif kind == "response.output_item.done":
            items[index] = copy.deepcopy(event["item"])
        elif kind == "response.function_call_arguments.delta":
            item = items.get(index)
            if not item or item.get("type") != "function_call":
                raise ProviderError("Responses 工具事件顺序无效。")
            item["arguments"] = item.get("arguments", "") + event.get("delta", "")
        elif kind == "response.function_call_arguments.done" and index in items:
            items[index]["arguments"] = event["arguments"]
        elif kind == "response.output_text.delta":
            public_text(index, event.get("content_index", 0), event.get("delta"), delta=True)
        elif kind == "response.output_text.done":
            public_text(index, event.get("content_index", 0), event.get("text"))
        elif kind == "response.reasoning_summary_text.delta":
            public_summary(index, event.get("summary_index", 0), event.get("delta"), delta=True)
        elif kind == "response.reasoning_summary_text.done":
            public_summary(index, event.get("summary_index", 0), event.get("text"))
        elif kind == "response.reasoning_summary_part.done":
            part = event.get("part", {})
            if part.get("type") == "summary_text":
                public_summary(index, event.get("summary_index", 0), part.get("text"))
        elif kind in ("response.completed", "response.incomplete"):
            result = copy.deepcopy(event.get("response", {}))
            result.setdefault("output", [items[k] for k in sorted(items)])
            for out_index, item in enumerate(result["output"]):
                if item.get("type") == "reasoning":
                    for part_index, part in enumerate(item.get("summary", [])):
                        if part.get("type") == "summary_text":
                            public_summary(out_index, part_index, part.get("text"))
                elif item.get("type") == "message":
                    for part_index, part in enumerate(item.get("content", [])):
                        if part.get("type") == "output_text":
                            public_text(out_index, part_index, part.get("text"))
            _check_cancel(cancelled)
            return _responses_response(result, lambda *a: None)
        # Do not expose reasoning_text, encrypted content, signatures, or opaque data.
    raise ProviderError("Responses 流式响应未完整结束；已接收文字仅为部分回答。")


def _wire_body(config, body):
    result = copy.deepcopy(body)
    protocol = config.get("protocol", "openai")
    effort = result.get("reasoning_effort")
    if effort and effort not in ("none", "minimal", "low", "medium", "high", "xhigh"):
        raise ProviderError("无效的思考强度，请选择模型默认或可用档位。", category="reasoning_parameter")
    if protocol == "responses":
        return _responses_body(result, effort)
    if protocol == "anthropic":
        system, messages = [], []
        for message in result.get("messages", []):
            role = message["role"]
            if role == "system":
                system.append(message["content"])
            elif role == "tool":
                messages.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": message["content"]}]})
            elif role == "assistant" and isinstance(message.get("_native_content"), list):
                messages.append({"role": role, "content": message["_native_content"]})
            elif role == "assistant" and message.get("tool_calls"):
                blocks = [{"type": "text", "text": message["content"]}] if message.get("content") else []
                for call in message["tool_calls"]:
                    blocks.append({"type": "tool_use", "id": call["id"], "name": call["function"]["name"], "input": json.loads(call["function"]["arguments"])})
                messages.append({"role": role, "content": blocks})
            else:
                messages.append({"role": role, "content": message["content"]})
        wire = {"model": result["model"], "system": "\n\n".join(system), "messages": messages,
                "max_tokens": result.get("max_tokens", result.get("max_completion_tokens", 6000))}
        if result.get("stream"):
            wire["stream"] = True
        if result.get("tools"):
            wire["tools"] = [{"name": t["function"]["name"], "description": t["function"].get("description", ""), "input_schema": t["function"]["parameters"]} for t in result["tools"]]
        if effort == "none":
            wire["thinking"] = {"type": "disabled"}
        elif effort:
            wire["thinking"] = {"type": "adaptive"}
            wire["output_config"] = {"effort": "low" if effort == "minimal" else effort}
        return wire
    if config.get("provider") != "openai":
        result["max_tokens"] = result.pop("max_completion_tokens", result.get("max_tokens", 6000))
        result.pop("store", None)
    if config.get("provider") == "deepseek" and effort:
        result["thinking"] = {"type": "disabled" if effort == "none" else "enabled"}
        if effort == "none":
            result.pop("reasoning_effort", None)
    elif config.get("provider") == "qwen" and effort:
        result.pop("reasoning_effort", None)
        result["enable_thinking"] = effort != "none"
        if effort != "none":
            result["thinking_budget"] = {"minimal": 1024, "low": 4096, "medium": 8192,
                                         "high": 16384, "xhigh": 32768}[effort]
    return result


def _normal_response(config, result, emit=None):
    emit = emit or (lambda *a: None)
    if result.get("error"):
        raise ProviderError("模型服务返回错误；请检查模型权限或稍后重试。")
    if config.get("protocol") == "responses":
        return _responses_response(result, emit)
    if config.get("protocol") != "anthropic":
        choices = result.get("choices", [])
        if choices:
            emit("reasoning", choices[0].get("message", {}).get("reasoning_content"))
        return result
    content, calls = [], []
    for block in result.get("content", []):
        if block.get("type") == "thinking":
            emit("reasoning", block.get("thinking"))
        if block.get("type") == "text":
            content.append(block.get("text", ""))
        elif block.get("type") == "tool_use":
            calls.append({"id": block["id"], "type": "function", "function": {"name": block["name"], "arguments": json.dumps(block["input"], ensure_ascii=False)}})
    stop = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length"}.get(result.get("stop_reason"), result.get("stop_reason"))
    return {"model": result.get("model"), "usage": _safe_usage(result.get("usage")),
            "choices": [{"finish_reason": stop, "message": {"role": "assistant", "content": "".join(content),
                         "tool_calls": calls, "_native_content": copy.deepcopy(result.get("content", []))}}]}


def _sse_events(response, cancelled):
    data, size, total = [], 0, 0
    while True:
        _check_cancel(cancelled)
        raw = response.readline(1_000_001)
        _check_cancel(cancelled)
        if not raw:
            if data:
                yield "\n".join(data)
            return
        total += len(raw)
        size += len(raw)
        if len(raw) > 1_000_000 or size > 1_000_000 or total > 8_000_000:
            raise ProviderError("模型流式响应过大。")
        line = raw.decode("utf-8").rstrip("\r\n")
        if not line:
            if data:
                yield "\n".join(data)
            data, size = [], 0
        elif line.startswith("data:"):
            data.append(line[5:].lstrip(" "))


def _stream_response(config, response, on_delta, cancelled, emit=None):
    emit = emit or (lambda *a: None)
    if config.get("protocol") == "responses":
        return _stream_responses(response, on_delta, cancelled, emit)
    text, calls, usage, model, finish, ended = [], {}, {}, None, None, False
    native_blocks, reasoning = {}, []
    native = config.get("protocol") == "anthropic"
    for data in _sse_events(response, cancelled):
        if data == "[DONE]":
            ended = True
            break
        event = json.loads(data)
        if not isinstance(event, dict) or event.get("error") or event.get("type") == "error":
            raise ProviderError("模型流式响应中断。")
        delta_text = ""
        if native:
            kind = event.get("type")
            if kind == "message_start":
                model = event.get("message", {}).get("model")
                usage.update(_safe_usage(event.get("message", {}).get("usage")))
            elif kind == "content_block_start":
                block = event.get("content_block", {})
                native_blocks[event["index"]] = copy.deepcopy(block)
                if block.get("type") == "thinking":
                    emit("reasoning", block.get("thinking"))
                if block.get("type") == "text":
                    delta_text = block.get("text", "")
                elif block.get("type") == "tool_use":
                    calls[event["index"]] = {"id": block["id"], "type": "function", "function": {"name": block["name"], "arguments": ""}}
            elif kind == "content_block_delta":
                delta = event.get("delta", {})
                block = native_blocks.get(event["index"], {})
                if delta.get("type") == "text_delta":
                    delta_text = delta.get("text", "")
                    block["text"] = block.get("text", "") + delta_text
                elif delta.get("type") == "input_json_delta":
                    calls[event["index"]]["function"]["arguments"] += delta.get("partial_json", "")
                elif delta.get("type") == "thinking_delta":
                    block["thinking"] = block.get("thinking", "") + delta.get("thinking", "")
                    if block.get("type") == "thinking":
                        emit("reasoning", delta.get("thinking"))
                elif delta.get("type") == "signature_delta":
                    block["signature"] = block.get("signature", "") + delta.get("signature", "")
            elif kind == "message_delta":
                finish = {"end_turn": "stop", "stop_sequence": "stop", "tool_use": "tool_calls", "max_tokens": "length"}.get(event.get("delta", {}).get("stop_reason"), "unknown")
                usage.update(_safe_usage(event.get("usage")))
            elif kind == "message_stop":
                ended = True
                break
        else:
            model = event.get("model", model)
            usage.update(_safe_usage(event.get("usage")))
            for choice in event.get("choices", []):
                if choice.get("index", 0) != 0:
                    continue
                finish = choice.get("finish_reason") or finish
                delta = choice.get("delta", {})
                delta_text = delta.get("content") or ""
                if isinstance(delta.get("reasoning_content"), str):
                    reasoning.append(delta["reasoning_content"])
                    emit("reasoning", delta["reasoning_content"])
                for part in delta.get("tool_calls", []):
                    index = part.get("index", 0)
                    call = calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    call["id"] += part.get("id", "")
                    for key in ("name", "arguments"):
                        call["function"][key] += part.get("function", {}).get(key, "")
        if delta_text:
            if not isinstance(delta_text, str):
                raise ProviderError("模型流式文本格式无效。")
            text.append(delta_text)
            if on_delta:
                on_delta(delta_text)
    _check_cancel(cancelled)
    if not ended or finish is None:
        raise ProviderError("模型流式响应未完整结束；已接收文字仅为部分回答。")
    message = {"role": "assistant", "content": "".join(text), "tool_calls": [calls[k] for k in sorted(calls)]}
    if native:
        for index, call in calls.items():
            block = native_blocks.get(index)
            if block is not None:
                block["input"] = json.loads(call["function"]["arguments"] or json.dumps(block.get("input", {})))
                if not call["function"]["arguments"]:
                    call["function"]["arguments"] = json.dumps(block["input"])
        message["_native_content"] = [native_blocks[k] for k in sorted(native_blocks)]
    elif reasoning:
        message["reasoning_content"] = "".join(reasoning)
    return {"model": model, "usage": usage, "choices": [{"finish_reason": finish, "message": message}]}


def _http_error_category(exc):
    """Inspect a bounded error in memory; return fixed labels, never provider text."""
    if exc.code not in (400, 422):
        return "http_error"
    try:
        payload = json.loads(exc.read(64000))
        error = payload.get("error", payload) if isinstance(payload, dict) else {}
        message = error.get("message", "") if isinstance(error, dict) else ""
        if not isinstance(message, str):
            return "invalid_request"
        value = message.lower()
        tool = r"(?:tools?|function(?:s| calling)?)"
        unsupported = r"(?:not support(?:ed)?|unsupported|not available|not implemented)"
        if re.search(tool, value) and any(term in value for term in ("schema", "additionalproperties", "required", "invalid argument", "parameters.properties")):
            return "tool_schema"
        if re.search(tool + r".{0,100}" + unsupported + "|" + unsupported + r".{0,100}" + tool, value):
            return "tools_unsupported"
        if any(key in value for key in ("reasoning_effort", "reasoning.summary", "reasoning.effort", "thinking", "output_config", "thinking_budget")):
            return "reasoning_parameter"
        return "invalid_request"
    except (OSError, ValueError, TypeError, http.client.HTTPException):
        return "invalid_request"


def request(config, path, body=None, *, timeout=120, on_delta=None, cancelled=None, on_reasoning=None):
    _check_cancel(cancelled)
    source = config.get("key_source", "environment")
    base = validate_base_url(config["base_url"], key_source=source)
    secret = config.get("_secret")
    key = secret() if callable(secret) else config.get("_key", "")
    if source == "none":
        key = ""
    elif not key:
        raise ProviderError("未配置可用 API 密钥；请设置环境变量或重新输入密钥。")
    if key:
        from .diagnostics import register_secret
        register_secret(key)
    native = config.get("protocol") == "anthropic"
    headers = {"Content-Type": "application/json", "Accept": "text/event-stream" if body and body.get("stream") else "application/json"}
    if key:
        headers["x-api-key" if native else "Authorization"] = key if native else "Bearer " + key
    if native:
        headers["anthropic-version"] = "2023-06-01"
    if path == "/chat/completions":
        if native:
            path = "/messages"
        elif config.get("protocol") == "responses":
            path = "/responses"
    if not path.startswith("/") or path.startswith("//"):
        raise ProviderError("API 路径无效。")
    wire = _wire_body(config, body) if body is not None else None
    req = urllib.request.Request(base + path, data=json.dumps(wire, ensure_ascii=False).encode("utf-8") if wire is not None else None, headers=headers)
    emit_reasoning = reasoning_callback(on_reasoning, cancelled)
    stop_watch = threading.Event()
    response = None
    try:
        response = _open(req, timeout)
        if cancelled:
            # Interrupt a stalled streaming read when possible. Connect/headers are
            # bounded by timeout; there is no claim of instantaneous connect abort.
            def watch():
                while not stop_watch.wait(.1):
                    if cancelled():
                        try:
                            sock = response.fp.raw._sock
                            sock.shutdown(socket.SHUT_RDWR)
                        except (AttributeError, OSError):
                            pass
                        return
            threading.Thread(target=watch, daemon=True).start()
        with response:
            _check_cancel(cancelled)
            if body and body.get("stream"):
                return _stream_response(config, response, on_delta, cancelled, emit_reasoning)
            raw = response.read(8_000_001)
            _check_cancel(cancelled)
            if len(raw) > 8_000_000:
                raise ProviderError("API 返回过大。")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ProviderError("API 返回的顶层结构无效。")
            if result.get("error"):
                raise ProviderError("模型服务返回错误；请检查模型权限或稍后重试。")
            normalized = _normal_response(config, result, emit_reasoning) if body is not None else result
            _check_cancel(cancelled)
            return normalized
    except urllib.error.HTTPError as exc:
        category = _http_error_category(exc)
        exc.close()
        hint = ("工具参数格式不被服务接受；请检查工具协议兼容性。" if category == "tool_schema" else
                "模型不接受所选思考设置；请选择模型默认或该模型支持的强度。" if category == "reasoning_parameter" else
                "请检查模型权限、余额和 API 地址。")
        raise ProviderError(f"模型服务 HTTP {exc.code}；{hint}", status=exc.code, category=category) from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException):
        _check_cancel(cancelled)
        raise ProviderError("模型服务网络连接失败或超时。") from None
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        _check_cancel(cancelled)
        raise ProviderError("模型服务返回格式无效。") from None
    finally:
        stop_watch.set()
        emit_reasoning.finish()


class ModelRegistry:
    def __init__(self, data_dir):
        self._path = Path(data_dir) / "model-profiles.json"
        with _LOCKS_LOCK:
            self._lock = _LOCKS.setdefault(str(self._path.resolve()).casefold(), threading.RLock())

    def presets(self):
        return copy.deepcopy(PRESETS)

    def _read(self):
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
                raise ValueError()
            return data["profiles"]
        except (ValueError, OSError):
            raise ProviderError("模型配置无法读取；原文件已保留。") from None

    def _write(self, profiles):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp = self._path.with_name(self._path.name + "." + uuid.uuid4().hex + ".tmp")
        try:
            temp.write_text(json.dumps({"version": 1, "profiles": profiles}, ensure_ascii=False, indent=2), encoding="utf-8")
            # Windows antivirus/indexers can briefly hold a newly read file;
            # keep the transaction lock and retry without deleting the old file.
            for attempt in range(6):
                try:
                    os.replace(temp, self._path)
                    break
                except PermissionError:
                    if attempt == 5:
                        raise ProviderError("模型配置暂时被占用，未覆盖旧配置；请重试。") from None
                    time.sleep(.02 * (attempt + 1))
        finally:
            temp.unlink(missing_ok=True)

    def _secret_for(self, profile):
        source = profile["key_source"]
        if source == "manual":
            try:
                return unprotect(profile.get("_protected_key", "")) if profile.get("_protected_key") else ""
            except CredentialError as exc:
                raise ProviderError(str(exc)) from None
        if source == "environment":
            from .agent import _environment
            env = profile.get("env_var", "")
            names = (env,)
            if env == "OPENAI_API_KEY":
                names += ("OpenAI", "OPENAI")
            elif env == "DEEPSEEK_API_KEY":
                names += ("DEEPSEEK",)
            return _environment(names) if env else ""
        return ""

    def _public(self, profile):
        allowed = {"id", "label", "provider", "protocol", "base_url", "model", "key_source", "env_var", "models", "reasoning_effort", "max_tokens"}
        public = {key: copy.deepcopy(value) for key, value in profile.items() if key in allowed}
        try:
            public["has_key"] = bool(self._secret_for(profile))
        except ProviderError:
            public["has_key"] = False
        return public

    def public_profiles(self):
        with self._lock:
            return [self._public(p) for p in self._read().values()]

    def save_profile(self, payload, api_key=None):
        if not isinstance(payload, dict):
            raise ProviderError("模型配置格式无效。")
        with self._lock:
            profiles = self._read()
            ident = payload.get("id") or uuid.uuid4().hex
            if not isinstance(ident, str) or not re.fullmatch(r"[\w-]{1,100}", ident):
                raise ProviderError("模型配置 ID 无效。")
            old = profiles.get(ident, {})
            provider = payload.get("provider", old.get("provider", "custom"))
            preset = next((p for p in PRESETS if p["provider"] == provider), None)
            if preset is None:
                raise ProviderError("模型服务类型无效。")
            p = {**preset, **old}
            for key in ("label", "protocol", "base_url", "model", "key_source", "env_var", "reasoning_effort", "max_tokens"):
                if key in payload:
                    p[key] = payload[key]
            p.update(id=ident, provider=provider, models=old.get("models", []))
            p.setdefault("key_source", "environment")
            if api_key:
                p["key_source"] = "manual"
            if p["protocol"] not in ("openai", "anthropic", "responses") or p["key_source"] not in ("manual", "environment", "none"):
                raise ProviderError("模型协议或密钥来源无效。")
            for key in ("label", "model", "env_var"):
                if not isinstance(p[key], str) or len(p[key]) > 200 or any(ord(c) < 32 for c in p[key]):
                    raise ProviderError("模型名称或环境变量名称无效。")
            if p["env_var"] and not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", p["env_var"]):
                raise ProviderError("环境变量名称无效。")
            if "max_tokens" in p:
                try:
                    p["max_tokens"] = int(p["max_tokens"])
                except (TypeError, ValueError):
                    raise ProviderError("输出长度应为整数。") from None
                if not 256 <= p["max_tokens"] <= 128000:
                    raise ProviderError("输出长度须在 256–128000 之间。")
            if p.get("reasoning_effort", "") not in ("", "none", "minimal", "low", "medium", "high", "xhigh"):
                raise ProviderError("推理强度无效。")
            p["base_url"] = validate_base_url(p["base_url"], key_source=p["key_source"])
            if old and (p["base_url"] != old["base_url"] or p["protocol"] != old["protocol"]):
                p["models"] = []
                if p["key_source"] == "manual" and not api_key:
                    raise ProviderError("更换 API 地址或协议时请重新输入密钥。")
            if api_key is not None and (not isinstance(api_key, str) or len(api_key) > 8192 or any(ord(c) < 32 for c in api_key)):
                raise ProviderError("密钥格式无效。")
            if api_key:
                from .diagnostics import register_secret
                register_secret(api_key.strip())
                try:
                    p["_protected_key"] = protect(api_key.strip())
                except CredentialError as exc:
                    raise ProviderError(str(exc)) from None
            elif api_key == "" and p["key_source"] == "manual":
                # Blank editing fields retain an existing secret. Switch the source
                # or delete the profile to remove it; plaintext is never returned.
                p.setdefault("_protected_key", "")
            if p["key_source"] != "manual":
                p.pop("_protected_key", None)
            profiles[ident] = p
            self._write(profiles)
            return self._public(p)

    def delete_profile(self, ident):
        with self._lock:
            profiles = self._read()
            profiles.pop(ident, None)
            self._write(profiles)

    def resolve(self, ident):
        with self._lock:
            profile = self._read().get(ident)
            if profile is None:
                raise ProviderError("所选模型配置不存在，请重新选择。")
            profile = copy.deepcopy(profile)
            result = self._public(profile)
            if not result["model"].strip():
                raise ProviderError("请填写模型 ID 或先获取模型列表。")
            def secret():
                value = self._secret_for(profile)
                if value:
                    from .diagnostics import register_secret
                    register_secret(value)
                return value
            result["_secret"] = secret
            return result

    def discover_models(self, ident):
        with self._lock:
            profile = copy.deepcopy(self._read().get(ident))
            if profile is None:
                raise ProviderError("模型配置不存在。")
        config = self._public(profile)
        config["_secret"] = lambda: self._secret_for(profile)
        models, cursor, received = set(), "", 0
        is_qwen = config["provider"] == "qwen"
        if is_qwen:
            # DashScope discovery is a native API at the SAME origin, not an
            # undocumented OpenAI-compatible /models endpoint.
            parsed = urlsplit(config["base_url"])
            config["base_url"] = f"{parsed.scheme}://{parsed.netloc}/api/v1"
        for page in range(1, 21):
            path = "/models"
            if is_qwen:
                path += f"?page_no={page}&page_size=100&capabilities=TG"
            elif cursor:
                path += "?after_id=" + quote(cursor, safe="")
            result = request(config, path, timeout=30)
            if is_qwen:
                output = result.get("output", {})
                rows = output.get("models") if isinstance(output, dict) else None
                if result.get("success") is not True or not isinstance(rows, list):
                    raise ProviderError("服务未提供有效的模型列表；请检查业务空间地址或直接填写模型 ID。")
                received += len(rows)
                total = output.get("total")
                if type(total) not in (int, float) or total < 0:
                    raise ProviderError("模型列表分页格式无效。")
                rows = [{"id": row.get("model")} for row in rows if isinstance(row, dict)]
                more = received < total
            else:
                rows = result.get("data")
                if not isinstance(rows, list):
                    raise ProviderError("服务未提供有效的模型列表；可直接填写模型 ID。")
                more = config["protocol"] == "anthropic" and result.get("has_more")
            for item in rows:
                if isinstance(item, dict) and isinstance(item.get("id"), str) and len(item["id"]) <= 200:
                    models.add(item["id"])
            if not more:
                break
            if not is_qwen:
                next_cursor = result.get("last_id")
                if not isinstance(next_cursor, str) or next_cursor == cursor:
                    raise ProviderError("模型列表分页格式无效。")
                cursor = next_cursor
        else:
            raise ProviderError("模型列表过大；请直接填写模型 ID。")
        with self._lock:
            profiles = self._read()
            if ident in profiles and profiles[ident] == profile:
                profiles[ident]["models"] = sorted(models)
                self._write(profiles)
        return sorted(models)
