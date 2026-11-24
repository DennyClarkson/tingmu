"""Incremental model signals, parser work, and retained real QML delegates."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from unittest.mock import patch

import pytest
from PySide6.QtCore import QObject, QMetaObject, QPersistentModelIndex, Qt, Q_ARG
from PySide6.QtTest import QAbstractItemModelTester, QSignalSpy

from tingmu.quick.list_models import MessageMarkdownCache, StableListModel
from tingmu.quick.main import create_qml_app
from tingmu.richtext import markdown_blocks


def test_list_model_changes_only_updated_rows_and_keeps_move_identity(qapp):
    model = StableListModel()
    tester = QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Warning)
    reset = QSignalSpy(model.modelReset)
    changed = QSignalSpy(model.dataChanged)
    inserted = QSignalSpy(model.rowsInserted)
    removed = QSignalSpy(model.rowsRemoved)
    moved = QSignalSpy(model.rowsMoved)
    rows = [{"id": name, "content": name, "nested": {"value": 1}} for name in ("a", "b", "c")]
    model.sync(rows)
    assert model.count == 3 and inserted.count() == 3
    retained = QPersistentModelIndex(model.index(1, 0))
    model.sync(deepcopy(rows))
    assert changed.count() == 0 and reset.count() == 0
    rows[1]["content"] = "streamed"
    model.sync(rows)
    assert changed.count() == 1
    assert changed.at(0)[0].row() == changed.at(0)[1].row() == 1
    model.sync([rows[2], rows[0], rows[1]])
    assert moved.count() == 1 and removed.count() == 0
    assert retained.isValid() and retained.row() == 2
    assert retained.data(model.ModelDataRole)["id"] == "b"
    model.sync([rows[1], {"id": "d", "content": "new"}, rows[2]])
    assert [model.get(i)["id"] for i in range(model.count)] == ["b", "d", "c"]
    assert removed.count() == 1 and inserted.count() == 4 and reset.count() == 0
    assert retained.isValid() and retained.row() == 0
    assert tester.model() is model


def test_list_model_owns_snapshots_and_supports_legacy_rows(qapp):
    model = StableListModel()
    row = {"role": "assistant", "content": "old", "tool_events": [{"status": "running"}]}
    model.sync([row])
    row["tool_events"][0]["status"] = "mutated"
    exported = model.get(0)
    exported["tool_events"].clear()
    assert model.get(0)["tool_events"] == [{"status": "running"}]
    changed = QSignalSpy(model.dataChanged)
    reset = QSignalSpy(model.modelReset)
    model.sync([{**row, "content": "updated"}])
    assert changed.count() == 1 and reset.count() == 0
    assert "id" not in model.get(0)
    with pytest.raises(ValueError, match="unique"):
        model.sync([{"id": "same"}, {"id": "same"}])
    assert model.count == 1
    with ThreadPoolExecutor(max_workers=1) as pool:
        with pytest.raises(RuntimeError, match="owning Qt thread"):
            pool.submit(model.sync, []).result()
    assert model.count == 1


def test_markdown_cache_parses_only_changed_content_and_releases_old_revisions():
    cache = MessageMarkdownCache()
    rows = [{"id": str(index), "content": f"## Message {index}\n\n**Text**"} for index in range(60)]
    with patch("tingmu.richtext.markdown_blocks", wraps=markdown_blocks) as parse:
        first = cache.display(rows)
        assert parse.call_count == 60
        first[0]["blocks"].clear()
        rows[-1]["phase"] = "thinking"
        rows[-1]["reasoning_text"] = "public provider reasoning"
        assert cache.display(rows)[0]["blocks"]
        assert parse.call_count == 60
        for index in range(20):
            rows[-1]["content"] = f"Stream revision {index}"
            cache.display(rows)
        assert parse.call_count == 80
        assert len(cache._entries) == 60
        assert "Stream revision 0" not in repr(cache._entries)
        rows = [rows[-1]]
        cache.display(rows)
        assert len(cache._entries) == 1 and parse.call_count == 80
        rows[0]["content"] = '[unsafe](file:///private)\n\n<img src="private">'
        displayed = cache.display(rows)
        assert displayed[0]["blocks"] == markdown_blocks(rows[0]["content"])
        assert "file:///private" not in repr(displayed[0]["blocks"])
        cache.display([])
        assert cache._entries == {}


def _invoke(item, name, *args):
    assert QMetaObject.invokeMethod(item, name, Qt.ConnectionType.DirectConnection,
                                    *[Q_ARG("QVariant", arg) for arg in args])


def _visual(item, name):
    pending = [item]
    while pending:
        candidate = pending.pop()
        if candidate.objectName() == name:
            return candidate
        pending.extend(candidate.childItems())
    return None


@pytest.fixture
def quick(qapp, qtbot, tmp_path):
    app, bridge, engine = create_qml_app(tmp_path / "data")
    window = engine.rootObjects()[0]
    window.setWidth(1280)
    window.setHeight(800)
    bridge.timer.stop()
    bridge.inbox_timer.stop()
    qtbot.wait(40)
    yield bridge, engine, window
    bridge.shutdown()
    for child in app.topLevelWindows():
        child.hide()
    engine.deleteLater()
    qtbot.wait(20)


def test_real_queue_delegate_keeps_expanded_evidence_across_progress(quick, qtbot):
    bridge, engine, window = quick
    _invoke(window, "openModule", "queue")
    panel = window.findChild(QObject, "queueModule").property("loadedItem")
    bridge._jobs = [{"id": "job-one", "name": "Synthetic.mp4", "status": "running",
                     "message": "Teaching", "progress": 0.2, "error": "",
                     "evidence_notes": ["Matched subtitles", "Original timing retained"]}]
    bridge.jobsChanged.emit()
    qtbot.waitUntil(lambda: _visual(panel, "queueJob:job-one") is not None)
    card = _visual(panel, "queueJob:job-one")
    card.setProperty("evidenceOpen", True)
    changed = QSignalSpy(bridge.jobModel.dataChanged)
    reset = QSignalSpy(bridge.jobModel.modelReset)
    bridge._jobs[0].update(progress=0.8, message="Verification")
    bridge.jobsChanged.emit()
    qtbot.wait(50)
    assert _visual(panel, "queueJob:job-one") is card
    assert card.property("evidenceOpen")
    assert _visual(panel, "queueStageProgressBar").property("value") == pytest.approx(0.8)
    assert changed.count() == 1 and reset.count() == 0
    assert engine._tingmu_warnings == []


def test_real_chat_delegate_and_reasoning_scroll_survive_stream_updates(quick, qtbot):
    bridge, engine, window = quick
    _invoke(window, "openModule", "chat")
    panel = window.findChild(QObject, "chatPanel")
    panel.window().setHeight(850)
    panel.setProperty("showSessions", False)
    bridge._messages = [{"id": "answer", "role": "assistant", "status": "streaming",
                         "phase": "answering", "content": "Short answer.",
                         "reasoning_text": "Public reasoning line.\n" * 80,
                         "reasoning_kind": "reasoning", "reasoning_elapsed_ms": 2000}]
    bridge.messagesChanged.emit()
    qtbot.waitUntil(lambda: _visual(panel, "chatMessage:answer") is not None)
    card = _visual(panel, "chatMessage:answer")
    _invoke(panel, "toggleReasoning", "answer")
    panel.setProperty("followTail", False)
    qtbot.wait(50)
    reasoning = _visual(card, "chatReasoning")
    assert reasoning.property("expanded")
    scroll = _visual(card, "chatReasoningScroll")
    flickable = scroll.property("contentItem")
    assert flickable.property("contentHeight") > flickable.property("height")
    flickable.setProperty("contentY", 100.0)
    previous_scroll = flickable.property("contentY")
    changed = QSignalSpy(bridge.messageModel.dataChanged)
    with patch("tingmu.richtext.markdown_blocks", wraps=markdown_blocks) as parse:
        bridge._messages[0]["content"] = "Short answer, with another streamed phrase."
        bridge.messagesChanged.emit()
        qtbot.wait(70)
        assert parse.call_count == 1
    assert _visual(panel, "chatMessage:answer") is card
    assert _visual(card, "chatReasoning") is reasoning
    assert reasoning.property("expanded")
    assert flickable.property("contentY") == pytest.approx(previous_scroll, abs=1)
    assert changed.count() == 1
    assert engine._tingmu_warnings == []
