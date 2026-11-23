from dataclasses import replace
import json

from tingmu.batch import PreparationQueue
from tingmu.global_models import ROLE_FIELDS
from tingmu.providers import ModelRegistry
from tingmu.storage import Store, atomic_json
from tingmu.workspaces import WorkspaceCatalog


def test_migrate_conflicting_connections_roles_and_frozen_jobs(tmp_path):
    catalog = WorkspaceCatalog(tmp_path)
    ident = catalog.create("英语", "zh-CN", "en")
    path = catalog.directory(ident)
    catalog.select(ident)
    profile = {"id": "common", "provider": "custom", "label": "A", "model": "root-model",
               "base_url": "https://root.example/v1", "protocol": "openai", "key_source": "none", "env_var": ""}
    named = {**profile, "label": "B", "model": "named-model", "base_url": "https://named.example/v1",
             "key_source": "manual", "_protected_key": "ciphertext", "apiKey": "unwanted-field"}
    atomic_json(tmp_path / "model-profiles.json", {"version": 1, "profiles": {"common": profile}})
    atomic_json(path / "model-profiles.json", {"version": 1, "profiles": {"common": named}})
    atomic_json(tmp_path / "settings.json", {"annotation_profile_id": "common", "review_model": "legacy-review"})
    atomic_json(path / "settings.json", {"annotation_profile_id": "common", "cloud_text_enabled": False})
    atomic_json(path / "preparation-queue.json",
                {"jobs": [{"id": "old-job", "status": "paused", "settings": {"annotation_profile_id": "common"}}]})
    store = Store(path)
    registry = ModelRegistry(path)
    new_id = registry.legacy_profile_ids()["common"]
    assert new_id != "common" and set(registry._read()) == {"common", new_id}
    assert registry._read()[new_id]["_protected_key"] == "ciphertext"
    assert "unwanted-field" not in (tmp_path / "model-profiles.json").read_text("utf-8")
    assert "ciphertext" not in str(registry.public_profiles())
    settings = store.load_settings()
    assert settings.annotation_profile_id == Store(tmp_path).load_settings().annotation_profile_id == new_id
    assert settings.review_model == "legacy-review" and settings.model == "named-model"
    assert registry.text_role(settings, "annotation") == (False, None)
    assert PreparationQueue(store).snapshot()[0]["settings"]["annotation_profile_id"] == new_id
    assert PreparationQueue(store).snapshot()[0]["id"] == "old-job"
    from tingmu.quick.features import FeatureMixin
    chat = FeatureMixin()
    chat.model_registry = registry
    imported = registry.resolve(new_id)
    previous = {"id": "common", "fingerprint": chat._profile_fingerprint({**imported, "id": "common"})}
    replay_profile, _ = chat._chat_model_snapshot(previous)
    assert replay_profile["id"] == new_id and replay_profile["model"] == "named-model"
    root_profile = registry.resolve("common")
    global_request = {"id": "common", "scope": "global", "fingerprint": chat._profile_fingerprint(root_profile)}
    assert chat._chat_model_snapshot(global_request)[0]["model"] == "root-model"
    registry.delete_profile(new_id)
    assert set(ModelRegistry(path)._read()) == {"common"}
    assert Store(path).load_settings().annotation_profile_id == ""


def test_global_role_change_survives_stale_workspace_save_and_profile_edit(tmp_path):
    catalog = WorkspaceCatalog(tmp_path)
    named = Store(catalog.directory(catalog.create("第二", "zh", "en")))
    root = Store(tmp_path)
    registry = ModelRegistry(named.data_dir)
    first = registry.save_profile({"provider": "ollama", "model": "one"})
    second = registry.save_profile({"provider": "ollama", "model": "two"})
    registry.assign(["annotation"], first["id"])
    stale = named.load_settings()
    registry.assign(["annotation"], second["id"])
    registry.save_profile({"id": second["id"], "model": "renamed"})
    named.save_settings(replace(stale, teaching_preferences="more examples"))
    assert root.load_settings().annotation_profile_id == second["id"]
    assert named.load_settings().model == "renamed" and not named.load_settings().cloud_text_enabled
    assert not (named.data_dir / "model-profiles.json").exists()
    assert ROLE_FIELDS.isdisjoint(json.loads((named.data_dir / "settings.json").read_text("utf-8")))
