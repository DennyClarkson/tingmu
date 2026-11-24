"""Text transport and structured completion, independent of learning and Qt.

Profiles retain lazy credential resolvers. Public metadata contains provider-safe
accounting only; neither credentials nor response bodies enter that accounting.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
import time
from urllib.parse import urlsplit

from .providers import ProviderError as AgentError, request as provider_request, safe_metadata


def environment(names: tuple[str, ...]) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    if os.name == "nt":
        import winreg

        for root, path in (
            (winreg.HKEY_CURRENT_USER, r"Environment"),
            (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        ):
            try:
                with winreg.OpenKey(root, path) as key:
                    for name in names:
                        try:
                            value = str(winreg.QueryValueEx(key, name)[0]).strip()
                            if value:
                                return value
                        except OSError:
                            pass
            except OSError:
                pass
    return ""


def default_config(provider: str, *, environment=environment) -> tuple[str, str, str]:
    if provider not in ("openai", "deepseek"):
        raise AgentError("请选择 OpenAI、DeepSeek 或本地模式。")
    prefix = provider.upper()
    aliases = (
        ("OPENAI_API_KEY", "OpenAI", "OPENAI") if provider == "openai" else ("DEEPSEEK_API_KEY", "DEEPSEEK")
    )
    key = environment(aliases)
    base = environment((prefix + "_BASE_URL", prefix + "_API_BASE")) or (
        "https://api.openai.com/v1" if provider == "openai" else "https://api.deepseek.com"
    )
    parsed = urlsplit(base)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise AgentError("API 地址必须是无用户信息和查询参数的 HTTPS 地址。")
    model = environment((prefix + "_MODEL",)) or (
        "gpt-5.4-mini" if provider == "openai" else "deepseek-flash"
    )
    return key, base.rstrip("/"), model


class LlmClient:
    def __init__(self, provider="deepseek", model=None, *, profile=None, config_resolver=None):
        self._profile = deepcopy(profile) if profile else None
        self.provider = (self._profile.get("provider", provider) if self._profile else provider).lower()
        if self._profile:
            self.model = self._profile["model"]
            self._key, self._base = "", self._profile["base_url"]
        elif self.provider == "local":
            self.model, self._key, self._base = "local-dictionaries", "", ""
        else:
            self._key, self._base, default = (config_resolver or default_config)(self.provider)
            self.model = model or default
        self.timeout = 120
        self.max_completion_tokens = self._profile.get("max_tokens", 6000) if self._profile else 6000
        self.reasoning_effort = ""
        self.last_response_metadata = {}
        self.response_metadata = []

    def fork(self):
        """Create a fresh request context without rereading environment or credentials."""
        child = LlmClient("local")
        for key in ("provider", "model", "_profile", "_key", "_base", "timeout",
                    "max_completion_tokens", "reasoning_effort"):
            setattr(child, key, deepcopy(getattr(self, key)))
        return child

    def transport_config(self):
        return deepcopy(self._profile) if self._profile else {
            "provider": self.provider, "protocol": "openai", "base_url": self._base,
            "_key": self._key, "key_source": "environment"}

    def _transport_config(self):
        """Legacy transport inspection hook; new collaborators use transport_config."""
        return self.transport_config()

    def request(self, path, body=None, *, on_reasoning=None):
        options = {"on_reasoning": on_reasoning} if on_reasoning is not None else {}
        return self._request(path, body, **options)

    def _request(self, path, body=None, *, on_reasoning=None):
        """Compatibility hook for existing injected provider fixtures."""
        return provider_request(self.transport_config(), path, body,
                                timeout=self.timeout, on_reasoning=on_reasoning)

    def stream_request(self, body, *, on_delta=None, cancelled=None, on_reasoning=None):
        options = {"on_reasoning": on_reasoning} if on_reasoning is not None else {}
        return self._stream_request(body, on_delta=on_delta, cancelled=cancelled, **options)

    def _stream_request(self, body, *, on_delta=None, cancelled=None, on_reasoning=None):
        return provider_request(self.transport_config(), "/chat/completions", body,
                                timeout=self.timeout, on_delta=on_delta, cancelled=cancelled,
                                on_reasoning=on_reasoning)

    def list_models(self):
        result = self.request("/models")
        return sorted(item["id"] for item in result.get("data", [])
                      if isinstance(item, dict) and isinstance(item.get("id"), str))

    def complete(self, system: str, payload: dict, schema: dict | None = None, *,
                 request=None, annotation_version=None) -> str:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if self.provider == "openai":
            body["max_completion_tokens"] = self.max_completion_tokens
            body["store"] = False
            if self.model.startswith(("gpt-5", "o1", "o3", "o4")):
                body["reasoning_effort"] = "low"
            elif self.model.startswith("gpt-6"):
                body["reasoning_effort"] = "medium"
            if schema:
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "subtitle_annotations", "strict": True, "schema": schema},
                }
        else:
            body["max_tokens"] = self.max_completion_tokens
            if self.provider == "deepseek":
                body["thinking"] = {"type": "disabled"}
            if schema:
                body["response_format"] = {"type": "json_object"}
        if self._profile and self._profile.get("reasoning_effort"):
            body["reasoning_effort"] = self._profile["reasoning_effort"]
        if self.reasoning_effort and self.reasoning_effort != "default":
            body["reasoning_effort"] = self.reasoning_effort
        if schema:
            # Native Anthropic and compatibility fallbacks still receive the exact
            # schema in the trusted instruction. Local validation is never relaxed.
            body["messages"][0]["content"] += "\nRequired JSON schema: " + json.dumps(schema, ensure_ascii=False)
        started = time.monotonic()
        for format_attempt in range(3):
            try:
                response = (request or self.request)("/chat/completions", body)
                break
            except AgentError as exc:
                if (exc.status not in (400, 422) or exc.category in ("tool_schema", "reasoning_parameter")
                        or not body.get("response_format") or format_attempt == 2):
                    raise
                if body["response_format"]["type"] == "json_schema":
                    body["response_format"] = {"type": "json_object"}
                else:
                    body.pop("response_format")
        self.last_response_metadata = {
            "provider": self.provider,
            "requested_model": self.model,
            **safe_metadata(response),
            "latency_seconds": round(time.monotonic() - started, 3),
            "finish_reason": None,
            "thinking": body.get("thinking"),
            "reasoning_effort": body.get("reasoning_effort"),
            "max_output_tokens": self.max_completion_tokens,
            "response_format": body.get("response_format", {}).get("type", "prompt"),
        }
        if annotation_version is not None:
            self.last_response_metadata["annotation_version"] = annotation_version
        self.response_metadata.append(self.last_response_metadata)
        try:
            choice = response["choices"][0]
            self.last_response_metadata["finish_reason"] = choice.get("finish_reason")
            if choice.get("finish_reason") != "stop":
                raise AgentError("模型回答被截断或拒绝，未应用此批结果。",
                                 category="length" if choice.get("finish_reason") == "length" else "response")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise AgentError("模型返回空内容，未应用此批结果。")
            return content
        except (KeyError, IndexError, TypeError):
            raise AgentError("API 返回的回答结构无效。") from None


class LegacyCompletionAdapter:
    """Adapt the pre-refactor injected completion protocol at one compatibility edge."""
    def __init__(self, legacy):
        self._complete = legacy._complete

    def complete(self, system, payload, schema=None):
        return self._complete(system, payload, schema)


def completion_port(value):
    """Prefer the public protocol, retaining old external organizer fixtures."""
    return value if callable(getattr(value, "complete", None)) else LegacyCompletionAdapter(value)
