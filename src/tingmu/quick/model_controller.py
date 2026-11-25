"""Model domain operations with owned state and explicit callback ports."""

from copy import deepcopy
import hashlib
import json


from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ModelPorts:
    model_registry: Callable
    modelsChanged: Any
    queue: Callable
    set_state: Callable
    settings: Callable
    settingsChanged: Any
    start_job: Callable
    store: Callable
    sync_chat_stopping: Callable
    task_workers: Callable


class ModelController:
    def __init__(self, state, ports):
        self.state, self.ports = state, ports

    def refresh(self):
        return self._refresh_models()

    def _refresh_models(self):
        self.ports.model_registry().load_roles(self.ports.settings())
        self.state.profiles = self.ports.model_registry().public_profiles()
        self.ports.settingsChanged.emit()
        self.ports.modelsChanged.emit()

    def _profile(self, role):
        profile_id = getattr(self.ports.settings(), role + "_profile_id", "")
        return self.ports.model_registry().resolve(profile_id) if profile_id else None

    def _tutor_transport(self, *, profile=None, provider=None, model=None, reasoning_effort=None):
        from tingmu.agent import _config
        from tingmu.llm_client import LlmClient

        if provider is None:
            profile = self._profile("companion")
        agent = LlmClient(
            provider or self.ports.settings().companion_provider,
            model or self.ports.settings().companion_model,
            profile=profile,
            config_resolver=_config,
        )
        effort = (
            reasoning_effort
            if reasoning_effort is not None
            else self.ports.settings().companion_reasoning_effort
        )
        if effort != "default":
            agent.reasoning_effort = effort
        return agent

    def _chat_model_snapshot(self, previous=None):
        if previous is not None:
            profile_id = previous.get("id", "")
            if profile_id:
                original_id = profile_id
                if previous.get("scope") != "global":
                    profile_id = (
                        self.ports.model_registry()
                        .legacy_profile_ids()
                        .get(profile_id, "missing:" + profile_id)
                    )
                from tingmu.providers import ProviderError

                if profile_id not in {p["id"] for p in self.ports.model_registry().public_profiles()}:
                    raise ValueError("原消息使用的模型连接已删除，无法原样重试；请发起新消息选择连接。")
                try:
                    profile = self.ports.model_registry().resolve(profile_id)
                except ProviderError:
                    raise ValueError("原消息使用的模型连接当前不可用，请检查连接后重试。") from None
                fingerprint = self._profile_fingerprint({**profile, "id": original_id})
                if previous.get("fingerprint") != fingerprint:
                    raise ValueError("原消息的模型连接配置已改变，无法原样重试；请使用新消息。")
            else:
                profile = None
            return profile, deepcopy(previous)
        profile = self._profile("companion")
        public = {
            "provider": self.ports.settings().companion_provider,
            "model": self.ports.settings().companion_model,
            "id": "",
        }
        if profile:
            public = {
                k: deepcopy(profile[k])
                for k in ("id", "provider", "model", "protocol", "reasoning_effort", "max_tokens")
                if k in profile
            }
            public["fingerprint"] = self._profile_fingerprint(profile)
            public["scope"] = "global"
        return profile, public

    @staticmethod
    def _profile_fingerprint(profile):
        # Detect endpoint/config changes without storing URLs, credentials or private profiles.
        public = {
            k: profile.get(k)
            for k in ("id", "provider", "model", "protocol", "base_url", "reasoning_effort", "max_tokens")
        }
        return hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()

    def _stop_model_jobs(self):
        for key in ("chat", "notebook", "summary", "models", "queue"):
            worker = self.ports.task_workers().get(key)
            if worker:
                worker[1].set()
                if key == "queue":
                    self.ports.queue().pause()
        self.ports.sync_chat_stopping()

    def command_text_model_policy(self, name, p):
        mode = p.get("mode")
        if mode not in ("off", "local", "remote"):
            raise ValueError("文本模型使用范围无效。")
        restricted = (self.ports.settings().text_models_enabled and mode == "off") or (
            self.ports.settings().cloud_text_enabled and mode != "remote"
        )
        self.ports.settings().text_models_enabled = mode != "off"
        self.ports.settings().cloud_text_enabled = mode == "remote"
        self.ports.store().save_settings(self.ports.settings())
        self.ports.settingsChanged.emit()
        self.ports.set_state(cloudEnabled=self.ports.settings().cloud_text_enabled)
        if restricted:
            self._stop_model_jobs()

    def command_save_model(self, name, p):
        payload = dict(p.get("profile", p))
        key = payload.pop("apiKey", p.get("apiKey", None))
        if payload.get("key_source") != "manual":
            key = None
        saved = self.ports.model_registry().save_profile(payload, api_key=key or None)
        self.state.status = "模型配置已保存"
        self._refresh_models()
        return saved["id"]

    def command_delete_model(self, name, p):
        profile_id = p["id"]
        if "queue" in self.ports.task_workers():
            raise ValueError("请先暂停队列再删除模型配置")
        self.ports.model_registry().delete_profile(profile_id)
        self._refresh_models()

    def command_discover_models(self, name, p):
        profile_id = p["id"]
        allow_remote = bool(self.ports.settings().cloud_text_enabled)
        from tingmu.providers import loopback_url

        profile = next((item for item in self.state.profiles if item["id"] == profile_id), {})
        if not allow_remote and not loopback_url(profile.get("base_url")):
            raise ValueError("当前仅允许查询本机 API。可直接填写远程模型 ID，或启用远程文本 API。")
        self.state.status = "正在读取提供商模型列表…"
        self.ports.modelsChanged.emit()

        def completed(models):
            self.state.discovered = models
            self.state.status = f"已读取 {len(models)} 个模型；可选择或手动输入模型 ID"
            self._refresh_models()

        self.ports.start_job(
            "models",
            lambda progress, cancelled: self.ports.model_registry().discover_models(
                profile_id, cancelled=cancelled, allow_remote=allow_remote
            ),
            completed,
        )

    def command_refresh_models(self, name, p):
        self._refresh_models()

    def command_select_model(self, name, p):
        role, profile_id = p["role"], p.get("id", "")
        if role not in ("annotation", "review", "companion"):
            raise ValueError("无效的模型用途")
        self.ports.model_registry().assign([role], profile_id)
        self._refresh_models()
