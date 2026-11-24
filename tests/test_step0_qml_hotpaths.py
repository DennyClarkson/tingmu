"""Step 0: actual QML hot paths, bounded copying, and lossless deferred drafts."""

from copy import deepcopy
from unittest.mock import patch

import pytest
from PySide6.QtCore import QObject, Property, QMetaObject, Qt, Q_ARG

from tingmu.models import Cue, LanguagePair, StudyDocument
from tingmu.quick.bridge import Bridge
from tingmu.quick.list_models import MessageMarkdownCache, StableListModel
from tingmu.quick.main import create_qml_app


def invoke(item, name, *args):
    assert QMetaObject.invokeMethod(item, name, Qt.ConnectionType.DirectConnection,
                                    *[Q_ARG("QVariant", arg) for arg in args])


class ReadCountingBridge(Bridge):
    full_messages_reads = 0
    full_jobs_reads = 0

    @Property("QVariantList", notify=Bridge.messagesChanged)
    def messages(self):
        self.full_messages_reads += 1
        return super().messages

    @Property("QVariantList", notify=Bridge.jobsChanged)
    def jobs(self):
        self.full_jobs_reads += 1
        return super().jobs


@pytest.fixture
def quick(qapp, qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr("tingmu.quick.main.Bridge", ReadCountingBridge)
    app, bridge, engine = create_qml_app(tmp_path / "data")
    window = engine.rootObjects()[0]
    bridge.timer.stop()
    bridge.inbox_timer.stop()
    qtbot.wait(40)
    yield bridge, engine, window
    window.setProperty("closingSession", True)
    window.close()
    bridge.shutdown()
    for child in app.topLevelWindows():
        child.hide()
    engine.deleteLater()
    qtbot.wait(20)


def prepare_cues(bridge, tmp_path):
    media = str(tmp_path / "synthetic.mkv")
    bridge.media_path = media
    bridge._set_state(mediaPath=media)
    bridge._set_document(StudyDocument(media, LanguagePair(), [
        Cue("one", 0, 1, "First sentence"), Cue("two", 2, 3, "Second sentence")]))
    bridge._sync_cue(500)
    return media


def test_streaming_copies_only_changed_rows_and_public_snapshots_are_isolated(qapp):
    cache, model = MessageMarkdownCache(), StableListModel()
    rows = [{"id": str(i), "content": "Historical answer " * 100,
             "tool_events": [{"result": {"saved": False}}]} for i in range(150)]
    model.sync(cache.display_view(rows))
    with patch("tingmu.quick.list_models.deepcopy", wraps=deepcopy) as copy:
        for chunk in range(12):
            rows[-1]["content"] = f"Stream chunk {chunk}"
            model.sync(cache.display_view(rows))
        # One owned message snapshot and one changed model row per stream chunk.
        assert copy.call_count == 24
        assert all(call.args[0]["id"] == "149" for call in copy.call_args_list)
    rows[0]["tool_events"][0]["result"]["saved"] = True
    assert model.get(0)["tool_events"][0]["result"]["saved"] is False
    exported = cache.display(rows)
    exported[1]["blocks"].clear()
    exported[1]["tool_events"].clear()
    assert cache.display(rows)[1]["blocks"]
    assert cache.display(rows)[1]["tool_events"]
    one = model.findById("1")
    one["tool_events"].clear()
    assert model.findById("1")["tool_events"]


def test_real_qml_stream_and_playback_never_read_full_compatibility_lists(quick, qtbot, tmp_path):
    bridge, engine, window = quick
    media = prepare_cues(bridge, tmp_path)
    invoke(window, "openModule", "chat")
    invoke(window, "openModule", "queue")
    bridge._messages = [{"id": str(i), "role": "assistant", "content": "History", "status": "complete"}
                        for i in range(80)]
    bridge._messages.append({"id": "live", "role": "assistant", "content": "", "status": "streaming",
                             "phase": "answering"})
    bridge._jobs = [{"id": "job", "name": "synthetic", "path": media, "status": "running", "progress": 0}]
    bridge._set_state(chatBusy=True)
    bridge.messagesChanged.emit()
    bridge.jobsChanged.emit()
    qtbot.wait(30)
    assert bridge.full_messages_reads == bridge.full_jobs_reads == 0
    for chunk in range(10):
        bridge._messages[-1]["content"] = f"Reply {chunk}"
        bridge.messagesChanged.emit()
        bridge._jobs[0]["progress"] = chunk / 10
        bridge.jobsChanged.emit()
        bridge._sync_cue(500 if chunk % 2 else 2500)
        bridge.playbackChanged.emit()
        qtbot.wait(10)
    assert bridge.full_messages_reads == bridge.full_jobs_reads == 0
    assert bridge.chatState["activePhase"] == "answering"
    assert bridge.queueState["runningMediaPaths"] == [media]
    assert engine._tingmu_warnings == []


def test_correction_only_tracks_single_message_while_open(quick, qtbot, tmp_path):
    bridge, engine, window = quick
    prepare_cues(bridge, tmp_path)
    dialog = window.findChild(QObject, "subtitleCorrectionDialog")
    bridge._messages = [{"id": "answer", "role": "assistant", "content": "", "status": "streaming"}]
    bridge.messagesChanged.emit()
    with patch.object(bridge.messageModel, "get", wraps=bridge.messageModel.get) as get:
        invoke(dialog, "begin", "one")
        qtbot.waitUntil(lambda: dialog.property("opened"))
        dialog.setProperty("messageId", "answer")
        assert dialog.property("waiting")
        bridge._messages[0]["status"] = "completed"
        bridge._messages[0]["tool_events"] = [{"name": "revise_current_subtitle", "status": "success",
                                               "result": {"saved": True}}]
        bridge.messagesChanged.emit()
        assert dialog.property("saved")
        assert get.call_count >= 2
        invoke(dialog, "close")
        qtbot.waitUntil(lambda: not dialog.property("visible"))
        assert dialog.property("messageId") == ""
        get.reset_mock()
        bridge._messages[0]["content"] = "Further stream"
        bridge.messagesChanged.emit()
        qtbot.wait(20)
        assert get.call_count == 0
    assert bridge.full_messages_reads == 0
    assert engine._tingmu_warnings == []


def test_model_manager_reparents_main_menu_after_detached_origin_is_hidden(quick, qtbot):
    bridge, engine, window = quick
    invoke(window, "openModule", "chat")
    dock = window.findChild(QObject, "chatModule")
    chat = dock.property("loadedItem")
    bridge.requestDialog("models", chat, {})
    manager = window.findChild(QObject, "modelManagerWindow")
    assert manager.transientParent() == chat.window()
    manager.hide()
    dock.setProperty("detached", False)
    action = window.findChild(QObject, "globalTextModelsAction")
    invoke(action, "trigger")
    qtbot.waitUntil(manager.isVisible)
    assert manager.transientParent() == window
    manager.hide()
    # Workspace setup shares the same routed root entry point.
    invoke(window.findChild(QObject, "workspaceSetup"), "modelsRequested")
    assert manager.transientParent() == window
    assert len(window.findChildren(QObject, "modelManagerWindow")) == 1
    assert engine._tingmu_warnings == []


def test_companion_drafts_debounce_flush_on_switch_collection_and_close(quick, qtbot, tmp_path):
    bridge, engine, window = quick
    media = prepare_cues(bridge, tmp_path)
    editor = window.findChild(QObject, "cueNoteDraftInput")
    first_key = media + "|zh-CN|yue|one"
    second_key = media + "|zh-CN|yue|two"
    with patch.object(bridge.drafts, "write", wraps=bridge.drafts.write) as write:
        for value in ("a", "ab", "abc"):
            editor.setProperty("text", value)
            invoke(editor, "textEdited")
        assert write.call_count == 0
        qtbot.waitUntil(lambda: write.call_count == 1)
        assert bridge.loadDrafts("noteDrafts")[first_key] == "abc"
        editor.setProperty("text", "flush on switch")
        invoke(editor, "textEdited")
        bridge._sync_cue(2500)
        assert bridge.loadDrafts("noteDrafts")[first_key] == "flush on switch"
        editor.setProperty("text", "save collection")
        invoke(editor, "textEdited")
        invoke(window.findChild(QObject, "saveCueNoteButton"), "clicked")
        assert second_key not in bridge.loadDrafts("noteDrafts")
        assert bridge.store.entries(media)[0]["note"] == "save collection"
        editor.setProperty("text", "flush on close")
        invoke(editor, "textEdited")
        window.setProperty("closingSession", True)
        assert window.close()
        assert bridge.loadDrafts("noteDrafts")[second_key] == "flush on close"
    assert engine._tingmu_warnings == []


def test_failed_collection_preserves_note_and_draft(quick, qtbot, tmp_path):
    bridge, _, window = quick
    media = prepare_cues(bridge, tmp_path)
    editor = window.findChild(QObject, "cueNoteDraftInput")
    editor.setProperty("text", "unsaved personal note")
    invoke(editor, "textEdited")
    with patch.object(bridge.store, "save_note", side_effect=OSError("synthetic write failure")):
        invoke(window.findChild(QObject, "saveCueNoteButton"), "clicked")
    assert editor.property("text") == "unsaved personal note"
    assert bridge.loadDrafts("noteDrafts")[media + "|zh-CN|yue|one"] == "unsaved personal note"
    assert bridge.learningState["error"]


def test_failed_draft_flush_keeps_main_open_until_retry_succeeds(quick, tmp_path):
    bridge, _, window = quick
    media = prepare_cues(bridge, tmp_path)
    editor = window.findChild(QObject, "cueNoteDraftInput")
    editor.setProperty("text", "retained during storage failure")
    invoke(editor, "textEdited")
    window.setProperty("closingSession", True)
    with patch.object(bridge.drafts, "write", side_effect=OSError("synthetic draft failure")):
        assert not window.close()
        assert window.isVisible()
        assert editor.property("text") == "retained during storage failure"
    assert window.close()
    assert bridge.loadDrafts("noteDrafts")[media + "|zh-CN|yue|one"] == "retained during storage failure"
