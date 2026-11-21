"""Application actions execute through scoped services and the Qt main thread."""
from copy import deepcopy
import json
import threading

import pytest
from PySide6.QtCore import QThread

from tingmu.app_actions import ActionContext, ActionError, AppActionExecutor, action_tools, validate_arguments
from tingmu.models import Cue, LanguagePair, Token
from tingmu.notebooks import NotebookManager
from tingmu.providers import RequestCancelled
from tingmu.quick.bridge import Bridge
from tingmu.storage import Store


@pytest.fixture
def bridge(qapp, tmp_path):
    obj = Bridge(Store(tmp_path / "data"))
    yield obj
    obj.shutdown()


@pytest.fixture
def actions(tmp_path):
    store = Store(tmp_path / "data")
    manager = NotebookManager(store)
    ctx = ActionContext("session", str(tmp_path / "a.mkv"), LanguagePair(target="en"),
                        Cue("source", 1, 2, "Have a nice day", tokens=[Token("nice", "nais", "good")]))
    return AppActionExecutor(store, manager, ctx, None, lambda *a: {"executed": True})


@pytest.mark.parametrize("name,args", [
    ("process_videos", {"scope": "current", "mode": "prepare", "path": "other.mkv"}),
    ("manage_queue", {"action": "retry", "job_ids": ["one", "one"]}),
    ("control_playback", {"action": "seek", "value": True}),
    ("control_playback", {"action": "seek", "value": float("nan")}),
    ("organize_notebook", {"entry_ids": ["1"]}),
    ("set_reasoning_effort", {"effort": "invented"}),
])
def test_strict_action_arguments(name, args):
    schema = next(t["function"]["parameters"] for t in action_tools() if t["function"]["name"] == name)
    with pytest.raises(ActionError):
        validate_arguments(args, schema)


def test_collection_scope_and_revision_conflict(actions):
    actions("collect_current_word", {"text": "nice"})
    rows = actions("list_collections", {})["items"]
    row = rows[0]
    assert row["organized"] is False
    result = actions("update_collection", {"entry_id": row["id"], "note": "compare with 母语", "revision": row["note_revision"]})
    assert result["saved"]
    with pytest.raises(ActionError) as error:
        actions("update_collection", {"entry_id": row["id"], "note": "stale", "revision": row["note_revision"]})
    assert error.value.code == "revision_conflict"
    actions.store.save_note(actions.context.media_path, Cue("other", 3, 4, "別の文"), "", LanguagePair(target="ja"))
    foreign = next(e for e in actions.store.entries() if e["target_language"] == "ja")
    with pytest.raises(ActionError, match="语言"):
        actions("delete_collection", {"entry_id": foreign["id"]})
    assert actions.notebooks.snapshot("en")["collections"][0]["text"] == "nice"


def test_notebook_full_read_revision_and_target_note(actions):
    actions("append_learning_note", {"title": "First", "text": "A" * 350})
    page = actions("read_notebook", {"limit": 100})
    with pytest.raises(ActionError) as error:
        actions("save_notebook", {"markdown": "overwrite", "revision": page["revision"]})
    assert error.value.code == "incomplete_read"
    complete = actions("read_notebook", {})
    actions.notebooks.append_note("en", "concurrent", "new prose")
    with pytest.raises(ActionError) as error:
        actions("update_learning_note", {"note_id": complete["notes"][0]["id"], "title": "changed", "text": "bad overwrite", "revision": complete["revision"]})
    assert error.value.code == "revision_conflict"
    fresh = actions("read_notebook", {})
    actions("update_learning_note", {"note_id": fresh["notes"][0]["id"], "title": "changed", "text": "new note", "revision": fresh["revision"]})
    assert "new prose" in actions.notebooks.snapshot("en")["markdown"]


