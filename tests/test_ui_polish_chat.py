"""Chat interactions through the real QML controls and isolated application bridge."""

import pytest
from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest

from tingmu.models import Cue, LanguagePair, StudyDocument, Token
from test_qml_v4 import find, invoke, quick as quick
from test_qml_v5 import click, visual_find


@pytest.fixture
def chat(quick, qtbot):
    app, bridge, engine, main = quick
    invoke(main, "openModule", "chat")
    panel = find(main, "chatModule").property("loadedItem")
    window = panel.window()
    window.setWidth(760)
    window.setHeight(760)
    window.requestActivate()
    qtbot.waitUntil(window.isActive)
    return app, bridge, engine, window, panel


@pytest.mark.parametrize("cancel_with", ["button", "escape"])
def test_cancel_edit_restores_composer_and_next_send_is_new(chat, qtbot, monkeypatch, cancel_with):
    _, bridge, engine, window, panel = chat
    bridge._messages = [{"id": "original", "role": "user", "content": "Original question"}]
    bridge.messagesChanged.emit()
    qtbot.waitUntil(lambda: find(panel, "chatHistory").property("count") == 1)
    qtbot.wait(30)
    composer = find(panel, "chatInput")
    composer.setProperty("text", "Unsent question")
    click(window, visual_find(panel, "editChatMessage"))
    assert panel.property("editMessageId") == "original"
    assert composer.property("text") == "Original question"
    assert find(panel, "chatEditBanner").isVisible()
    QTest.keyClick(window, Qt.Key.Key_X)
    assert "x" in composer.property("text")

    if cancel_with == "escape":
        QTest.keyClick(window, Qt.Key.Key_Escape)
    else:
        click(window, find(panel, "cancelChatEditButton"))
    assert panel.property("editMessageId") == ""
    assert not find(panel, "chatEditBanner").isVisible()
    assert composer.property("text") == "Unsent question"
    assert composer.hasActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Z)
    typed = composer.property("text")
    assert "z" in typed
    calls = []
    monkeypatch.setattr(bridge, "_command", lambda name, payload: calls.append((name, payload)))
    QTest.keyClick(window, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert calls == [("sendChat", {"id": "", "text": typed})]
    assert composer.property("text") == ""
    assert engine._tingmu_warnings == []


def test_submit_edit_uses_original_message_id_and_leaves_edit_mode(chat, qtbot, monkeypatch):
    _, bridge, engine, window, panel = chat
    bridge._messages = [{"id": "question", "role": "user", "content": "First version"}]
    bridge.messagesChanged.emit()
    qtbot.wait(30)
    click(window, visual_find(panel, "editChatMessage"))
    assert panel.property("editMessageId") == "question"
    composer = find(panel, "chatInput")
    composer.setProperty("text", "Updated question")
    calls = []
    monkeypatch.setattr(bridge, "_command", lambda name, payload: calls.append((name, payload)))
    click(window, find(panel, "sendChatButton"))
    assert calls == [("editChat", {"id": "question", "text": "Updated question"})]
    assert panel.property("editMessageId") == composer.property("text") == ""
    assert not find(panel, "chatEditBanner").isVisible()
    assert engine._tingmu_warnings == []


def test_rename_prefills_and_selects_active_title_and_enter_persists(chat, qtbot):
    _, bridge, engine, window, panel = chat
    first_id = bridge.activeSessionId
    bridge.command("renameChat", {"id": first_id, "title": "Original title"})
    bridge.command("newChat", {})
    second_id = bridge.activeSessionId
    bridge.command("renameChat", {"id": second_id, "title": "Another session"})
    bridge.command("selectChat", {"id": first_id})

    click(window, find(panel, "renameChatButton"))
    dialog = find(panel, "renameChatDialog")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    title = find(dialog, "chatSessionTitle")
    assert title.hasActiveFocus()
    assert title.property("text") == title.property("selectedText") == "Original title"
    QTest.keyClick(window, Qt.Key.Key_R)
    assert title.property("text") == "r"
    QTest.keyClick(window, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: not dialog.property("visible"))
    sessions = {session["id"]: session["title"] for session in bridge.chatSessions}
    assert sessions[first_id] == "r"
    assert sessions[second_id] == "Another session"
    assert engine._tingmu_warnings == []


def test_copy_feedback_is_local_and_expires_for_message_reasoning_and_code(chat, qtbot):
    app, bridge, engine, window, panel = chat
    code = "print(42)\n"
    message = "~~~python\n" + code + "~~~"
    reasoning = "Provider reasoning."
    bridge._messages = [{"id": "reply", "role": "assistant", "status": "complete",
                         "content": message, "reasoning_text": reasoning}]
    bridge.messagesChanged.emit()
    qtbot.wait(40)
    buttons = [(visual_find(panel, "copyChatMessage"), "复制", message),
               (visual_find(panel, "copyChatReasoning"), "复制", reasoning),
               (visual_find(panel, "copyCodeButton"), "复制代码", code)]

    clipboard = app.clipboard()
    previous = clipboard.text()
    try:
        for i, (button, _, expected) in enumerate(buttons):
            click(window, button)
            assert clipboard.text() == expected
            assert button.property("text") == "已复制"
            for untouched, idle_label, _ in buttons[i + 1:]:
                assert untouched.property("text") == idle_label
        qtbot.wait(250)
        assert all(button.property("text") == "已复制" for button, _, _ in buttons)
        qtbot.waitUntil(lambda: all(button.property("text") == label for button, label, _ in buttons),
                        timeout=2500)
    finally:
        clipboard.setText(previous)
    assert engine._tingmu_warnings == []


def test_chinese_companion_header_uses_one_row_in_regular_window(quick, qtbot):
    _, _, engine, main = quick
    main.setWidth(1480)
    invoke(find(main, "interfaceSimplified"), "trigger")
    qtbot.wait(60)
    pane = find(main, "companionModule").property("loadedItem")
    tabs = find(pane, "currentCueTab")
    source = find(pane, "cueSourceButton")
    repeat = find(pane, "repeatCompanionCue")
    assert tabs.property("text") == "当前句"
    for item in (tabs, source, repeat):
        assert item.isVisible() and item.width() > 0 and item.height() > 0
        assert pane.boundingRect().contains(item.mapRectToItem(pane, item.boundingRect()))
    assert abs(tabs.mapToScene(QPointF()).y() - source.mapToScene(QPointF()).y()) < 4
    left_margin = tabs.mapToItem(pane, QPointF()).x()
    right_margin = pane.width() - repeat.mapToItem(pane, QPointF(repeat.width(), 0)).x()
    assert abs(left_margin - right_margin) < 2
    assert engine._tingmu_warnings == []


def test_english_companion_controls_fit_narrow_panel_and_preserve_reading_order(quick, qtbot):
    _, bridge, engine, main = quick
    dock = find(main, "companionModule")
    invoke(dock, "detach")
    pane = dock.property("loadedItem")
    window = pane.window()
    window.setMinimumWidth(280)
    window.setHeight(850)
    media = str(bridge.store.data_dir / "lesson.mkv")
    bridge.media_path = media
    bridge._set_state(mediaPath=media)
    cue = Cue("line", 0, 4, "得閒一齊飲茶。", translation="Have tea together.",
              explanation="An informal invitation.",
              tokens=[Token("得閒", "dak1 haan4")])
    bridge._set_document(StudyDocument(media, LanguagePair(), [cue]))
    bridge._sync_cue(100)
    invoke(find(main, "interfaceEnglish"), "trigger")
    try:
        for width in (280, 300):
            window.setWidth(width)
            qtbot.wait(60)
            assert pane.width() <= width
            if width == 280:
                tab = find(pane, "currentCueTab")
                source = find(pane, "cueSourceButton")
                assert source.mapToScene(QPointF()).y() >= tab.mapToScene(QPointF(0, tab.height())).y()
            for name in ("currentCueTab", "collectedCuesTab", "cueSourceButton", "repeatCompanionCue",
                         "correctCompanionSubtitle", "saveSelectionButton", "saveSentenceButton",
                         "openLearningButton", "cueNoteDraftInput", "saveCueNoteButton"):
                item = find(pane, name)
                x = item.mapToItem(pane, QPointF()).x()
                assert item.isVisible() and item.width() > 0
                assert x >= 0 and x + item.width() <= pane.width() + 1, (width, name, x, item.width())
            order = ("selectableCueText", "correctCompanionSubtitle", "companionTranslation",
                     "companionExplanation", "companionReading", "wordCard0")
            items = [visual_find(pane, name) for name in order]
            tops = [item.mapToItem(pane, QPointF()).y() for item in items]
            assert all(first < second for first, second in zip(tops, tops[1:]))
            assert all(item.isVisible() for item in items)
    finally:
        invoke(find(main, "interfaceSimplified"), "trigger")
    assert engine._tingmu_warnings == []


def test_chat_event_badge_changes_on_same_message_and_toolbar_fits_both_widths(chat, qtbot):
    _, bridge, engine, window, panel = chat
    panel.setProperty("showSessions", False)
    event = {"name": "read_notebook", "label": "读取笔记", "status": "running"}
    bridge._messages = [{"id": "ask", "role": "user", "content": "Review the lesson."},
                        {"id": "reply", "role": "assistant", "content": "Reading.", "tool_events": [event]}]
    bridge.messagesChanged.emit()
    qtbot.wait(30)
    assert visual_find(panel, "chatMessage:ask").property("userMessage")
    answer = visual_find(panel, "chatMessage:reply")
    assert not answer.property("userMessage")
    assert visual_find(answer, "chatToolBadge").property("text") == "进行中"
    for state, result, tone, label in [("complete", {"confirmation_required": True}, "warning", "待确认"),
                                       ("failed", "Synthetic error", "danger", "失败"),
                                       ("complete", {}, "neutral", "已完成")]:
        event.update(status=state, result=result)
        bridge.messagesChanged.emit()
        qtbot.wait(20)
        badge = visual_find(answer, "chatToolBadge")
        assert badge.property("text") == label and badge.property("tone") == tone
    model, effort, send = [find(panel, name) for name in ("chatModelChoice", "chatReasoningChoice", "sendChatButton")]
    for width in (380, 920):
        window.setWidth(width)
        qtbot.wait(40)
        positions = [item.mapToItem(panel, QPointF()) for item in (model, effort, send)]
        assert all(pos.x() >= 0 and pos.x() + item.width() <= panel.width() + 1
                   for pos, item in zip(positions, (model, effort, send)))
        if width == 380:
            assert positions[1].y() > positions[0].y() + model.height()
        else:
            assert abs(positions[1].y() - positions[0].y()) < 2
    assert engine._tingmu_warnings == []
