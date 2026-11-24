from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
import json
from unittest.mock import patch

from tingmu.batch import PreparationQueue
from tingmu.global_models import ROLE_FIELDS
from tingmu.models import AppSettings
from tingmu.providers import ModelRegistry
from tingmu.storage import Store
from tingmu.workspaces import WorkspaceCatalog


def _windows(tmp_path):
    catalog = WorkspaceCatalog(tmp_path)
    workspace = catalog.create("Second window", "zh", "en")
    return Store(tmp_path), Store(catalog.directory(workspace))


def test_replaced_window_preferences_do_not_revert_external_roles(tmp_path):
    first, second = _windows(tmp_path)
    registry = first.model_registry
    old = registry.save_profile({"provider": "ollama", "model": "old"})
    new = registry.save_profile({"provider": "ollama", "model": "new"})
    registry.assign(["annotation", "review", "companion"], old["id"])
    stale = second.load_settings()
    registry.assign(["annotation", "review", "companion"], new["id"])
    registry.save_profile({"id": new["id"], "model": "updated"})
    changed = replace(replace(stale, theme="ocean"), teaching_preferences="examples")
    second.save_settings(changed)
    assert changed.annotation_profile_id == changed.review_profile_id == changed.companion_profile_id == new["id"]
    assert changed.model == changed.review_model == changed.companion_model == "updated"
    assert first.load_settings().annotation_profile_id == new["id"]
    assert second.load_settings().teaching_preferences == "examples"
    # Refreshing the replacement must not mutate the source window's baseline.
    assert stale._loaded_roles["annotation_profile_id"] == old["id"]
    assert stale._loaded_roles is not changed._loaded_roles
    second.save_settings(replace(stale, overlay_font_size=28))
    assert first.load_settings().annotation_profile_id == new["id"]


def test_legacy_direct_field_changes_update_only_the_edited_roles(tmp_path):
    first, second = _windows(tmp_path)
    first.save_settings(AppSettings(provider="local", model="legacy-one", companion_model="legacy-chat"))
    stale = second.load_settings()
    first.model_registry.update_roles({"review_model": "external-review", "companion_model": "external-chat"})
    stale.model = "legacy-two"
    second.save_settings(replace(stale, subtitle_offset_ms=250))
    actual = first.load_settings()
    assert (actual.provider, actual.model) == ("local", "legacy-two")
    assert actual.review_model == "external-review"
    assert actual.companion_model == "external-chat"
    # Choosing a default explicitly is still an edit, even after replacement.
    second.save_settings(replace(second.load_settings(), provider=AppSettings().provider))
    assert first.load_settings().provider == AppSettings().provider


def test_settings_wire_format_excludes_runtime_baseline(tmp_path):
    store = Store(tmp_path)
    loaded = store.load_settings()
    public_fields = {field.name for field in fields(AppSettings) if not field.name.startswith("_")}
    assert set(loaded.to_dict()) == public_fields
    assert AppSettings(**loaded.to_dict()).to_dict() == loaded.to_dict()
    store.save_settings(replace(loaded, teaching_preferences="short"))
    saved = json.loads((tmp_path / "settings.json").read_text("utf-8"))
    assert set(saved) == public_fields - ROLE_FIELDS
    assert "_loaded_roles" not in saved
    assert "_loaded_roles" not in (tmp_path / "model-profiles.json").read_text("utf-8")


def test_frozen_queue_keeps_its_roles_and_identity_without_runtime_baseline(tmp_path):
    store = Store(tmp_path)
    registry = store.model_registry
    profile = registry.save_profile({"provider": "ollama", "model": "frozen"})
    registry.assign(["annotation"], profile["id"])
    settings = store.load_settings()
    media = tmp_path / "sample.mp4"
    media.write_bytes(b"isolated queue fixture")
    queue = PreparationQueue(store)
    job = queue.enqueue([media], settings)[0]
    assert job["settings"] == settings.to_dict()
    assert "_loaded_roles" not in job["settings"]
    assert queue.enqueue([media], AppSettings(**settings.to_dict())) == []
    registry.assign(["annotation"], "")
    resumed = PreparationQueue(Store(tmp_path)).snapshot()[0]
    assert resumed["id"] == job["id"]
    assert resumed["settings"] == job["settings"]


def test_cached_store_registry_refreshes_external_assign_edit_and_delete(tmp_path):
    first, second = _windows(tmp_path)
    reader = second.model_registry
    assert reader is second.model_registry
    first.load_settings()
    second.load_settings()
    from tingmu import global_models
    original_read = global_models._read_json
    reads = []

    def record_read(path, fallback):
        if path == reader._path:
            reads.append(path)
        return original_read(path, fallback)

    with patch("tingmu.global_models._read_json", side_effect=record_read), patch(
            "tingmu.global_models._legacy_workspace", side_effect=AssertionError("migration repeated")):
        for _ in range(4):
            second.save_settings(replace(second.load_settings(), theme="forest"))
        assert reads == []
        profile = first.model_registry.save_profile({"provider": "ollama", "model": "one"})
        first.model_registry.assign(["annotation"], profile["id"])
        reads.clear()
        assert second.load_settings().model == "one"
        assert len(reads) == 1
        first.model_registry.save_profile({"id": profile["id"], "model": "two"})
        assert second.load_settings().model == "two"
        first.model_registry.delete_profile(profile["id"])
        assert second.load_settings().annotation_profile_id == ""


def test_cached_registry_copies_cannot_mutate_state_or_lose_other_writers(tmp_path):
    registry = ModelRegistry(tmp_path)
    profile = registry.save_profile({"provider": "ollama", "model": "original"})
    snapshot = registry._state()
    snapshot["profiles"][profile["id"]]["model"] = "not saved"
    assert registry._read()[profile["id"]]["model"] == "original"
    other = ModelRegistry(tmp_path)

    def save(index):
        writer = registry if index % 2 else other
        writer.save_profile({"id": f"profile-{index}", "provider": "ollama", "model": str(index)})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(save, range(12)))
    assert len(registry._read()) == len(other._read()) == 13