def test_cancel_before_write_and_delete_backup(actions):
    actions.cancelled = lambda: True
    with pytest.raises(RequestCancelled):
        actions("collect_current_word", {"text": "nice"})
    assert actions.store.entries() == []
    actions.cancelled = None
    actions("collect_current_word", {"text": "nice"})
    entry = actions.store.entries()[0]
    with pytest.raises(ActionError):
        actions("delete_collection", {"entry_id": entry["id"]})
    actions("list_collections", {})
    result = actions("delete_collection", {"entry_id": entry["id"]})
    assert result["backup_created"] and actions.store.entries() == []
    backups = list((actions.store.data_dir / "backups/agent-actions").glob("*.json"))
    assert len(backups) == 1 and json.loads(backups[0].read_text(encoding="utf-8"))["entry"]["text"] == "nice"


def context(bridge, path=""):
    return ActionContext(bridge._active_session_id, path, deepcopy(bridge._settings.language), None, (path,) if path else ())


def test_worker_dispatch_runs_queue_on_main_thread(bridge, tmp_path, qtbot, monkeypatch):
    video = tmp_path / "selected.mkv"
    video.write_bytes(b"sample")
    bound = context(bridge, str(video))
    threads, results = [], []
    original = bridge.queue.enqueue
    def enqueue(*a, **kw):
        threads.append(QThread.currentThread())
        return original(*a, **kw)
    monkeypatch.setattr(bridge.queue, "enqueue", enqueue)
    worker = threading.Thread(target=lambda: results.append(bridge._request_app_action("process_videos", {"scope": "selected", "mode": "prepare", "start": False}, bound, None)))
    worker.start()
    qtbot.waitUntil(lambda: not worker.is_alive(), timeout=5000)
    worker.join()
    assert threads == [bridge.thread()]
    assert len(results[0]["job_ids"]) == 1
    assert not results[0]["processing_completed"]
    assert bridge.queue.snapshot()[0]["status"] == "queued"
    assert str(tmp_path) not in json.dumps(results)


def test_current_media_switch_and_cancel_reject_before_ui_mutation(bridge, tmp_path):
    bridge.media_path = str(tmp_path / "new.mkv")
    bound = context(bridge, str(tmp_path / "old.mkv"))
    with pytest.raises(ActionError) as error:
        bridge._request_app_action("control_playback", {"action": "pause"}, bound, None)
    assert error.value.code == "media_changed"
    before = bridge.settings["teaching_preferences"]
    with pytest.raises(RequestCancelled):
        bridge._request_app_action("set_teaching_preferences", {"preferences": "change"}, bound, lambda: True)
    assert bridge.settings["teaching_preferences"] == before


def test_preferences_persist_and_queue_pause_retry_are_real(bridge, tmp_path, monkeypatch):
    bound = context(bridge)
    result = bridge._request_app_action("set_teaching_preferences", {"preferences": "详细解释并比较母语"}, bound, None)
    assert result["existing_materials_regenerated"] is False
    assert bridge.store.load_settings().teaching_preferences == "详细解释并比较母语"
    path = tmp_path / "queue.mkv"
    path.touch()
    path.with_suffix(".srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest cue\n", encoding="utf-8")
    job = bridge.queue.enqueue([path], bridge._settings)[0]
    bridge.queue._jobs[0]["status"] = "failed"
    bridge._request_app_action("manage_queue", {"action": "retry", "job_ids": [job["id"]]}, bound, None)
    assert bridge.queue.snapshot()[0]["status"] == "queued"
    started = []
    monkeypatch.setattr(bridge, "_start_job", lambda key, *a: started.append(key))
    bridge._request_app_action("manage_queue", {"action": "start"}, bound, None)
    assert started == ["queue"]
    bridge._request_app_action("manage_queue", {"action": "pause"}, bound, None)
    assert bridge.queue._pause.is_set()


