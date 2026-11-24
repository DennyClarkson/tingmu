"""Controlled worker handoffs never share mutable UI settings or chat rows."""

from copy import deepcopy
from dataclasses import asdict
import threading

import pytest
from PySide6.QtCore import QThread, Qt

from tingmu.app_actions import ActionContext, ActionError, action_tools
from tingmu.models import Cue, LanguagePair
from tingmu.providers import RequestCancelled
from tingmu.quick.bridge import Bridge
from tingmu.quick.features import message
from tingmu.quick.workspace_controller import WorkspaceController
from tingmu.storage import Store
from tingmu.workspaces import WorkspaceCatalog


@pytest.fixture
def workspace(qapp, tmp_path):
    catalog = WorkspaceCatalog(tmp_path / "data")
    bridge = Bridge(Store(catalog.root))
    controller = WorkspaceController(bridge, catalog, "default", False, lambda *args: None, None)
    bridge.workspaces = controller
    yield bridge, controller
    controller._switch_timer.stop()
    controller._language_timer.stop()
    bridge.shutdown()


def test_recommendation_captures_ui_settings_and_publishes_independent_result(workspace, monkeypatch, qtbot):
    bridge, controller = workspace
    captured, release, finished = threading.Event(), threading.Event(), threading.Event()
    observations, errors = {}, []
    settings_for = controller._settings_for
    def checked_settings(workspace_id):
        assert QThread.currentThread() == controller.thread()
        return settings_for(workspace_id)
    monkeypatch.setattr(controller, "_settings_for", checked_settings)
    before = deepcopy(bridge._settings)
    fingerprint = controller._fingerprint(before)
    def advise(settings, **kwargs):
        observations["settings"] = settings
        observations["kwargs"] = kwargs
        captured.set()
        assert release.wait(5)
        assert settings.target_language == before.target_language
        return {"candidates": [{"model": "small", "available": True}],
                "hardware": {"nested": {"label": "original", "interpreter": "private"}}}
    monkeypatch.setattr("tingmu.model_advisor.recommend_models", advise)
    context = ActionContext(bridge._active_session_id, "", LanguagePair(), workspace_id="default")
    def run():
        try:
            result = bridge._request_app_action("recommend_workspace_model", {"workspace_id": "default"}, context, None)
            # The queued receiver must see the emitted snapshot, not this object.
            result["candidates"][0]["model"] = "mutated"
            result["hardware"]["nested"]["label"] = "mutated"
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()
    worker = threading.Thread(target=run)
    worker.start()
    try:
        qtbot.waitUntil(captured.is_set, timeout=5000)
        bridge._settings.target_language = "ja"
        release.set()
        assert finished.wait(5)
        qtbot.waitUntil(lambda: "default" in controller.recommendations)
        assert not errors
        assert observations["settings"] is not bridge._settings
        assert observations["kwargs"]["language"] == before.target_language
        published = controller.recommendations["default"]
        assert published["candidates"][0]["model"] == "small"
        assert published["hardware"] == {"nested": {"label": "original"}}
        assert published["settings_fingerprint"] == fingerprint
        with pytest.raises(ActionError) as stale:
            controller.handle_agent_action("propose_workspace_model", {"workspace_id": "default", "model": "small"}, context)
        assert stale.value.code == "recommendation_stale"
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()


def test_cancelled_queued_settings_snapshot_never_probes_or_reads_settings(workspace, monkeypatch, qapp):
    bridge, controller = workspace
    requested, cancelled, finished = threading.Event(), threading.Event(), threading.Event()
    errors, probes, reads = [], [], []
    controller._settingsSnapshotRequested.connect(lambda request: requested.set(), Qt.ConnectionType.DirectConnection)
    monkeypatch.setattr(controller, "_settings_for", lambda workspace_id: reads.append(workspace_id))
    monkeypatch.setattr("tingmu.model_advisor.recommend_models", lambda *args, **kwargs: probes.append(args))
    context = ActionContext(bridge._active_session_id, "", LanguagePair(), workspace_id="default")
    def run():
        try:
            controller.recommend({"workspace_id": "default"}, context, cancelled.is_set)
        except Exception as exc:
            errors.append(exc)
        finally:
            finished.set()
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert requested.wait(5)
        cancelled.set()
        assert finished.wait(5)
        qapp.processEvents()
        assert len(errors) == 1 and isinstance(errors[0], RequestCancelled)
        assert not probes and not reads and not controller.recommendations
    finally:
        cancelled.set()
        worker.join(5)
    assert not worker.is_alive()


