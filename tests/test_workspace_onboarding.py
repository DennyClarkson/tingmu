"""Chat onboarding mutates only typed workspaces; downloads need a bound human click."""
from dataclasses import replace
from copy import deepcopy
import threading

import pytest

from tingmu.app_actions import ActionError, AppActionExecutor, ActionContext, action_tools
from tingmu.models import LanguagePair
from tingmu.quick.bridge import Bridge
from tingmu.quick.workspace_controller import WorkspaceController
from tingmu.storage import Store
from tingmu.workspaces import WorkspaceCatalog


@pytest.fixture
def setup(qapp, tmp_path, monkeypatch):
    catalog = WorkspaceCatalog(tmp_path / "data")
    bridge = Bridge(Store(catalog.root))
    switched = []
    controller = WorkspaceController(bridge, catalog, "default", False,
                                     lambda *args: switched.append(args), None)
    bridge.workspaces = controller
    monkeypatch.setattr("tingmu.quick.workspace_controller.model_status", lambda model: "missing")
    monkeypatch.setattr("tingmu.quick.workspace_controller.model_inventory", lambda model: {"installed": False})
    context = ActionContext(bridge._active_session_id, "", LanguagePair(), workspace_id="default")
    executor = AppActionExecutor(bridge.store, bridge.notebook_manager, context, None, bridge._request_app_action)
    yield bridge, controller, executor, switched
    controller._switch_timer.stop()
    bridge.shutdown()


def create(executor, name="英语听力"):
    return executor("create_study_workspace", {"name": name, "source": "zh-CN", "target": "en"})["workspace"]


def test_workspace_creation_can_reuse_a_displayed_teaching_connection(setup):
    bridge, controller, _, switched = setup
    bridge.command("textModelPolicy", {"mode": "local"})
    bridge.command("saveModel", {"provider": "ollama", "model": "local-teacher"})
    profile = bridge.modelProfiles[0]
    bridge.command("selectModel", {"role": "annotation", "id": profile["id"]})
    offer = controller.textReuseOffer()
    assert [r["model"] for r in offer["roles"]] == ["local-teacher"]
    assert controller.action("create", {"name":"法语学西语", "source":"fr", "target":"es",
                                        "textReuseSignature":offer["signature"]})
    target = controller._store(switched[-1][0])
    saved = target.load_settings()
    assert saved.language.source == "fr" and saved.language.target == "es"
    assert saved.auto_prepare_media and saved.text_models_enabled and not saved.cloud_text_enabled
    assert saved.annotation_profile_id == profile["id"]


def recommend(controller, workspace_id, model="small"):
    settings = controller._settings_for(workspace_id)
    controller._save_recommendation(workspace_id, {"candidates": [{"model": model, "name": "Whisper small",
        "available": True, "installed": False, "device": "cpu", "download_bytes": 123,
        "estimated_download_bytes": 456}], "recommendation": {"model": model}}, controller._fingerprint(settings))


def proposal(controller, executor, workspace_id):
    recommend(controller, workspace_id)
    return executor("propose_workspace_model", {"workspace_id": workspace_id, "model": "small"})["proposal"]


def test_create_and_configure_another_workspace_without_moving_chat(setup):
    bridge, controller, executor, switched = setup
    original_settings = deepcopy(bridge._settings)
    workspace = create(executor)
    assert workspace["target"] == "en" and workspace["auto_prepare"]
    assert workspace["media_folder"].endswith("media")
    assert not controller._settings_for(workspace["id"]).cloud_text_enabled
    executor("configure_study_workspace", {"workspace_id": workspace["id"], "level": "intermediate", "preferences": "短解释"})
    assert controller._settings_for(workspace["id"]).teaching_preferences == "短解释"
    assert bridge._settings == original_settings and not switched
    rows = executor("list_study_workspaces", {})["workspaces"]
    assert {row["id"] for row in rows} == {"default", workspace["id"]}


def test_model_cannot_forge_download_consent(setup):
    bridge, controller, executor, _ = setup
    workspace = create(executor)
    pending = proposal(controller, executor, workspace["id"])
    with pytest.raises(ActionError):
        executor("propose_workspace_model", {"workspace_id": workspace["id"], "model": "small", "approved": True})
    assert not {"approve_download", "download_model", "decideProposal"} & {t["function"]["name"] for t in action_tools()}
    with pytest.raises(ActionError):
        executor("decideProposal", {"id": pending["id"], "approved": True})
    assert not controller.decideProposal("fabricated", True)
    assert "model-download" not in bridge._workers
    assert controller._settings_for(workspace["id"]).asr_model != "small"


def test_consent_is_bound_to_origin_session_and_target_settings(setup):
    bridge, controller, executor, _ = setup
    workspace = create(executor)
    pending = proposal(controller, executor, workspace["id"])
    bridge._active_session_id = "another-session"
    assert not controller.decideProposal(pending["id"], True)
    bridge._active_session_id = pending["origin_session_id"]
    settings = controller._settings_for(workspace["id"])
    settings.target_language = "ja"
    controller._save_settings(workspace["id"], settings)
    assert not controller.decideProposal(pending["id"], True)
    assert controller.proposal["status"] == "stale"


