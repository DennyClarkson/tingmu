"""Workspace transitions react to completed UI callbacks and decisions, not ticks."""

from dataclasses import replace
import threading

import pytest
from PySide6.QtCore import QThread, QTimer

from tingmu.app_actions import ActionContext
from tingmu.models import AppSettings, LanguagePair
from tingmu.quick.bridge import Bridge
from tingmu.quick.features import message
from tingmu.quick.workspace_controller import WorkspaceController
from tingmu.storage import Store
from tingmu.workspaces import WorkspaceCatalog


@pytest.fixture
def workspace(qapp, tmp_path, monkeypatch):
    catalog = WorkspaceCatalog(tmp_path / "data")
    bridge = Bridge(Store(catalog.root))
    transitions, releases = [], []
    controller = WorkspaceController(bridge, catalog, "default", False,
                                     lambda *args: transitions.append(args), None)
    bridge.workspaces = controller
    monkeypatch.setattr("tingmu.quick.workspace_controller.model_status", lambda model: "ready")
    target = catalog.create("English", "zh-CN", "en", initial_settings=AppSettings(
        target_language="en", cloud_text_enabled=False, text_models_enabled=False))
    context = ActionContext(bridge._active_session_id, "", LanguagePair(), workspace_id="default")
    def start(key, callback):
        release = threading.Event()
        releases.append(release)
        def work(progress, cancelled):
            assert release.wait(5)
            return None
        bridge._start_job(key, work, callback)
        return release
    yield bridge, controller, target, context, transitions, start
    for release in releases:
        release.set()
    bridge.shutdown()


def pending_reply(bridge):
    reply = message("assistant", "", status="pending")
    bridge._messages = [message("user", "Set up workspace"), reply]
    bridge.conversations.save(bridge._active_session_id, bridge._messages)
    return reply


def request_switch(controller, target, context):
    result = controller.handle_agent_action("use_study_workspace", {"workspace_id": target}, context)
    assert result["deferred"]


def test_switch_waits_for_last_worker_callback_and_durable_origin(workspace, qtbot):
    bridge, controller, target, context, transitions, start = workspace
    reply = pending_reply(bridge)
    callbacks = []
    def chat_complete(result):
        assert QThread.currentThread() == bridge.thread()
        rows = bridge.conversations.load(context.session_id)["messages"]
        rows[-1].update(status="complete", content="Saved origin reply")
        bridge.conversations.save(context.session_id, rows)
        controller.changed.emit()
        assert not transitions
        callbacks.append("chat")
    def models_complete(result):
        controller.changed.emit()
        assert not transitions
        callbacks.append("models")
    chat_release = start("chat", chat_complete)
    model_release = start("models", models_complete)
    request_switch(controller, target, context)
    assert controller._switch_origin == (context.session_id, reply["id"])
    chat_release.set()
    qtbot.waitUntil(lambda: callbacks == ["chat"])
    assert not transitions
    model_release.set()
    qtbot.waitUntil(lambda: bool(transitions))
    assert callbacks == ["chat", "models"]
    assert transitions == [(target, None, False)]
    assert bridge.conversations.load(context.session_id)["messages"][-1]["content"] == "Saved origin reply"
    assert not controller.pending_switch


def test_download_decision_and_completion_wake_switch(workspace, monkeypatch, qtbot):
    bridge, controller, target, context, transitions, _ = workspace
    settings = controller._settings_for(target)
    controller.proposal = {"id": "proposal", "status": "pending", "workspace_id": target,
                           "origin_workspace_id": "default", "origin_session_id": context.session_id,
                           "settings_fingerprint": controller._fingerprint(settings),
                           "model": "small", "device": "cpu"}
    request_switch(controller, target, context)
    qtbot.wait(10)
    assert not transitions
    installed = []
    monkeypatch.setattr("tingmu.quick.workspace_controller.download_model", lambda *args: installed.append(True))
    monkeypatch.setattr("tingmu.quick.workspace_controller.model_inventory", lambda model: {"installed": bool(installed)})
    assert controller.decideProposal("proposal", True)
    qtbot.waitUntil(lambda: bool(transitions))
    assert controller.proposal["status"] == "ready"
    assert controller._settings_for(target).asr_model == "small"
    assert transitions == [(target, None, False)]


def test_text_reuse_decision_wakes_switch(workspace, monkeypatch, qtbot):
    bridge, controller, target, context, transitions, _ = workspace
    monkeypatch.setattr("tingmu.workspace_text._assignments", lambda *args, **kwargs: {
        "companion": {"role": "学习对话", "model": "synthetic", "host": "text.example", "provider": "custom",
                      "local": False, "profile": {"id": "synthetic"}}})
    result = controller.propose_text_models(target, context)
    assert result["requires_human_confirmation"]
    request_switch(controller, target, context)
    qtbot.wait(10)
    assert not transitions
    assert controller.decideTextReuse(controller.text_proposal["id"], True)
    qtbot.waitUntil(lambda: bool(transitions))
    assert controller.text_proposal["status"] == "ready"
    assert controller._settings_for(target).cloud_text_enabled