@pytest.mark.parametrize("branch", ["initial", "retryChat", "editChat"])
def test_chat_worker_owns_history_events_and_frozen_context(workspace, monkeypatch, qapp, branch):
    bridge, controller = workspace
    jobs, observed, saved_refs = [], {}, []
    model = {"provider": "synthetic", "model": "frozen-model", "id": "", "fingerprint": "frozen"}
    monkeypatch.setattr(bridge, "_chat_model_snapshot", lambda previous=None: (None, deepcopy(previous or model)))
    monkeypatch.setattr(bridge, "_tutor_transport", lambda **kwargs: None)
    monkeypatch.setattr("tingmu.providers.text_allowed", lambda *args: True)
    monkeypatch.setattr("tingmu.companion.CompanionAgent", lambda *args, **kwargs: Agent(*args, **kwargs))
    monkeypatch.setattr(bridge, "_start_job", lambda key, work, complete: jobs.append((work, complete)))
    save = bridge.conversations.save
    def record_save(session_id, rows):
        saved_refs.append((rows, deepcopy(rows)))
        return save(session_id, rows)
    monkeypatch.setattr(bridge.conversations, "save", record_save)
    cue = Cue("original-cue", 1, 2, "original words")
    request = {"workspace_id": "default", "media_path": "original-media.mkv",
               "language": asdict(LanguagePair("zh-CN", "en")), "cue": asdict(cue),
               "selected_media": ["selected.mkv"], "correction": {"revision": "frozen-revision"}}
    history = [message("user", "prior question"), message("assistant", "prior answer")]
    source_session = bridge._active_session_id
    if branch == "initial":
        bridge._v4_send_chat("Explain this", history=history, request_context=request, request_model=model,
                             reasoning_effort="high")
    else:
        origin = message("user", "Explain this", request_context=deepcopy(request), request_model=deepcopy(model),
                         reasoning_effort="high")
        bridge._messages = history + [origin, message("assistant", "original answer")]
        bridge._persist_chat(bridge.conversations.load(source_session), bridge._messages)
        bridge._feature_command(branch, {"id": origin["id"], "text": "Edited question"})
    frozen_session = bridge._active_session_id
    source_before = bridge.conversations.load(source_session)
    class Agent:
        last_response_metadata = {}
        def __init__(self, provider, name, **kwargs):
            observed["model"] = (provider, name, kwargs["reasoning_effort"])
        def reply(self, text, prior, language, active_cue, context, **callbacks):
            observed.update(text=text, history=deepcopy(prior), language=deepcopy(language), cue=deepcopy(active_cue),
                            action_context=deepcopy(callbacks["tool_executor"].context))
            # A transport mutating its history must not rewrite persisted history.
            prior[0]["content"] = "transport mutation"
            callbacks["on_reasoning"]({"kind": "summary", "text": "public summary"})
            callbacks["on_delta"]("partial ")
            event = {"name": "get_app_context", "status": "completed", "result": {"value": "original"}}
            callbacks["on_tool_event"](event)
            event["result"]["value"] = "transport mutation"
            callbacks["on_delta"]("answer")
            return "final answer"
    # Change both original inputs and UI state after capture but before execution.
    history[0]["content"] = "caller mutation"
    request["correction"]["revision"] = "caller mutation"
    request["language"]["target"] = "ja"
    model["model"] = "caller mutation"
    bridge._messages[0]["content"] = "UI mutation"
    bridge._settings.target_language = "ja"
    bridge._settings.companion_model = "new-model"
    bridge._active_cue = Cue("new-cue", 9, 10, "new words")
    controller.workspace_id = "new-workspace"
    results, failures = [], []
    def run():
        try:
            results.append(jobs[0][0](lambda *args: None, lambda: False))
        except Exception as exc:
            failures.append(exc)
    worker = threading.Thread(target=run)
    worker.start()
    worker.join(5)
    assert not worker.is_alive() and not failures
    qapp.processEvents()
    displayed = bridge._messages[-1]
    assert displayed["tool_events"][0]["result"] == {"value": "original"}
    jobs[0][1](results[0])
    assert observed["history"][0]["content"] == "prior question"
    assert observed["model"] == ("synthetic", "frozen-model", "high")
    assert observed["language"].target == "en" and observed["cue"].id == "original-cue"
    action_context = observed["action_context"]
    assert action_context.workspace_id == "default" and action_context.session_id == frozen_session
    assert action_context.media_path == "original-media.mkv"
    assert action_context.selected_media == ("selected.mkv",)
    assert action_context.correction["revision"] == "frozen-revision"
    saved = bridge.conversations.load(frozen_session)["messages"]
    assert saved[0]["content"] == "prior question"
    assert saved[-1]["content"] == "final answer" and saved[-1]["status"] == "complete"
    assert saved[-1]["request_context"]["language"]["target"] == "en"
    assert saved[-1]["request_model"]["model"] == "frozen-model"
    assert all(rows == original for rows, original in saved_refs)
    results[0][-1]["content"] = "result mutation"
    assert bridge._messages[-1]["content"] == "final answer"
    if branch != "initial":
        assert frozen_session != source_session
        assert bridge.conversations.load(source_session) == source_before