def test_chat_reasoning_context_and_model_stay_frozen_on_retry(bridge, tmp_path, monkeypatch, qtbot):
    received = []
    first = str(tmp_path / "first.mkv")
    bridge.media_path = first
    bridge._active_cue = Cue("frozen", 1, 2, "original")
    bridge._settings.companion_reasoning_effort = "high"
    def reply(self, text, history, language, cue, context, **kwargs):
        received.append((self.reasoning_effort, cue.id, kwargs["tool_executor"].context.media_path))
        kwargs["on_tool_event"]({"name": "get_app_context", "label": "读取应用状态", "status": "success", "result": {"ok": True}})
        return "done"
    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", reply)
    bridge._v4_send_chat("question")
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    original_id = bridge._active_session_id
    persisted = bridge.conversations.load(original_id)["messages"]
    assert persisted[0]["reasoning_effort"] == "high" and persisted[-1]["tool_events"][0]["status"] == "success"
    bridge._settings.companion_reasoning_effort = "none"
    bridge.media_path = str(tmp_path / "other.mkv")
    bridge._active_cue = Cue("other", 4, 5, "other")
    bridge._feature_command("retryChat", {})
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    assert received == [("high", "frozen", first)] * 2
    assert bridge.conversations.load(original_id)["messages"] == persisted


def test_deleted_historical_profile_never_substitutes(bridge, monkeypatch, qtbot):
    profile = bridge.model_registry.save_profile({"label": "saved", "provider": "ollama", "protocol": "openai", "base_url": "http://127.0.0.1:11434/v1", "model": "local", "key_source": "none"})
    bridge._refresh_models()
    bridge._feature_command("selectModel", {"role": "companion", "id": profile["id"]})
    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", lambda *a, **kw: "done")
    bridge._v4_send_chat("first")
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    original_id = bridge._active_session_id
    saved = bridge.conversations.load(original_id)["messages"][0]["request_model"]
    assert set(saved) <= {"id", "provider", "model", "protocol", "reasoning_effort", "max_tokens", "fingerprint"}
    bridge.model_registry.delete_profile(profile["id"])
    with pytest.raises(ValueError, match="删除"):
        bridge._feature_command("retryChat", {})
    assert bridge._active_session_id == original_id


def test_explicit_teaching_reset_preserves_review_source_and_backups(bridge, tmp_path):
    from tingmu.models import StudyDocument
    video = tmp_path / "prepared.mkv"
    video.touch()
    bridge.media_path = str(video)
    language = deepcopy(bridge._settings.language)
    cue = Cue("same-id", 1, 3, "reviewed", original_text="raw", translation="translation", explanation="old", segmented_texts=["reviewed"])
    bridge.store.save_document(StudyDocument(str(video), language, [cue], metadata={"review_stage": "ready", "stage": "ready", "completed_ids": [cue.id]}))
    result = bridge._request_app_action("process_videos", {"scope": "current", "mode": "teaching", "start": False}, context(bridge, str(video)), None)
    after = bridge.store.load_document(str(video), language)
    assert result["mode"] == "teaching" and not result["processing_completed"]
    assert (after.cues[0].id, after.cues[0].start, after.cues[0].end, after.cues[0].text, after.cues[0].original_text, after.cues[0].segmented_texts) == ("same-id", 1, 3, "reviewed", "raw", ["reviewed"])
    assert after.cues[0].translation == after.cues[0].explanation == ""
    assert after.metadata["review_stage"] == "ready"
    assert list((bridge.store.data_dir / "material-backups").glob("*/*.json"))


@pytest.mark.parametrize("command", ["retryChat", "editChat"])
def test_first_message_branch_never_rebinds_to_other_media_existing_chat(bridge, tmp_path, monkeypatch, qtbot, command):
    from tingmu.quick.features import message
    a, b = str(tmp_path / "a.mkv"), str(tmp_path / "b.mkv")
    bridge.media_path = a
    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", lambda *a, **kw: "A reply")
    bridge._v4_send_chat("A question")
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    original = bridge._active_session_id
    original_messages = deepcopy(bridge.conversations.load(original)["messages"])
    other = bridge.conversations.create(bridge._settings.language, b)
    bridge.conversations.save(other["id"], [message("user", "KEEP B"), message("assistant", "B answer")])
    untouched = bridge.conversations.load(other["id"])
    bridge.media_path = b
    bridge._select_chat(original)
    bridge._feature_command(command, {"id": original_messages[0]["id"], "text": "edited A"})
    branch = bridge._active_session_id
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    assert branch not in (original, other["id"])
    assert bridge.conversations.load(other["id"]) == untouched
    assert bridge.conversations.load(original)["messages"] == original_messages
    assert bridge.conversations.load(branch)["media_path"] == a
    assert bridge._messages[0]["request_context"]["media_path"] == a


