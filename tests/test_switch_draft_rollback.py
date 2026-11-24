"""Workspace switch commits only after draft flush, session open and selection."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QMetaObject, QObject, Qt
from shiboken6 import isValid

from tingmu.quick import main
from tingmu.drafts import DraftStorage


@pytest.fixture
def session(qapp, qtbot, tmp_path):
    app, bridge, engine = main.create_qml_app(tmp_path / "data")
    controller = bridge.workspaces
    catalog = controller.catalog
    target = catalog.create("Target", "zh-CN", "en")
    controller.switch_summary = "Keep this pending setup summary"
    qtbot.wait(20)
    yield SimpleNamespace(app=app, bridge=bridge, engine=engine, window=engine.rootObjects()[0],
                          controller=controller, catalog=catalog, target=target)
    opened = [(bridge, engine)]
    current = app._tingmu_quick
    if current != (bridge, engine) and isinstance(current[0], main.Bridge):
        opened.append(current)
    for item, scene in reversed(opened):
        if isValid(scene):
            for window in scene.rootObjects():
                window.setProperty("closingSession", True)
                window.close()
        if isValid(item):
            item.shutdown()
        if isValid(scene):
            scene.deleteLater()
    qtbot.wait(20)


def _flush_result(monkeypatch, session, result):
    calls = []

    def invoke(window, name, connection, result_type):
        assert window is session.window
        assert name == "flushAllDrafts"
        assert connection == Qt.ConnectionType.DirectConnection
        calls.append("flush")
        return result

    monkeypatch.setattr(main, "QMetaObject", SimpleNamespace(invokeMethod=invoke))
    return calls


def _assert_origin_retained(session):
    assert session.controller.switch_summary == "Keep this pending setup summary"
    assert session.app._tingmu_quick == (session.bridge, session.engine)
    assert session.catalog.active_id == "default"
    assert session.window.isVisible()
    assert not session.bridge.is_closed


def _edit_notebook(session, text):
    panel = session.window.findChild(QObject, "learningWorkspace")
    editor = session.window.findChild(QObject, "notebookEditor")
    assert panel is not None and editor is not None
    assert QMetaObject.invokeMethod(panel, "startEdit", Qt.ConnectionType.DirectConnection)
    assert editor.setProperty("text", text)
    assert panel.property("editing") and panel.property("dirty")
    assert panel.property("editLanguage")
    return panel, editor


def test_failed_draft_flush_aborts_before_opening_or_selecting(session, monkeypatch):
    text = "切换前尚未保存的学习笔记"
    panel, editor = _edit_notebook(session, text)
    before = DraftStorage(session.bridge.store.db_path).load("notebookDrafts")
    write = Mock(side_effect=OSError("synthetic switch draft write failure"))
    monkeypatch.setattr(session.bridge.drafts, "write", write)
    opened = Mock(side_effect=AssertionError("a failed draft must not create a new session"))
    select = Mock(side_effect=AssertionError("a failed draft must not select a new workspace"))
    monkeypatch.setattr(main, "_open_session", opened)
    monkeypatch.setattr(session.catalog, "select", select)
    with pytest.raises(ValueError, match="草稿未能保存"):
        session.controller.switch(session.target, None, False)
    write.assert_called_once_with("notebookDrafts", panel.property("editLanguage"),
                                  {"text": text, "revision": panel.property("editRevision")})
    assert editor.property("text") == text
    assert panel.property("editing") and panel.property("dirty")
    assert session.bridge.learningState["error"] == "synthetic switch draft write failure"
    assert DraftStorage(session.bridge.store.db_path).load("notebookDrafts") == before
    opened.assert_not_called()
    select.assert_not_called()
    _assert_origin_retained(session)


def test_failed_new_session_preserves_origin_and_pending_summary(session, monkeypatch):
    calls = _flush_result(monkeypatch, session, True)

    def fail_open(*args, **kwargs):
        assert calls == ["flush"]
        assert kwargs["switch_summary"] == session.controller.switch_summary
        raise RuntimeError("synthetic session open failure")

    monkeypatch.setattr(main, "_open_session", fail_open)
    with pytest.raises(RuntimeError, match="session open failure"):
        session.controller.switch(session.target, None, False)
    _assert_origin_retained(session)


def test_failed_catalog_selection_rolls_back_new_session_and_keeps_summary(session, monkeypatch):
    _flush_result(monkeypatch, session, True)
    new_window = Mock()
    new_bridge = SimpleNamespace(shutdown=Mock())
    new_engine = SimpleNamespace(rootObjects=lambda: [new_window], deleteLater=Mock())

    def open_replacement(*args, **kwargs):
        assert kwargs["switch_summary"] == session.controller.switch_summary
        session.app._tingmu_quick = (new_bridge, new_engine)
        return session.app, new_bridge, new_engine

    monkeypatch.setattr(main, "_open_session", open_replacement)
    monkeypatch.setattr(session.catalog, "select", Mock(side_effect=OSError("synthetic catalog failure")))
    with pytest.raises(OSError, match="catalog failure"):
        session.controller.switch(session.target, None, False)
    new_window.setProperty.assert_called_once_with("closingSession", True)
    new_window.close.assert_called_once_with()
    new_bridge.shutdown.assert_called_once_with()
    new_engine.deleteLater.assert_called_once_with()
    _assert_origin_retained(session)


def test_success_flushes_real_qml_and_consumes_summary_after_catalog_commit(session, monkeypatch):
    events = []
    text = "成功切换前写入原工作区的学习笔记"
    panel, _ = _edit_notebook(session, text)
    language = panel.property("editLanguage")
    draft = {"text": text, "revision": panel.property("editRevision")}
    original_drafts = DraftStorage(session.bridge.store.db_path)
    assert language not in original_drafts.load("notebookDrafts")
    original_open, original_select = main._open_session, session.catalog.select

    def flush(window, name, connection, result_type):
        events.append("flush")
        result = QMetaObject.invokeMethod(window, name, connection, result_type)
        assert result is True
        return result

    def open_replacement(*args, **kwargs):
        assert events == ["flush"]
        assert session.controller.switch_summary == "Keep this pending setup summary"
        assert original_drafts.load("notebookDrafts")[language] == draft
        events.append("open")
        return original_open(*args, **kwargs)

    def select(ident):
        assert events == ["flush", "open"]
        assert session.controller.switch_summary == "Keep this pending setup summary"
        events.append("select")
        original_select(ident)

    monkeypatch.setattr(main, "QMetaObject", SimpleNamespace(invokeMethod=flush))
    monkeypatch.setattr(main, "_open_session", open_replacement)
    monkeypatch.setattr(session.catalog, "select", select)
    session.controller.switch(session.target, None, False)
    assert events == ["flush", "open", "select"]
    assert session.controller.switch_summary == ""
    assert session.catalog.active_id == session.target
    assert session.bridge.is_closed
    new_bridge, _ = session.app._tingmu_quick
    assert new_bridge is not session.bridge
    assert new_bridge.messages[-1]["content"] == "Keep this pending setup summary"
