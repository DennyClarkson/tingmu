"""Notebook persistence must succeed before desktop close, tray exit or hiding."""

from unittest.mock import patch

from PySide6.QtCore import QMetaObject, Qt, Q_ARG, Q_RETURN_ARG

from test_qml_v4 import find, invoke, quick as quick


def flush(item):
    return QMetaObject.invokeMethod(item, "flushDrafts", Qt.DirectConnection, Q_RETURN_ARG("QVariant"))


def edit_notebook(window, text):
    panel = find(window, "learningWorkspace")
    invoke(panel, "startEdit")
    editor = find(window, "notebookEditor")
    editor.setProperty("text", text)
    assert panel.property("editing") and panel.property("dirty")
    return panel, editor


def test_notebook_flush_no_edit_or_language_needs_no_storage(quick):
    _, bridge, _, window = quick
    panel = find(window, "learningWorkspace")
    with patch.object(bridge.drafts, "write") as write, patch.object(bridge.drafts, "delete") as delete:
        assert flush(panel) is True
        panel.setProperty("editing", True)
        panel.setProperty("editLanguage", "")
        assert flush(panel) is True
        write.assert_not_called()
        delete.assert_not_called()


def test_notebook_write_failure_rejects_close_keeps_error_and_stops_timer(quick, qtbot):
    _, bridge, engine, window = quick
    text = "尚未保存的整本笔记"
    panel, editor = edit_notebook(window, text)
    language = panel.property("editLanguage")
    window.setProperty("closingSession", True)
    with patch.object(bridge.drafts, "write", side_effect=OSError("synthetic notebook write failure")) as write:
        assert not window.close()
        assert window.isVisible() and not bridge.is_closed
        assert editor.property("text") == text and panel.property("dirty")
        assert bridge.learningState["error"] == "synthetic notebook write failure"
        assert bridge.notificationState["error"] == bridge.learningState["error"]
        qtbot.wait(650)
        write.assert_called_once()
    assert window.close()
    assert bridge.loadDrafts("notebookDrafts")[language]["text"] == text
    assert engine._tingmu_warnings == []


def test_notebook_draft_removal_failure_rejects_close_and_preserves_saved_draft(quick):
    _, bridge, _, window = quick
    panel, editor = edit_notebook(window, "cached notebook draft")
    language = panel.property("editLanguage")
    assert flush(panel) is True
    before = bridge.loadDrafts("notebookDrafts")
    editor.setProperty("text", bridge.notebooks["markdown"])
    assert not panel.property("dirty")
    window.setProperty("closingSession", True)
    with patch.object(bridge.drafts, "delete", side_effect=OSError("synthetic draft removal failure")):
        assert not window.close()
        assert window.isVisible() and panel.property("editing")
        assert bridge.loadDrafts("notebookDrafts") == before
        assert panel.property("drafts").toVariant()[language] == before[language]
        assert bridge.learningState["error"] == "synthetic draft removal failure"
    assert window.close()
    assert language not in bridge.loadDrafts("notebookDrafts")


def test_tray_exit_rejection_resets_exit_flag_and_never_quits_or_hides(quick):
    app, bridge, _, window = quick
    tray = app._tingmu_tray
    panel, editor = edit_notebook(window, "keep this notebook on tray exit failure")
    with patch.object(app, "quit") as quit_app, patch.object(tray._tray, "hide") as hide_tray:
        with patch.object(bridge.drafts, "write", side_effect=OSError("synthetic tray draft failure")):
            tray.quitApplication()
            assert not tray._exiting
            assert window.isVisible() and not bridge.is_closed
            assert panel.property("dirty") and editor.property("text")
            assert bridge.learningState["error"] == "synthetic tray draft failure"
            quit_app.assert_not_called()
            hide_tray.assert_not_called()
        tray.quitApplication()
        assert tray._exiting
        quit_app.assert_called_once_with()
        hide_tray.assert_called_once_with()
    assert bridge.loadDrafts("notebookDrafts")[panel.property("editLanguage")]["text"] == editor.property("text")


def test_hide_to_tray_menu_preserves_visible_window_when_notebook_flush_fails(quick):
    app, bridge, _, window = quick
    edit_notebook(window, "keep notebook visible on hide failure")
    action = find(window, "hideToTrayAction")
    with patch.object(app._tingmu_tray, "hideToTray") as hide:
        with patch.object(bridge.drafts, "write", side_effect=OSError("synthetic hide draft failure")):
            assert QMetaObject.invokeMethod(action, "triggered", Qt.DirectConnection, Q_ARG("QObject*", None))
            hide.assert_not_called()
            assert window.isVisible()
            assert bridge.learningState["error"] == "synthetic hide draft failure"
        assert QMetaObject.invokeMethod(action, "triggered", Qt.DirectConnection, Q_ARG("QObject*", None))
        hide.assert_called_once_with()
