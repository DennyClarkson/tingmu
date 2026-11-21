"""Reuse the assigned text connections without exposing protected credentials to the Agent."""

from copy import deepcopy
import hashlib
import json
from urllib.parse import urlsplit
import uuid

from .agent import provider_status
from .providers import ModelRegistry, loopback_url


_ROLES = {"annotation": ("provider", "model", "逐句讲解"), "review": ("review_provider", "review_model", "字幕复核"),
          "companion": ("companion_provider", "companion_model", "学习对话")}


def _assignments(directory, settings):
    registry = ModelRegistry(directory)
    with registry._lock:
        profiles = registry._read()
    defaults = provider_status()
    assignments = {}
    for role, (provider_field, model_field, label) in _ROLES.items():
        if not settings.text_models_enabled or (role == "review" and not settings.review_enabled):
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
            if provider == "local" or not config.get("available") or not model:
                continue
            local, host = False, config.get("endpoint_host", provider)
        if local or settings.cloud_text_enabled:
            assignments[role] = {"role": label, "model": model, "host": host, "provider": provider,
                                 "local": local, "profile": profile}
    return assignments


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True).encode()).hexdigest()


def _public(rows):
    return [{k: v for k, v in row.items() if k != "profile"} for row in rows.values()]


def text_reuse_offer(directory, settings):
    rows = _assignments(directory, settings)
    return {"signature": _digest(rows) if rows else "", "roles": _public(rows)}


def clone_active_text_roles(origin_dir, dest_dir, origin_settings, target_settings, *, allow_remote=False,
                            expected_source=None):
    rows = _assignments(origin_dir, origin_settings)
    if expected_source and _digest(rows) != expected_source:
        raise ValueError("原工作区的模型连接已变更，请重新选择。")
    rows = {k: v for k, v in rows.items() if allow_remote or v["local"]}
    if not rows:
        return []
    registry = ModelRegistry(dest_dir)
    with registry._lock:
        profiles = registry._read()
        for role, row in rows.items():
            profile = row["profile"]
            ident = ""
            if profile:
                ident = profile["id"]
                # Keep unrelated or independently edited destination connections intact.
                if ident in profiles and profiles[ident] != profile:
                    ident = uuid.uuid5(uuid.NAMESPACE_URL, _digest(profile)).hex
                    if ident in profiles and profiles[ident] != {**profile, "id": ident}:
                        ident = uuid.uuid4().hex
                profiles[ident] = {**deepcopy(profile), "id": ident}
            setattr(target_settings, role + "_profile_id", ident)
            provider_field, model_field, _ = _ROLES[role]
            setattr(target_settings, provider_field, row["provider"])
            setattr(target_settings, model_field, row["model"])
        if any(row["profile"] for row in rows.values()):
            registry._write(profiles)
    target_settings.text_models_enabled = True
    if allow_remote and any(not row["local"] for row in rows.values()):
        # Enabling remote text also enables unassigned legacy defaults. Only activate
        # the roles shown in this consent; a missing role can be configured later.
        if not target_settings.cloud_text_enabled:
            for role, (provider_field, _, _) in _ROLES.items():
                assigned_id = getattr(target_settings, role + "_profile_id")
                if role not in rows and not loopback_url(profiles.get(assigned_id, {}).get("base_url")):
                    setattr(target_settings, role + "_profile_id", "")
                    setattr(target_settings, provider_field, "local")
        target_settings.cloud_text_enabled = True
    target_settings.review_enabled = "review" in rows
    return _public(rows)


class TextSetupMixin:
    def _text_setup_init(self):
        self.text_proposal = {}
        self._text_bindings = ()

    def _text_setup_pending(self):
        return self.text_proposal.get("status") == "pending"

    def propose_text_models(self, workspace_id, context):
        if workspace_id == self.workspace_id:
            return {"applied": True, "roles": _public(_assignments(self.bridge.store.data_dir, self.bridge._settings))}
        destination = self._store(workspace_id)
        source = self.bridge._settings
        target = self._settings_for(workspace_id)
        rows = _assignments(self.bridge.store.data_dir, source)
        public = _public(rows)
        if not any(not row["local"] for row in rows.values()):
            applied = clone_active_text_roles(self.bridge.store.data_dir, destination.data_dir, source, target)
            if applied:
                self._save_settings(workspace_id, target)
            return {"applied": bool(applied), "roles": applied, "requires_human_confirmation": False}
        self._text_bindings = (_digest(rows), self._target_text_signature(destination, target))
        self.text_proposal = {"id": uuid.uuid4().hex, "status": "pending", "workspace_id": workspace_id,
                              "workspace_name": self._workspace_public(workspace_id)["name"],
                              "origin_session_id": context.session_id, "roles": public}
        self.changed.emit()
        return {"applied": False, "requires_human_confirmation": True, "roles": public}

    @staticmethod
    def _target_text_signature(store, settings):
        keys = ["cloud_text_enabled", "text_models_enabled", "review_enabled"]
        for role, (provider, model, _) in _ROLES.items():
            keys.extend([role + "_profile_id", provider, model])
        registry = ModelRegistry(store.data_dir)
        with registry._lock:
            return _digest([[getattr(settings, key) for key in keys], registry._read()])

    def decide_text_reuse(self, ident, approved):
        p = self.text_proposal
        if p.get("id") != ident or not self._text_setup_pending():
            return False
        try:
            if not approved:
                p["status"] = "declined"
                return True
            if self.bridge._active_session_id != p["origin_session_id"]:
                raise ValueError("请在提出方案的原会话重新确认。")
            dest = self._store(p["workspace_id"])
            settings = self._settings_for(p["workspace_id"])
            expected, previous = self._text_bindings
            if previous != self._target_text_signature(dest, settings):
                raise ValueError("目标工作区的文本连接已变更，请重新选择。")
            clone_active_text_roles(self.bridge.store.data_dir, dest.data_dir, self.bridge._settings, settings,
                                    allow_remote=True, expected_source=expected)
            self._save_settings(p["workspace_id"], settings)
            p["status"] = "ready"
            return True
        except (ValueError, RuntimeError, OSError) as exc:
            p.update(status="failed", error=str(exc))
            return False
        finally:
            self.changed.emit()
