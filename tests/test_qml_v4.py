"""Exercise the v4 QML controls against an isolated real application bridge."""
import os
import json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QMetaObject, QPointF, Qt, Q_ARG
from PySide6.QtGui import QColor, QFontMetricsF
from PySide6.QtTest import QTest

from tingmu.models import Cue, LanguagePair, StudyDocument, Token
from tingmu.quick.main import create_qml_app


def invoke(item, method, *args):
    assert QMetaObject.invokeMethod(item, method, Qt.ConnectionType.DirectConnection,
                                    *[Q_ARG("QVariant", arg) for arg in args])


def find(item, name):
    found = item.findChild(QObject, name)
    assert found is not None, name
    return found


@pytest.fixture
def quick(qapp, qtbot, tmp_path):
    app, bridge, engine = create_qml_app(tmp_path / "data")
    window = engine.rootObjects()[0]
    window.setWidth(1280)
    window.setHeight(720)
    qtbot.wait(60)
    yield app, bridge, engine, window
    bridge.shutdown()
    for child in app.topLevelWindows():
        child.hide()
    engine.deleteLater()
    qtbot.wait(20)


def test_live_themes_cover_independent_windows_and_learning(quick, qtbot):
    app, bridge, engine, window = quick
    invoke(window, "openModule", "chat")
    chat = find(window, "chatModule").property("loadedItem")
    manager = find(window, "modelManagerWindow")
    invoke(manager, "open")
    for name, bg in [("forest", "#101315"), ("paper", "#f5f1e8"),
                     ("mist", "#e9f0ef"), ("graphite", "#17191d")]:
        bridge.setting("theme", name)
        qtbot.wait(20)
        assert window.color() == QColor(bg)
        assert chat.window().color() == QColor(bg)
        assert manager.color() == QColor(bg)
    invoke(window, "changeView", "learn")
    qtbot.wait(20)
    assert find(window, "learningWorkspace").property("visible")
    assert not find(window, "workspaceSplit").property("visible")
    assert engine._tingmu_warnings == []


def test_notebook_editor_saves_and_review_consumes_real_card(quick, qtbot):
    _, bridge, engine, window = quick
    cue = Cue("one", 1, 2, "我哋去飲茶", tokens=[Token("飲茶", "jam2 caa4", "喝茶")])
    bridge.store.save_word("sample.mp4", cue, cue.tokens[0], LanguagePair())
    invoke(window, "changeView", "learn")
    learning = find(window, "learningWorkspace")
    invoke(learning, "startEdit")
    editor = find(window, "notebookEditor")
    editor.setProperty("text", bridge.notebooks["markdown"] + "\n## 我的例句\n下次一齊去。\n")
    invoke(find(window, "saveNotebookButton"), "clicked")
    qtbot.wait(40)
    assert "下次一齊去。" in bridge.notebooks["markdown"]
    assert not learning.property("dirty")
    invoke(find(window, "practiceNotebookButton"), "clicked")
    assert learning.property("practiceMode")
    assert bridge.notebooks["practice"][0]["text"] == "飲茶"
    bridge.command("reviewEntry", {"id": bridge.notebooks["practice"][0]["id"], "rating": "good"})
    qtbot.wait(20)
    assert bridge.notebooks["practice"] == []
    assert engine._tingmu_warnings == []


def test_model_form_saves_local_profile_before_model_is_known(quick, qtbot):
    _, bridge, engine, window = quick
    manager = find(window, "modelManagerWindow")
    invoke(manager, "open")
    invoke(manager, "populate", {"provider": "ollama", "protocol": "openai",
                               "label": "本地测试", "base_url": "http://127.0.0.1:11434/v1",
                               "model": "", "key_source": "none"})
    save = find(manager, "saveModelButton")
    assert save.property("enabled")
    invoke(save, "clicked")
    qtbot.wait(30)
    profile = next(p for p in bridge.modelProfiles if p["label"] == "本地测试")
    assert profile["model"] == ""
    assert manager.property("profileId") == profile["id"]
    assert find(manager, "modelSecret").property("text") == ""
    assert engine._tingmu_warnings == []


