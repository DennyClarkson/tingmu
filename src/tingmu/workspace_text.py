"""Reuse the assigned text connections without exposing protected credentials to the Agent."""

from dataclasses import dataclass, replace
from typing import Callable
import hashlib
import json
from urllib.parse import urlsplit

from .agent import provider_status
from .providers import ModelRegistry, loopback_url


_ROLES = {"annotation": ("provider", "model", "逐句讲解"), "review": ("review_provider", "review_model", "字幕复核"),
          "companion": ("companion_provider", "companion_model", "学习对话")}


def _assignments(directory, settings, *, include_disabled=False):
    registry = ModelRegistry(directory)
    state = registry._state()
    profiles = state["profiles"]
    settings = registry.load_roles(replace(settings), state=state)
    # An offline workspace with only implicit environment defaults should not
    # surface a remote opt-in during automatic learning setup.
    explicit_remote = any(p and not loopback_url(p["base_url"]) for role in _ROLES
                          if (p := profiles.get(getattr(settings, role + "_profile_id"))))
    defaults = provider_status()
    assignments = {}
    for role, (provider_field, model_field, label) in _ROLES.items():
        if not include_disabled and (not settings.text_models_enabled or (role == "review" and not settings.review_enabled)):
            continue
        ident = getattr(settings, role + "_profile_id")
        profile = profiles.get(ident) if ident else None
        if ident:
            if not profile or not profile.get("model") or (profile["key_source"] == "manual" and not profile.get("_protected_key")):
                continue
            local = loopback_url(profile["base_url"])
            host = urlsplit(profile["base_url"]).hostname
            provider, model = profile["provider"], profile["model"]
        else:
            provider, model = getattr(settings, provider_field), getattr(settings, model_field)
            config = defaults.get(provider, {})
            if not (settings.text_models_enabled and settings.cloud_text_enabled) and not explicit_remote:
                continue
            if provider == "local" or not config.get("available") or not model:
                continue
            local, host = False, config.get("endpoint_host", provider)
        if include_disabled or local or settings.cloud_text_enabled:
            assignments[role] = {"role": label, "model": model, "host": host, "provider": provider,
                                 "local": local, "profile": profile}
    return assignments


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True).encode()).hexdigest()


def _public(rows):
    return [{k: v for k, v in row.items() if k != "profile"} for row in rows.values()]


def effective_text_roles(directory, settings):
    return _public(_assignments(directory, settings))


def text_reuse_offer(directory, settings):
    rows = _assignments(directory, settings, include_disabled=True)
    return {"signature": _digest(rows) if rows else "", "roles": _public(rows)}


def enable_global_text(directory, source_settings, target_settings, *, allow_remote=False, expected_source=None):
    rows = _assignments(directory, source_settings, include_disabled=True)
    if expected_source and _digest(rows) != expected_source:
        raise ValueError("全局文本连接已变更，请重新核对。")
    rows = {k: v for k, v in rows.items() if allow_remote or v["local"]}
    if rows:
        target_settings.text_models_enabled = True
        if any(not row["local"] for row in rows.values()):
            target_settings.cloud_text_enabled = True
        target_settings.review_enabled = "review" in rows
    return _public(rows)


def assignments(directory, settings, *, include_disabled=False):
    """Snapshot assignments through the retained legacy lookup hook."""
    return _assignments(directory, settings, include_disabled=include_disabled)


def public_roles(rows):
    return _public(rows)


def roles_signature(rows):
    return _digest(rows)


def target_text_signature(store, settings):
    keys = ["cloud_text_enabled", "text_models_enabled", "review_enabled"]
    for role, (provider, model, _) in _ROLES.items():
        keys.extend([role + "_profile_id", provider, model])
    registry = ModelRegistry(store.data_dir)
    return _digest([[getattr(settings, key) for key in keys], registry._read()])


@dataclass(frozen=True)
class _LegacyTextSetupPorts:
    """Closed adapter for the historical workspace-controller extension points."""

    bridge: object
    changed: object
    store_for: Callable
    settings_for: Callable
    save_settings: Callable
    workspace_public: Callable


class TextSetupMixin:
    """Deprecated import adapter; new controllers compose TextSetupService.

    Only the former controller hooks are adapted. The service uses public
    settings and chat ports and never reaches into private application state.
    """

    def _text_setup_init(self):
        from .quick.workspace_text import TextSetupService

        ports = _LegacyTextSetupPorts(
            bridge=self.bridge,
            changed=self.changed,
            store_for=lambda workspace_id: self._store(workspace_id),
            settings_for=lambda workspace_id: self._settings_for(workspace_id),
            save_settings=lambda workspace_id, settings: self._save_settings(workspace_id, settings),
            workspace_public=lambda workspace_id: self._workspace_public(workspace_id),
        )
        self._legacy_text_setup = TextSetupService(ports)

    @property
    def text_proposal(self):
        return self._legacy_text_setup.proposal

    @text_proposal.setter
    def text_proposal(self, value):
        self._legacy_text_setup.proposal = value

    @property
    def _text_bindings(self):
        return self._legacy_text_setup.bindings

    @_text_bindings.setter
    def _text_bindings(self, value):
        self._legacy_text_setup.bindings = value

    def _text_setup_pending(self):
        return self._legacy_text_setup.pending()

    def propose_text_models(self, workspace_id, context):
        return self._legacy_text_setup.propose(workspace_id, context)

    def decide_text_reuse(self, ident, approved):
        return self._legacy_text_setup.decide(ident, approved)

    @staticmethod
    def _target_text_signature(store, settings):
        return target_text_signature(store, settings)