@pytest.mark.parametrize("action", ["update_learning_note", "delete_learning_note"])
def test_partial_notebook_read_cannot_modify_unread_note(actions, action):
    actions.notebooks.append_note("en", "first", "A" * 500)
    actions.notebooks.append_note("en", "unread", "Preserve this unseen body")
    partial = actions("read_notebook", {"limit": 100})
    note = next(n for n in partial["notes"] if n["title"] == "unread")
    args = {"note_id": note["id"], "revision": partial["revision"]}
    if action == "update_learning_note":
        args.update(title="changed", text="replacement")
    before = actions.notebooks.snapshot("en")["markdown"]
    with pytest.raises(ActionError) as error:
        actions(action, args)
    assert error.value.code == "incomplete_read"
    assert actions.notebooks.snapshot("en")["markdown"] == before
    actions("read_notebook", {})
    result = actions(action, args)
    assert result["saved"]


def test_completed_current_prepare_does_not_start_unrelated_failed_job(bridge, tmp_path, monkeypatch):
    paths = [tmp_path / "completed.mkv", tmp_path / "unrequested.mkv"]
    for path in paths:
        path.touch()
    jobs = bridge.queue.enqueue(paths, bridge._settings)
    bridge.queue._jobs[0]["status"] = "completed"
    bridge.queue._jobs[1]["status"] = "failed"
    bridge.media_path = str(paths[0])
    started = []
    monkeypatch.setattr(bridge, "_start_job", lambda *a: started.append(a))
    result = bridge._request_app_action("process_videos", {"scope": "current", "mode": "prepare"}, context(bridge, bridge.media_path), None)
    assert result["processing_completed"] is True and result["job_ids"] == [jobs[0]["id"]]
    assert not started
    assert bridge.queue.snapshot()[1]["status"] == "failed"


def test_prepare_current_runs_only_requested_job_even_with_other_pending(bridge, tmp_path, monkeypatch):
    from tingmu.models import StudyDocument
    paths = [tmp_path / "unrequested.mkv", tmp_path / "requested.mkv"]
    for path in paths:
        path.touch()
    paths[1].with_suffix(".srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nTest cue\n", encoding="utf-8")
    bridge.queue.enqueue(paths, bridge._settings)
    bridge.media_path = str(paths[1])
    calls = []
    def prepare(path, settings, *a, **kw):
        calls.append(path.name)
        return StudyDocument(str(path), settings.language, [Cue("a", 0, 1, "done")])
    monkeypatch.setattr("tingmu.batch.prepare_media", prepare)
    monkeypatch.setattr(bridge, "_start_job", lambda key, work, completed: completed(work(lambda x: None, lambda: False)))
    bridge._request_app_action("process_videos", {"scope": "current", "mode": "prepare"}, context(bridge, bridge.media_path), None)
    assert calls == ["requested.mkv"]
    assert [j["status"] for j in bridge.queue.snapshot()] == ["queued", "completed"]


def test_read_coverage_tracks_only_payload_that_fits_transport_cap(actions, monkeypatch):
    snapshot = {"revision": "observed", "markdown": "\\" * 18000,
                "notes": [{"id": str(i), "title": "title" * 100, "entry_ids": list(range(100))} for i in range(100)]}
    monkeypatch.setattr(actions.notebooks, "snapshot", lambda language: deepcopy(snapshot))
    page = actions("read_notebook", {})
    assert len(json.dumps(page, ensure_ascii=False)) < 32000
    assert page["end"] == len(page["markdown"]) < len(snapshot["markdown"])
    assert page["has_more"] and page["notes_truncated"]
    assert actions._read_ranges == [(0, page["end"])]
    with pytest.raises(ActionError) as error:
        actions("delete_learning_note", {"note_id": "99", "revision": "observed"})
    assert error.value.code == "incomplete_read"