def test_paused_overlay_collects_selected_source_without_resizing(quick, qtbot):
    _, bridge, engine, window = quick
    bridge.media_path = "sample.mp4"
    bridge._set_document(StudyDocument("sample.mp4", LanguagePair(), [Cue("one", 1, 3, "今日我哋去飲茶")]))
    bridge._sync_cue(1500)
    player = find(window, "playerModule").property("loadedItem")
    invoke(player, "toggleFullscreen")
    qtbot.wait(60)
    overlay = find(window, "companionOverlay")
    content = find(overlay, "overlayScroll")
    selected = find(content, "selectableCueText")
    assert QMetaObject.invokeMethod(selected, "select", Qt.ConnectionType.DirectConnection,
                                    Q_ARG("int", 2), Q_ARG("int", 4))
    before = overlay.width(), overlay.height()
    invoke(find(content, "saveSelectionButton"), "clicked")
    qtbot.wait(30)
    assert any(e["text"] == "我哋" for e in bridge.store.entries())
    assert (overlay.width(), overlay.height()) == before
    invoke(find(content, "openLearningButton"), "clicked")
    qtbot.wait(20)
    assert window.property("viewMode") == "learn"
    assert not player.property("fullscreen")
    assert engine._tingmu_warnings == []


def test_chat_new_session_and_stream_update_preserve_reading_position(quick, qtbot):
    app, bridge, engine, window = quick
    invoke(window, "openModule", "chat")
    panel = find(window, "chatPanel")
    prior = bridge.activeSessionId
    invoke(find(panel, "newChatButton"), "clicked")
    assert bridge.activeSessionId != prior
    bridge._messages = [{"id": str(i), "role": "assistant", "status": "complete",
                         "content": "## 语言笔记\n\n句子与表达。\n\n" * 8}
                        for i in range(16)]
    bridge.messagesChanged.emit()
    qtbot.wait(100)
    history = find(panel, "chatHistory")
    assert history.property("contentHeight") > history.property("height") * 2
    panel.setProperty("followTail", False)
    history.setProperty("contentY", 120.0)
    panel.setProperty("readingPosition", 120.0)
    bridge._messages[-1]["content"] += "\n新的流式内容。"
    bridge.messagesChanged.emit()
    qtbot.wait(80)
    assert abs(history.property("contentY") - 120.0) < 1
    assert engine._tingmu_warnings == []


def test_notebook_draft_survives_external_language_change(quick, qtbot):
    _, bridge, engine, window = quick
    invoke(window, "changeView", "learn")
    learning = find(window, "learningWorkspace")
    invoke(learning, "startEdit")
    editor = find(window, "notebookEditor")
    draft = bridge.notebooks["markdown"] + "\n粤语草稿仍在这里。\n"
    editor.setProperty("text", draft)
    bridge.command("selectNotebook", {"language": "en"})
    qtbot.wait(20)
    assert not learning.property("editing")
    assert "粤语草稿" not in bridge.notebooks["markdown"]
    bridge.command("selectNotebook", {"language": "yue"})
    qtbot.wait(20)
    invoke(learning, "startEdit")
    assert editor.property("text") == draft
    assert learning.property("editLanguage") == "yue"
    assert learning.property("dirty")
    assert engine._tingmu_warnings == []


def test_theme_choice_keyboard_and_menu_escape(quick, qtbot):
    _, bridge, engine, window = quick
    button = find(window, "learningViewButton")
    label = find(button, "buttonText")
    assert label.property("width") >= QFontMetricsF(button.property("font")).horizontalAdvance("学习")
    choice = find(window, "themeChoice")
    invoke(choice, "forceActiveFocus")
    QTest.keyClick(window, Qt.Key.Key_Down)
    qtbot.wait(20)
    assert bridge.settings["theme"] == "paper"
    menu = find(window, "fileMenu")
    invoke(menu, "open")
    qtbot.wait(20)
    assert menu.property("opened")
    QTest.keyClick(window, Qt.Key.Key_Escape)
    qtbot.wait(20)
    assert not menu.property("opened")
    assert engine._tingmu_warnings == []