def test_human_decisions_remain_absent_from_model_tools():
    assert not {"decideProposal", "decideTextReuse", "decide_text_reuse", "approve_download"} & {
        tool["function"]["name"] for tool in action_tools()}


def test_model_discovery_freezes_permission_before_worker_runs(workspace, monkeypatch):
    bridge, _ = workspace
    jobs, calls, failures = [], [], []
    bridge._settings.cloud_text_enabled = False
    bridge._model_profiles = [{"id": "local", "base_url": "http://127.0.0.1:1234/v1"}]
    monkeypatch.setattr(bridge, "_start_job", lambda key, work, complete: jobs.append(work))
    def discover(ident, *, cancelled, allow_remote):
        assert QThread.currentThread() != bridge.thread()
        calls.append((ident, allow_remote))
        return []
    monkeypatch.setattr(bridge.model_registry, "discover_models", discover)
    bridge._feature_command("discoverModels", {"id": "local"})
    # Broadening permission after submission must not broaden this request.
    bridge._settings.cloud_text_enabled = True
    def run():
        try:
            jobs[0](lambda *args: None, lambda: False)
        except Exception as exc:
            failures.append(exc)
    worker = threading.Thread(target=run)
    worker.start()
    worker.join(5)
    assert not worker.is_alive() and not failures
    assert calls == [("local", False)]


def test_notebook_worker_owns_selected_entry_ids(workspace, monkeypatch):
    bridge, _ = workspace
    jobs, calls, failures = [], [], []
    agent = object()
    monkeypatch.setattr(bridge.model_registry, "text_role", lambda *args: (True, None))
    monkeypatch.setattr(bridge, "_tutor_transport", lambda **kwargs: agent)
    monkeypatch.setattr(bridge, "_start_job", lambda key, work, complete: jobs.append(work))
    def organize(target, tutor, *, source_language, cancelled, entry_ids, instructions):
        assert QThread.currentThread() != bridge.thread()
        # Exercise the real notebook input validator: its service API takes lists.
        selected = bridge.notebook_manager._selected([{"id": 1}, {"id": 2}], entry_ids)
        calls.append((target, tutor, source_language, selected, instructions))
        entry_ids.append(3)
        return {}
    monkeypatch.setattr(bridge.notebook_manager, "organize", organize)
    selected_ids = [1, 2]
    original_target, original_source = bridge._notebook_language, bridge._settings.source_language
    bridge._feature_command("organizeNotebook", {"ids": selected_ids, "instructions": "Keep sources"})
    selected_ids[:] = [9]
    bridge._notebook_language = "ja"
    bridge._settings.source_language = "en"
    def run():
        try:
            jobs[0](lambda *args: None, lambda: False)
        except Exception as exc:
            failures.append(exc)
    worker = threading.Thread(target=run)
    worker.start()
    worker.join(5)
    assert not worker.is_alive() and not failures
    assert calls == [(original_target, agent, original_source, [{"id": 1}, {"id": 2}], "Keep sources")]
    assert selected_ids == [9]