def test_selected_media_limit_rejects_before_submit_and_retry_preserves_boundary(bridge, tmp_path, monkeypatch, qtbot):
    received = []
    def reply(self, *a, **kwargs):
        received.append(kwargs["tool_executor"].context.selected_media)
        return "done"
    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", reply)
    paths = [str(tmp_path / f"video-{i}.mkv") for i in range(101)]
    bridge._agent_selected_media = paths
    before = bridge.conversations.load(bridge._active_session_id)["messages"]
    with pytest.raises(ValueError, match="100"):
        bridge._v4_send_chat("use selected")
    assert bridge.conversations.load(bridge._active_session_id)["messages"] == before and not received
    bridge._agent_selected_media = paths[:100]
    bridge._v4_send_chat("use selected")
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    stored = bridge.conversations.load(bridge._active_session_id)["messages"][0]
    assert stored["request_context"]["selected_media"] == paths[:100]
    bridge._agent_selected_media = []
    bridge._feature_command("retryChat", {})
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    assert received == [tuple(paths[:100])] * 2


def test_explicit_full_regeneration_backs_up_material_and_preserves_personal_data(bridge, tmp_path):
    from tingmu.models import StudyDocument
    video = tmp_path / "full-redo.mkv"
    video.write_bytes(b"original video bytes")
    bridge.media_path = str(video)
    language = deepcopy(bridge._settings.language)
    cue = Cue("source", 1, 3, "source text", translation="old translation")
    bridge.store.save_document(StudyDocument(str(video), language, [cue]))
    bridge.store.save_note(str(video), cue, "personal", language)
    bridge.notebook_manager.append_note(language.target, "Manual", "Keep my writing")
    entries = bridge.store.entries()
    notebook = bridge.notebook_manager.snapshot(language.target)["markdown"]
    result = bridge._request_app_action("process_videos", {"scope": "current", "mode": "all", "start": False}, context(bridge, str(video)), None)
    assert result["mode"] == "all" and not result["processing_completed"]
    assert bridge.store.load_document(str(video), language) is None
    assert video.read_bytes() == b"original video bytes"
    assert bridge.store.entries() == entries
    assert bridge.notebook_manager.snapshot(language.target)["markdown"] == notebook
    assert list((bridge.store.data_dir / "material-backups").glob("*/*.json"))
    assert bridge.queue.snapshot()[0]["force_asr"] is True


@pytest.mark.parametrize("reason", ["timeout", "cancelled", "closed"])
def test_dispatched_action_observes_cancellation_after_entry(bridge, monkeypatch, reason):
    caller_cancelled = threading.Event()
    envelope = {"name": "get_app_context", "args": {}, "context": context(bridge),
                "cancelled": caller_cancelled.is_set, "done": threading.Event(), "abandoned": threading.Event()}
    observed = []
    def perform(name, args, bound, cancelled):
        observed.append(cancelled())
        if reason == "timeout":
            envelope["abandoned"].set()
        elif reason == "cancelled":
            caller_cancelled.set()
        else:
            bridge._closed = True
        observed.append(cancelled())
        if cancelled():
            raise RequestCancelled("stopped")
        pytest.fail("cancelled operation continued")
    monkeypatch.setattr(bridge, "_perform_app_action", perform)
    try:
        bridge._dispatch_app_action(envelope)
    finally:
        bridge._closed = False
    assert observed == [False, True]
    assert envelope["done"].is_set() and isinstance(envelope["error"], RequestCancelled)
    assert "result" not in envelope