def test_notebook_draft_debounces_closes_and_recovers_after_restart(quick, qtbot):
    app, bridge, engine, window = quick
    invoke(window, "changeView", "learn")
    learning = find(window, "learningWorkspace")
    invoke(learning, "startEdit")
    editor = find(window, "notebookEditor")
    revision = bridge.notebooks["revision"]
    draft = bridge.notebooks["markdown"] + "\n这段长笔记尚未保存。\n"
    editor.setProperty("text", draft)
    qtbot.wait(700)
    layout_path = bridge.store.data_dir / "quick-layout.json"
    persisted = json.loads(layout_path.read_text(encoding="utf-8"))
    assert persisted["preferences"]["notebookDrafts"]["yue"] == {"text": draft, "revision": revision}
    latest = draft + "退出前最后一句也要保留。\n"
    editor.setProperty("text", latest)
    window.close()  # Before the debounce expires: Main.onClosing must flush.
    qtbot.wait(20)
    persisted = json.loads(layout_path.read_text(encoding="utf-8"))
    assert persisted["preferences"]["notebookDrafts"]["yue"]["text"] == latest
    bridge.shutdown()
    _, restored_bridge, restored_engine = create_qml_app(bridge.store.data_dir)
    restored_window = restored_engine.rootObjects()[0]
    try:
        restored_learning = find(restored_window, "learningWorkspace")
        invoke(restored_learning, "startEdit")
        assert find(restored_window, "notebookEditor").property("text") == latest
        assert restored_learning.property("editRevision") == revision
        invoke(find(restored_window, "saveNotebookButton"), "clicked")
        qtbot.wait(700)
        persisted = json.loads(layout_path.read_text(encoding="utf-8"))
        assert "yue" not in persisted["preferences"]["notebookDrafts"]
        assert latest == restored_bridge.notebooks["markdown"]
        assert restored_engine._tingmu_warnings == []
    finally:
        restored_bridge.shutdown()
        for child in app.topLevelWindows():
            child.hide()
        restored_engine.deleteLater()
        qtbot.wait(20)
    assert engine._tingmu_warnings == []


def test_queue_failure_is_readable_and_retry_buttons_dispatch(quick, qtbot, monkeypatch):
    _, bridge, engine, window = quick
    bridge.timer.stop()  # Keep this synthetic presentation snapshot independent of disk jobs.
    invoke(window, "openModule", "queue")
    panel = find(window, "queueModule").property("loadedItem")
    floating = panel.window()
    floating.setWidth(330)
    floating.setHeight(640)
    failure = ("字幕讲解校验未通过：模型改变了已复核正文，请换用能保留原文的讲解模型后重试。\n"
               "已完成的识别、复核与前面讲解步骤均已保存；重试会复用可用检查点。\n"
               "不需要重新识别视频，也不会删除收藏和个人笔记。")
    bridge._jobs = [{"id": "failed-one", "name": "合成测试视频.mp4", "status": "failed",
                     "message": "学习材料阶段未完成，等待修正模型设置后重试", "progress": 0.4,
                     "error": failure}]
    bridge.jobsChanged.emit()
    qtbot.wait(60)
    calls = []
    monkeypatch.setattr(bridge, "_command", lambda name, params: calls.append((name, params)))
    def visual(name):
        pending = [panel]
        while pending:
            item = pending.pop()
            if item.objectName() == name:
                return item
            pending.extend(item.childItems())
        raise AssertionError(name)
    start = find(panel, "queueStartButton")
    retry = visual("queueRetryJobButton")
    error = visual("queueJobError")
    assert start.property("text") == "重试失败"
    assert start.property("enabled")
    assert retry.property("visible") and retry.property("text") == "重试"
    assert error.property("text") == failure
    assert error.property("readOnly") and error.property("selectByMouse")
    assert error.property("lineCount") > 3
    assert error.property("height") >= error.property("contentHeight")
    assert error.property("width") <= panel.width() - 24
    def click(button):
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(floating, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    click(start)
    click(retry)
    assert calls == [("startQueue", {}), ("retryJob", {"id": "failed-one"})]
    bridge._set_state(queueRunning=True)
    qtbot.wait(20)
    assert start.property("text") == "暂停"
    assert not retry.property("enabled")
    assert not visual("queueRemoveJobButton").property("enabled")
    assert not find(panel, "queueClearCompletedButton").property("enabled")
    click(start)
    assert calls[-1] == ("pauseQueue", {})
    bridge._set_state(queueRunning=False)
    bridge._jobs.append({"id": "pending", "name": "等待.mp4", "status": "queued",
                         "message": "等待", "progress": 0, "error": ""})
    bridge.jobsChanged.emit()
    qtbot.wait(20)
    assert start.property("text") == "开始队列"
    bridge._jobs = [{"id": "done", "name": "已完成.mp4", "status": "completed",
                     "message": "已完成", "progress": 1, "error": ""}]
    bridge.jobsChanged.emit()
    qtbot.wait(20)
    assert not start.property("enabled")
    assert engine._tingmu_warnings == []