def test_declined_download_cancels_deferred_switch(workspace, qtbot):
    bridge, controller, target, context, transitions, _ = workspace
    controller.proposal = {"id": "proposal", "status": "pending", "workspace_id": target,
                           "origin_workspace_id": "default", "origin_session_id": context.session_id}
    request_switch(controller, target, context)
    assert controller.decideProposal("proposal", False)
    bridge.workersChanged.emit()
    qtbot.wait(10)
    assert not transitions and not controller.pending_switch
    assert controller.proposal["status"] == "declined"


@pytest.mark.parametrize("status", ["stopped", "error", "pending", "deleted"])
def test_changed_chat_navigation_does_not_drop_origin_failure_guard(workspace, qtbot, status):
    bridge, controller, target, context, transitions, start = workspace
    reply = pending_reply(bridge)
    def completed(result):
        if status == "deleted":
            bridge.conversations.delete(context.session_id)
        else:
            rows = bridge.conversations.load(context.session_id)["messages"]
            rows[-1]["status"] = status
            bridge.conversations.save(context.session_id, rows)
    release = start("chat", completed)
    other = bridge.conversations.create(LanguagePair(), title="Other chat")
    bridge._select_chat(other["id"])
    request_switch(controller, target, context)
    assert controller._switch_origin == (context.session_id, reply["id"])
    release.set()
    qtbot.waitUntil(lambda: not controller.pending_switch)
    assert not transitions
    assert bridge._active_session_id == other["id"]
    assert controller.error


@pytest.mark.parametrize("select_other", [False, True])
def test_language_refresh_runs_once_after_origin_worker(workspace, monkeypatch, qtbot, select_other):
    bridge, controller, _, context, _, start = workspace
    activations = []
    activate = bridge._activate_context_session
    def record_activate():
        activations.append(bridge._active_session_id)
        activate()
    monkeypatch.setattr(bridge, "_activate_context_session", record_activate)
    release = start("chat", lambda result: None)
    controller._save_settings("default", replace(bridge._settings, target_language="en"))
    assert controller._language_refresh_session == context.session_id
    assert not activations
    other = None
    if select_other:
        other = bridge.conversations.create(LanguagePair("zh-CN", "en"), title="Selected chat")
        bridge._select_chat(other["id"])
    release.set()
    qtbot.waitUntil(lambda: not controller._language_refresh_session)
    assert activations == ([] if select_other else [context.session_id])
    if select_other:
        assert bridge._active_session_id == other["id"]
    for _ in range(3):
        bridge.workersChanged.emit()
        controller.changed.emit()
    qtbot.wait(10)
    assert activations == ([] if select_other else [context.session_id])


def test_waiting_workspace_has_no_polling_and_events_coalesce(workspace, monkeypatch, qtbot):
    bridge, controller, target, context, transitions, _ = workspace
    calls = []
    finish = controller._finish_switch
    def record_finish():
        calls.append(True)
        finish()
    monkeypatch.setattr(controller, "_finish_switch", record_finish)
    controller.proposal = {"status": "pending"}
    request_switch(controller, target, context)
    for _ in range(10):
        controller.changed.emit()
        bridge.workersChanged.emit()
    qtbot.waitUntil(lambda: bool(calls))
    assert len(calls) == 1
    qtbot.wait(450)
    assert len(calls) == 1 and not transitions
    assert controller.findChildren(QTimer) == []
    assert not controller._switch_timer.isActive() and not controller._language_timer.isActive()


def test_queued_worker_updates_do_not_mutate_closed_workspace(workspace, qapp):
    bridge, controller, target, _, transitions, _ = workspace
    changes = []
    controller.changed.connect(lambda: changes.append(True))
    controller.proposal = {"status": "downloading", "progress": "original"}
    def emit_updates():
        controller._recommendationReady.emit(target, {"candidates": [{"model": "late"}]}, "fingerprint")
        bridge._progress.emit("model-download", "late progress")
    worker = threading.Thread(target=emit_updates)
    worker.start()
    worker.join(5)
    assert not worker.is_alive()
    # Both deliveries are queued for this Qt thread. Close before draining them.
    bridge.shutdown()
    qapp.processEvents()
    assert not controller.recommendations
    assert controller.proposal == {"status": "downloading", "progress": "original"}
    assert not changes and not transitions
