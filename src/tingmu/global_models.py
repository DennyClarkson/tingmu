"""Installation-wide text connections and task assignments for managed study folders."""

import json
from pathlib import Path
import re
import uuid
import time

from .models import AppSettings
from .portable_paths import _migration_lock
from .storage import atomic_json

ROLES = {
    "annotation": ("annotation_profile_id", "provider", "model"),
    "review": ("review_profile_id", "review_provider", "review_model"),
    "companion": ("companion_profile_id", "companion_provider", "companion_model"),
}
ROLE_FIELDS = frozenset(field for names in ROLES.values() for field in names)
PROFILE_FIELDS = {"id", "label", "provider", "protocol", "base_url", "model", "env_var", "key_source",
                  "models", "reasoning_effort", "max_tokens", "_protected_key"}


def workspace_preferences(settings):
    from dataclasses import asdict
    return {k: v for k, v in asdict(settings).items() if k not in ROLE_FIELDS}


def _read_json(path, fallback):
    if not path.is_file():
        return fallback
    return json.loads(path.read_text("utf-8"))


def _profile_dict(value):
    profiles = value.get("profiles", {})
    if not isinstance(profiles, dict):
        raise ValueError("模型连接文件格式无效")
    return {ident: {k: v for k, v in row.items() if k in PROFILE_FIELDS}
            for ident, row in profiles.items()}


def _legacy_workspace(path, workspace_id, profiles):
    for name in ("model-profiles.json", "settings.json"):
        if (path / name).resolve() != path / name:
            raise ValueError("模型设置文件不能指向其他目录")
    settings = _read_json(path / "settings.json", {})
    if not isinstance(settings, dict):
        settings = {}
    legacy = {k: v for k, v in settings.items() if k in ROLE_FIELDS and isinstance(v, str)}
    old = profiles.copy() if workspace_id == "default" else _profile_dict(_read_json(path / "model-profiles.json", {}))
    aliases = {}
    for old_id, profile in old.items():
        key = old_id
        while key in profiles and profiles[key] != {**profile, "id": key}:
            key = uuid.uuid5(uuid.NAMESPACE_URL, workspace_id + "/" + key).hex
        profiles[key] = {**profile, "id": key}
        if key != old_id:
            aliases[old_id] = key
    for key, _, _ in ROLES.values():
        ident = legacy.get(key)
        if ident:
            legacy[key] = aliases.get(ident, ident) if ident in old else "missing:" + ident
    return {"aliases": aliases, "known_ids": list(old), "roles": legacy}


