"""Workspace orchestration depends on public services, not Bridge internals."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal

from tingmu.app_actions import ActionError
from tingmu.quick.workspace_actions import WORKSPACE_API_ACTIONS, apply_workspace_action
from tingmu.quick.workspace_controller import WORKSPACE_UI_ACTIONS, WorkspaceController
from tingmu.quick.workspace_text import TextSetupService
from tingmu.storage import Store
from tingmu.workspaces import WorkspaceCatalog


class PublicBridge(QObject):
    workersChanged = Signal()
    settingsChanged = Signal()
    task_progress = Signal(str, object)

    def __init__(self, store):
        super().__init__()
        self.store = store
        self.preferences = store.load_settings()
        self.is_closed = False
        self.running_tasks = set()
        self.calls = []
        self.library = []
        self.chat = SimpleNamespace(
            active_session_id="origin", activate_context_session=lambda: self.calls.append("activate")
        )
        self.learning = SimpleNamespace(
            set_language=lambda value: self.calls.append(("language", value)),
            refresh=lambda: self.calls.append("learning"),
        )
        self.models = SimpleNamespace(refresh=lambda: self.calls.append("models"))

    def settings_snapshot(self):
        return deepcopy(self.preferences)

    def apply_settings_snapshot(self, settings):
        self.preferences = deepcopy(settings)
        self.settingsChanged.emit()

    def task_keys(self):
        return frozenset(self.running_tasks)

    def reload_document(self):
        self.calls.append("document")

    def refresh_library(self):
        self.calls.append("library")

    def resume_media_inbox(self):
        self.calls.append("resume")

    def scan_workspace_media(self):
        self.calls.append("scan")

    def open_media(self, path):
        self.calls.append(("open", path))


@pytest.fixture
def workspace(qapp, tmp_path):
    catalog = WorkspaceCatalog(tmp_path / "data")
    bridge = PublicBridge(Store(catalog.root))
    controller = WorkspaceController(bridge, catalog, "default", False, lambda *args: None, None)
    context = SimpleNamespace(workspace_id="default", session_id="origin")
    return bridge, controller, context


def test_public_settings_ports_defer_chat_activation_and_own_the_snapshot(workspace):
    bridge, controller, context = workspace
    bridge.running_tasks.add("chat")
    result = controller.handle_agent_action(
        "configure_study_workspace",
        {"workspace_id": "default", "target": "en", "preferences": "short explanations"},
        context,
    )
    assert result["saved"]
    assert bridge.preferences.target_language == "en"
    assert bridge.calls == ["document", "library", ("language", "en"), "learning"]
    snapshot = controller.settings_for("default")
    snapshot.target_language = "ja"
    assert bridge.preferences.target_language == "en"
    bridge.running_tasks.clear()
    controller._finish_language_change()
    assert bridge.calls[-1] == "activate"


def test_text_consent_is_composed_and_bound_to_origin_session(workspace, monkeypatch):
    bridge, controller, context = workspace
    assert isinstance(controller.text_setup, TextSetupService)
    assert TextSetupService not in type(controller).__mro__
    monkeypatch.setattr(
        "tingmu.workspace_text._assignments",
        lambda *args, **kwargs: {
            "companion": {
                "role": "学习对话",
                "model": "synthetic",
                "host": "example.invalid",
                "provider": "custom",
                "local": False,
                "profile": {"id": "synthetic"},
            }
        },
    )
    target = controller.catalog.create("Target", "zh-CN", "en")
    controller.refresh()
    offered = controller.propose_text_models(target, context)
    assert offered["requires_human_confirmation"]
    bridge.chat.active_session_id = "different-session"
    assert not controller.decideTextReuse(controller.text_proposal["id"], True)
    assert controller.text_proposal["status"] == "failed"
    assert not controller.settings_for(target).cloud_text_enabled
    bridge.chat.active_session_id = "origin"
    controller.propose_text_models(target, context)
    assert controller.decideTextReuse(controller.text_proposal["id"], True)
    assert controller.settings_for(target).cloud_text_enabled


def test_legacy_text_setup_import_adapts_old_host_and_declines_without_grant(workspace, monkeypatch):
    from tingmu.workspace_text import TextSetupMixin

    bridge, controller, context = workspace
    assert not issubclass(WorkspaceController, TextSetupMixin)
    target = controller.catalog.create("Legacy target", "zh-CN", "en")
    monkeypatch.setattr("tingmu.workspace_text._assignments", lambda *args, **kwargs: {
        "companion": {"role": "学习对话", "model": "synthetic", "host": "example.invalid",
                      "provider": "custom", "local": False, "profile": {"id": "synthetic"}}})
    calls = []

    class LegacyHost(TextSetupMixin):
        def __init__(self):
            self.bridge = bridge
            self.changed = SimpleNamespace(emit=lambda: calls.append("changed"))
            self._text_setup_init()

        def _store(self, ident):
            return controller.store_for(ident)

        def _settings_for(self, ident):
            calls.append("settings")
            return controller.settings_for(ident)

        def _save_settings(self, ident, settings):
            pytest.fail("declining text reuse must not change permissions")

        def _workspace_public(self, ident):
            return controller.workspace_public(ident)

    legacy = LegacyHost()
    assert legacy.text_proposal == {} and legacy._text_bindings == ()
    assert not legacy._text_setup_pending()
    before = controller.settings_for(target).to_dict()
    result = legacy.propose_text_models(target, context)
    assert result["requires_human_confirmation"]
    assert legacy._text_setup_pending() and len(legacy._text_bindings) == 2
    assert legacy._text_bindings[1] == legacy._target_text_signature(
        controller.store_for(target), controller.settings_for(target))
    assert legacy.decide_text_reuse(legacy.text_proposal["id"], False)
    assert legacy.text_proposal["status"] == "declined"
    assert not legacy._text_setup_pending()
    assert controller.settings_for(target).to_dict() == before
    assert calls == ["settings", "changed", "changed"]
    legacy.text_proposal = {"status": "pending"}
    legacy._text_bindings = ("source", "target")
    assert legacy._text_setup_pending() and legacy._text_bindings == ("source", "target")
    assert controller.text_proposal == {}


def test_declared_workspace_service_actions_use_public_media_and_model_ports(workspace):
    bridge, controller, _ = workspace
    bridge.preferences.text_models_enabled = False
    bridge.library = [{"path": "synthetic.mp4", "name": "Synthetic", "status": "ready"}]
    request = {"workspace_id": "default", "action": "list"}
    listing = apply_workspace_action(controller, "workspace_media", request)
    assert listing["count"] == 1
    apply_workspace_action(
        controller, "workspace_media", {**request, "action": "play", "media_id": listing["items"][0]["id"]}
    )
    apply_workspace_action(controller, "workspace_media", {**request, "action": "rescan"})
    assert bridge.calls == [("open", "synthetic.mp4"), "scan"]
    result = apply_workspace_action(
        controller,
        "assign_local_text_model",
        {
            "workspace_id": "default",
            "endpoint": "http://localhost:11434",
            "provider": "ollama",
            "model": "synthetic-local",
            "role": "all",
        },
    )
    assert result["saved"] and not result["connection_tested"]
    assert bridge.calls[-1] == "models"
    assert bridge.preferences.text_models_enabled
    assert not bridge.preferences.cloud_text_enabled


def test_service_registry_rejects_human_decisions_without_settings_or_file_changes(workspace):
    bridge, controller, context = workspace
    before = bridge.preferences.to_dict()
    assert not {"decideProposal", "decideTextReuse", "requestDialog"} & WORKSPACE_API_ACTIONS.keys()
    for name in ("decideProposal", "decideTextReuse", "requestDialog"):
        with pytest.raises(ActionError):
            apply_workspace_action(controller, name, {"workspace_id": "default"})
        with pytest.raises(ActionError):
            controller.handle_agent_action(name, {"workspace_id": "default"}, context)
    assert bridge.preferences.to_dict() == before
    assert not bridge.calls


@pytest.mark.parametrize("name", ["_ui_create", "create_study_workspace", "decideProposal"])
def test_trusted_ui_registry_rejects_arbitrary_controller_methods(workspace, monkeypatch, name):
    from PySide6.QtTest import QSignalSpy

    bridge, controller, _ = workspace
    invoked = []
    monkeypatch.setattr(controller, name, lambda *args: invoked.append(args))
    changed = QSignalSpy(controller.changed)
    assert not controller.action(name, {})
    assert controller.error == "工作区操作无效"
    assert changed.count() == 1
    assert not invoked and not bridge.calls


def test_trusted_workspace_registry_covers_qml_actions_and_is_immutable():
    from pathlib import Path
    import re

    root = Path(__file__).parents[1] / "src" / "tingmu" / "quick" / "qml"
    actions = {name for path in root.glob("*.qml")
               for name in re.findall(r'workspaces\.action\("([^"]+)"', path.read_text("utf-8"))}
    assert actions
    assert actions <= WORKSPACE_UI_ACTIONS.keys()
    assert {"download", "downloadSpeech"} <= WORKSPACE_UI_ACTIONS.keys()
    with pytest.raises(TypeError):
        WORKSPACE_UI_ACTIONS["arbitrary"] = lambda *args: None


def test_session_bootstrap_receives_explicit_summary_without_mutating_origin(qapp, qtbot, tmp_path):
    from tingmu.quick.main import _open_session, create_qml_app

    app, origin, origin_engine = create_qml_app(tmp_path / "catalog")
    opened = [(origin, origin_engine)]
    try:
        catalog = origin.workspaces.catalog
        target = catalog.create("Target", "zh-CN", "en")
        original_session = origin.conversations.load(origin.activeSessionId)
        summary = "Synthetic workspace preparation summary"
        _, destination, destination_engine = _open_session(
            app, catalog, target, False, None, switch_summary=summary
        )
        opened.append((destination, destination_engine))
        saved = destination.conversations.load(destination.activeSessionId)
        assert saved["messages"][-1]["content"] == summary
        assert saved["messages"][-1]["model"] == "听幕本地向导"
        assert origin.conversations.load(origin.activeSessionId) == original_session
        assert not hasattr(origin, "_workspace_switch_summary")
        assert not hasattr(destination, "_workspace_switch_summary")
    finally:
        for bridge, engine in reversed(opened):
            for window in engine.rootObjects():
                window.setProperty("closingSession", True)
                window.close()
            bridge.shutdown()
            engine.deleteLater()
        qtbot.wait(20)