def test_human_consent_downloads_once_then_applies_exact_target(setup, monkeypatch, qtbot):
    bridge, controller, executor, _ = setup
    workspace = create(executor)
    pending = proposal(controller, executor, workspace["id"])
    calls = []
    monkeypatch.setattr("tingmu.quick.workspace_controller.download_model", lambda model, progress, cancel: calls.append(model))
    monkeypatch.setattr("tingmu.quick.workspace_controller.model_inventory", lambda model: {"installed": bool(calls)})
    assert controller.decideProposal(pending["id"], True)
    assert not controller.decideProposal(pending["id"], True)
    qtbot.waitUntil(lambda: controller.proposal["status"] == "ready")
    assert calls == ["small"]
    assert controller._settings_for(workspace["id"]).asr_model == "small"
    assert bridge._settings.asr_model != "small"


def test_download_failure_requires_new_bound_confirmation(setup, monkeypatch, qtbot):
    bridge, controller, executor, _ = setup
    workspace = create(executor)
    first = proposal(controller, executor, workspace["id"])
    def fail(*args):
        raise OSError("offline")
    monkeypatch.setattr("tingmu.quick.workspace_controller.download_model", fail)
    assert controller.decideProposal(first["id"], True)
    qtbot.waitUntil(lambda: controller.proposal["status"] == "failed")
    assert controller._settings_for(workspace["id"]).asr_model != "small"
    retry = proposal(controller, executor, workspace["id"])
    assert retry["id"] != first["id"]
    assert not controller.decideProposal(first["id"], True)
    assert controller.proposal["status"] == "pending"


def test_switch_waits_for_reply_and_human_model_decision(setup):
    bridge, controller, executor, switched = setup
    workspace = create(executor)
    proposal(controller, executor, workspace["id"])
    result = executor("use_study_workspace", {"workspace_id": workspace["id"]})
    assert result["deferred"]
    bridge._workers["chat"] = (None, threading.Event())
    controller._finish_switch()
    assert not switched
    bridge._workers.pop("chat")
    controller._finish_switch()
    assert not switched
    controller.proposal["status"] = "ready"
    controller._finish_switch()
    assert switched == [(workspace["id"], None, False)]


def test_installed_model_reuse_and_cross_workspace_rejection(setup, monkeypatch):
    bridge, controller, executor, _ = setup
    workspace = create(executor)
    recommend(controller, workspace["id"])
    monkeypatch.setattr("tingmu.quick.workspace_controller.model_inventory", lambda model: {"installed": True})
    result = executor("propose_workspace_model", {"workspace_id": workspace["id"], "model": "small"})
    assert result["applied"] and result["reused_installed"]
    assert not controller.proposal
    assert "model-download" not in bridge._workers
    with pytest.raises(ActionError, match="另一个"):
        bridge._perform_app_action("list_study_workspaces", {}, replace(executor.context, workspace_id=workspace["id"]), None)


def test_simple_learning_chat_works_without_text_provider_and_keeps_origin_reply(setup, monkeypatch, qtbot):
    bridge, controller, _, switched = setup
    bridge._settings.text_models_enabled = False
    bridge._settings.cloud_text_enabled = False
    origin_session = bridge._active_session_id
    threads = []
    def advisor(settings, **kwargs):
        threads.append(threading.current_thread().name)
        candidate = {"model": "small", "name": "Whisper small", "available": True,
                     "installed": True, "device": "cpu", "download_bytes": 0, "estimated_download_bytes": 456}
        return {"candidates": [candidate], "recommendation": candidate,
                "hardware": {"cpu_threads": 8, "cache_path": "private-full-path"}}
    monkeypatch.setattr("tingmu.model_advisor.recommend_models", advisor)
    monkeypatch.setattr("tingmu.quick.workspace_controller.model_inventory", lambda model: {"installed": True})
    bridge._v4_send_chat("我要学英语了")
    qtbot.waitUntil(lambda: "chat" not in bridge._workers, timeout=10000)
    qtbot.waitUntil(lambda: bool(switched), timeout=10000)
    saved = bridge.conversations.load(origin_session)["messages"]
    assert saved[-1]["status"] == "complete", saved[-1]
    assert saved[-1]["model"] == "听幕本地向导"
    assert "media" in saved[-1]["content"] and saved[-1]["request_context"]["workspace_id"] == "default"
    assert threads == ["tingmu-chat"]
    assert all("cache_path" not in r["hardware"] for r in controller.recommendations.values())
    assert bridge._settings.target_language == "yue"
    assert controller._settings_for(switched[0][0]).asr_model == "small"


def test_current_language_change_waits_for_reply_and_respects_user_selected_chat(setup):
    bridge, controller, executor, _ = setup
    original = bridge._active_session_id
    bridge._workers["chat"] = (None, threading.Event())
    executor("configure_study_workspace", {"workspace_id": "default", "target": "en"})
    controller._finish_language_change()
    assert bridge._active_session_id == original
    selected = bridge.conversations.create(LanguagePair("zh-CN", "en"))
    bridge._select_chat(selected["id"])
    bridge._workers.pop("chat")
    controller._finish_language_change()
    assert bridge._active_session_id == selected["id"]


def test_remote_model_is_visible_before_target_workspace_consent(setup):
    bridge, controller, executor, _ = setup
    bridge.command("saveModel", {"provider": "custom", "base_url": "https://text.example/v1", "model": "teacher"})
    ident = bridge.modelProfiles[0]["id"]
    bridge.command("selectModel", {"role": "annotation", "id": ident})
    target = create(executor)
    persisted = controller._settings_for(target["id"])
    assert persisted.auto_prepare_media and not persisted.cloud_text_enabled
    assert persisted.annotation_profile_id == ident
    assert bridge.model_registry.text_role(persisted, "annotation") == (False, None)
    assert not (controller.catalog.directory(target["id"]) / "model-profiles.json").exists()