class GlobalModelStore:
    def __init__(self, directory):
        directory = Path(directory).resolve()
        self.root = directory
        self.workspace = "default"
        if directory.parent.name == "workspaces" and (directory / "tingmu-workspace.json").is_file():
            self.root = directory.parent.parent
            self.workspace = _read_json(directory / "tingmu-workspace.json", {})["id"]
        self.root.mkdir(parents=True, exist_ok=True)
        self._path = self.root / "model-profiles.json"
        state = self._state()
        if state.get("version") != 2 or self.workspace not in state.get("imports", {}):
            with self._transaction():
                self._migrate(directory)

    def _transaction(self):
        return _migration_lock(self.root, ".model-registry.lock")

    def _state(self):
        from .providers import ProviderError
        try:
            if self._path.resolve() != self._path:
                raise ValueError
            state = _read_json(self._path, {"version": 1, "profiles": {}})
            if (not isinstance(state, dict) or state.get("version", 1) not in (1, 2)
                    or not isinstance(state.get("profiles"), dict)):
                raise ValueError
            return state
        except (ValueError, OSError):
            raise ProviderError("模型配置无法读取，原文件已保留。") from None

    def _migrate(self, directory):
        from .providers import ProviderError
        state = self._state()
        initial = state.get("version") != 2
        imports = state.setdefault("imports", {})
        if not initial and self.workspace in imports:
            return
        try:
            profiles = state["profiles"] = _profile_dict(state)
            roles = state.setdefault("roles", {})
            defaults = AppSettings()
            catalog = _read_json(self.root / "workspaces.json", {})
            locations = {"default": self.root}
            for item in catalog.get("items", []):
                ident = item["id"]
                if ident == "default" or not re.fullmatch("[0-9a-f]{32}", ident):
                    continue
                path = self.root / item["path"]
                if (path.parent == self.root / "workspaces" and path.resolve() == path
                        and _read_json(path / "tingmu-workspace.json", {}).get("id") == ident):
                    locations[ident] = path
            locations[self.workspace] = directory
            active = catalog.get("active", "default")
            # Keep root IDs stable. Select explicit legacy roles from the last active
            # workspace first, followed by the default workspace and catalog order.
            for ident, path in locations.items():
                if ident in imports:
                    continue
                imports[ident] = _legacy_workspace(path, ident, profiles)
            for ident in dict.fromkeys([active, *locations]):
                legacy = imports.get(ident, {}).get("roles", {})
                for keys in ROLES.values():
                    if any(key in roles for key in keys):
                        continue
                    values = {k: legacy.get(k, getattr(defaults, k)) for k in keys}
                    if any(v != getattr(defaults, k) for k, v in values.items()):
                        roles.update(values)
            state["version"] = 2
            self._save_state(state)
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            raise ProviderError("无法合并旧工作区的模型设置，原文件已保留。") from None

    def _save_state(self, state):
        # Windows indexers can briefly hold the destination during replacement.
        for attempt in range(6):
            try:
                atomic_json(self._path, state)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(.02 * (attempt + 1))

    def _read(self):
        return self._state()["profiles"]

    def _write(self, profiles):
        state = self._state()
        state["profiles"] = profiles
        self._save_state(state)

    def legacy_profile_ids(self):
        origin = self._state()["imports"][self.workspace]
        return {ident: origin["aliases"].get(ident, ident) for ident in origin["known_ids"]}

    def load_roles(self, settings, *, state=None):
        state = self._state() if state is None else state
        defaults = AppSettings()
        for ident, provider, model in ROLES.values():
            for key in (ident, provider, model):
                setattr(settings, key, state["roles"].get(key, getattr(defaults, key)))
            profile = state["profiles"].get(getattr(settings, ident))
            if profile:
                setattr(settings, provider, profile["provider"])
                setattr(settings, model, profile["model"])
        settings._loaded_roles = {k: getattr(settings, k) for k in ROLE_FIELDS}
        return settings

    def save_role_changes(self, settings):
        # A workspace save must not replace another window's newer role selections.
        defaults = AppSettings()
        baseline = getattr(settings, "_loaded_roles", {})
        changes = {k: getattr(settings, k) for k in ROLE_FIELDS
                   if getattr(settings, k) != baseline.get(k, getattr(defaults, k))}
        if changes:
            with self._transaction():
                state = self._state()
                state["roles"].update(changes)
                self._save_state(state)
        self.load_roles(settings)

    def assign(self, roles, profile_id):
        from .providers import ProviderError
        defaults = AppSettings()
        with self._transaction():
            state = self._state()
            profile = state["profiles"].get(profile_id)
            if profile_id and not profile:
                raise ProviderError("请先保存模型连接")
            if profile and not profile.get("model", "").strip():
                raise ProviderError("请先为连接选择或填写模型 ID 并保存，再分配用途。")
            for role in roles:
                ident, provider, model = ROLES[role]
                state["roles"].update({ident: profile_id, provider: profile["provider"] if profile else getattr(defaults, provider),
                                       model: profile["model"] if profile else getattr(defaults, model)})
            self._save_state(state)

    def delete_profile(self, ident):
        with self._transaction():
            state = self._state()
            state["profiles"].pop(ident, None)
            for keys in ROLES.values():
                if state["roles"].get(keys[0]) == ident:
                    for key in keys:
                        state["roles"][key] = getattr(AppSettings(), key)
            self._save_state(state)