def test_timeout_during_enqueue_keeps_completed_step_and_stops_remaining_work(bridge, tmp_path, monkeypatch):
    paths = [tmp_path / "first.mkv", tmp_path / "second.mkv"]
    for path in paths:
        path.touch()
    bound = ActionContext(bridge._active_session_id, "", deepcopy(bridge._settings.language), None, tuple(str(p) for p in paths))
    envelope = {"name": "process_videos", "args": {"scope": "selected", "mode": "prepare"},
                "context": bound, "cancelled": None, "done": threading.Event(), "abandoned": threading.Event()}
    original = bridge.queue.enqueue
    def enqueue(*args, **kwargs):
        result = original(*args, **kwargs)
        envelope["abandoned"].set()
        return result
    started = []
    monkeypatch.setattr(bridge.queue, "enqueue", enqueue)
    monkeypatch.setattr(bridge, "_start_action_jobs", lambda ids: started.extend(ids))
    bridge._dispatch_app_action(envelope)
    assert isinstance(envelope["error"], RequestCancelled)
    assert envelope["done"].is_set()
    assert [j["name"] for j in bridge.queue.snapshot()] == ["first.mkv"]
    assert not started


def test_switch_chat_restores_only_its_pending_tool_status(bridge, monkeypatch, qtbot):
    ready, release = threading.Event(), threading.Event()
    event = {"name": "update_collection", "label": "保存收藏个性化备注", "status": "running"}
    def reply(self, *args, **kwargs):
        kwargs["on_tool_event"](event)
        ready.set()
        release.wait(5)
        return "done"
    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", reply)
    original = bridge._active_session_id
    try:
        bridge._v4_send_chat("update note")
        qtbot.waitUntil(ready.is_set)
        qtbot.waitUntil(lambda: bridge.state.get("chatToolPhase") == "running")
        saved = bridge.conversations.load(original)["messages"]
        bridge._feature_command("newChat", {})
        empty = bridge._active_session_id
        assert empty != original
        assert bridge.state["chatToolStatus"] == "" and bridge.state["chatToolPhase"] == "idle"
        assert bridge.conversations.load(original)["messages"] == saved
        bridge._select_chat(original)
        assert bridge.state["chatToolStatus"] == event["label"]
        assert bridge.state["chatToolPhase"] == "running"
        assert bridge.conversations.load(original)["messages"] == saved
        bridge._select_chat(empty)
        assert bridge.state["chatToolStatus"] == ""
    finally:
        release.set()
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    assert bridge._active_session_id == empty and bridge.state["chatToolStatus"] == ""
    bridge._select_chat(original)
    assert bridge.state["chatToolStatus"] == "" and bridge.state["chatToolPhase"] == "idle"
    assert bridge.conversations.load(original)["messages"][-1]["tool_events"] == [event]


@pytest.mark.parametrize("ending", ["return", "transport_error", "persistence_error"])
def test_chat_stopping_is_immediate_session_scoped_and_cleared_on_every_exit(bridge, monkeypatch, qtbot, ending):
    ready, release = threading.Event(), threading.Event()
    main_thread = threading.current_thread()
    original_persist = bridge._persist_chat
    def persist(*args, **kwargs):
        if ending == "persistence_error" and threading.current_thread() is not main_thread:
            raise OSError("synthetic persistence failure")
        return original_persist(*args, **kwargs)
    def reply(self, *args, **kwargs):
        ready.set()
        release.wait(5)
        if ending == "transport_error":
            raise RuntimeError("synthetic transport failure")
        return "done"
    monkeypatch.setattr(bridge, "_persist_chat", persist)
    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", reply)
    original = bridge._active_session_id
    try:
        bridge._v4_send_chat("wait for connection")
        qtbot.waitUntil(ready.is_set)
        assert bridge.state["chatStopping"] is False
        bridge._feature_command("stopChat", {})
        assert bridge.state["chatStopping"] is True
        assert bridge._messages[-1]["status"] == "pending"
        bridge._feature_command("newChat", {})
        other = bridge._active_session_id
        assert bridge.state["chatStopping"] is False
        bridge._select_chat(original)
        assert bridge.state["chatStopping"] is True
        bridge._select_chat(other)
        assert bridge.state["chatStopping"] is False
    finally:
        release.set()
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    assert bridge.state["chatStopping"] is False
    assert bridge._chat_worker_session_id == ""
    bridge._select_chat(original)
    assert bridge.state["chatStopping"] is False
