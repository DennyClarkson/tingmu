"""v0.5 regressions exercise actual windows, focus and user controls."""
from PySide6.QtCore import QCoreApplication, QMetaObject, QPoint, QPointF, QRectF, Qt, Q_ARG, Q_RETURN_ARG
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest

from tingmu.models import Cue, LanguagePair, StudyDocument, Token
from test_qml_v4 import find, invoke, quick as quick


def click(window, item):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)


def test_companion_distinguishes_unopened_unprepared_processing_and_gap(quick, qtbot):
    _, bridge, engine, window = quick
    bridge.timer.stop()
    companion = find(window, "companionModule").property("loadedItem")
    text = find(companion, "selectableCueText")
    assert "打开一个视频" in text.property("text")
    bridge._set_state(mediaPath="synthetic.mp4")
    qtbot.wait(20)
    assert "视频已打开" in text.property("text")
    bridge._jobs = [{"id": "test", "path": "synthetic.mp4", "status": "running", "name": "合成任务"}]
    bridge.jobsChanged.emit()
    qtbot.wait(20)
    assert "正在处理这个视频" in text.property("text")
    bridge._jobs = []
    bridge.jobsChanged.emit()
    bridge._set_document(StudyDocument("synthetic.mp4", LanguagePair(), [Cue("one", 10, 12, "下一句")]))
    bridge._sync_cue(9000)
    qtbot.wait(20)
    assert "没有当前字幕" in text.property("text")
    bridge._sync_cue(10500)
    qtbot.wait(20)
    assert text.property("text") == "下一句"
    assert engine._tingmu_warnings == []


def test_mouse_fullscreen_then_space_pauses_without_leaving(quick, qtbot, monkeypatch):
    _, bridge, engine, window = quick
    bridge._set_state(mediaPath="synthetic.mp4")
    player = find(window, "playerModule").property("loadedItem")
    calls = []
    monkeypatch.setattr(bridge, "_command", lambda name, payload: calls.append((name, payload)))
    click(window, find(window, "enterFullscreen"))
    qtbot.wait(100)
    fullscreen = find(window, "fullscreenWindow")
    assert fullscreen.isVisible() and player.property("fullscreen")
    QTest.keyClick(fullscreen, Qt.Key.Key_Space)
    qtbot.wait(30)
    assert calls == [("playPause", {})]
    assert fullscreen.isVisible() and player.property("fullscreen")
    QTest.keyClick(fullscreen, Qt.Key.Key_Escape)
    qtbot.wait(30)
    assert not fullscreen.isVisible()
    # A real editor must retain Space as text, not trigger playback.
    invoke(window, "openModule", "chat")
    panel = find(window, "chatModule").property("loadedItem")
    chat_window = panel.window()
    chat_window.requestActivate()
    qtbot.wait(50)
    editor = find(panel, "chatInput")
    invoke(editor, "forceActiveFocus")
    QTest.keyClick(chat_window, Qt.Key.Key_Space)
    assert editor.property("text") == " "
    assert calls == [("playPause", {})]
    assert engine._tingmu_warnings == []


def test_detach_frees_slot_and_menu_restores_same_module(quick, qtbot, monkeypatch):
    _, bridge, engine, window = quick
    companion = find(window, "companionModule")
    subtitles = find(window, "subtitleModule")
    subtitle_item = subtitles.property("loadedItem")
    player = find(window, "playerModule")
    sink = bridge.player.videoSink()
    original_companion_height = companion.height()
    invoke(subtitles, "detach")
    qtbot.wait(60)
    assert subtitles.property("detached")
    assert not find(window, "chatModule").property("visible")
    assert companion.height() > original_companion_height
    original_player_width = player.width()
    invoke(companion, "detach")
    qtbot.wait(60)
    assert player.width() > original_player_width
    invoke(window, "openModule", "subtitles")
    invoke(window, "openModule", "companion")
    qtbot.wait(60)
    assert not subtitles.property("detached") and not companion.property("detached")
    assert find(window, "chatModule").property("visible")
    assert subtitles.property("loadedItem") is subtitle_item
    assert bridge.player.videoSink() is sink
    # Imported layouts may put chat in the player's formerly short slot.
    window.setProperty("moduleSlots", {"chat": "player", "player": "subtitles", "subtitles": "chat"})
    window.setProperty("additionalModules", {"chat": True})
    chat = find(window, "chatModule")
    chat.setProperty("detached", False)
    panel = chat.property("loadedItem")
    window.setWidth(1914)
    window.setHeight(1042)
    qtbot.wait(60)
    handle = player.mapToScene(QPointF(player.width() / 2, player.height() + 4)).toPoint()
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, handle)
    QTest.mouseMove(window, QPoint(handle.x(), 250), delay=30)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(handle.x(), 250))
    qtbot.wait(40)
    assert player.height() >= 419
    for height in (1042, 720, 600):
        window.setHeight(height)
        qtbot.wait(60)
        for name in ("chatInput", "chatModelChoice", "chatReasoningChoice", "sendChatButton"):
            control = find(panel, name)
            top = control.mapToItem(panel, QPointF(0, 0)).y()
            bottom = control.mapToItem(panel, QPointF(0, control.height())).y()
            assert top >= 0 and bottom <= panel.height() + 1, (height, name, top, bottom, panel.height())
        assert find(panel, "chatHistory").height() >= 48
        assert subtitles.height() >= 100  # The player below chat still has visible space.
        assert subtitles.mapToScene(QPointF(0, subtitles.height())).y() <= window.height()
    calls = []
    monkeypatch.setattr(bridge, "_command", lambda name, payload: calls.append((name, payload)))
    find(panel, "chatInput").setProperty("text", "短面板发送回归")
    click(window, find(panel, "sendChatButton"))
    assert calls[-1] == ("sendChat", {"id": "", "text": "短面板发送回归"})
    assert engine._tingmu_warnings == []


def test_reasoning_and_preferences_use_shared_settings_in_small_chat(quick, qtbot):
    _, bridge, engine, window = quick
    invoke(window, "openModule", "chat")
    panel = find(window, "chatModule").property("loadedItem")
    floating = panel.window()
    floating.setWidth(380)
    floating.setHeight(740)
    floating.requestActivate()
    qtbot.wait(50)
    choice = find(panel, "chatReasoningChoice")
    invoke(choice, "forceActiveFocus")
    QTest.keyClick(floating, Qt.Key.Key_Down)
    qtbot.wait(20)
    assert bridge.settings["companion_reasoning_effort"] == "none"
    bridge.setting("companion_reasoning_effort", "high")
    qtbot.wait(20)
    assert choice.property("currentIndex") == 5
    assert choice.mapToScene(QPointF(choice.width(), 0)).x() <= floating.width()
    click(floating, find(panel, "chatTeachingPreferencesButton"))
    qtbot.wait(30)
    dialog = find(panel, "teachingPreferencesDialog")
    assert dialog.property("opened")
    find(dialog, "teachingPreferencesInput").setProperty("text", "重点比较普通话和粤语，解释语气词，给出生活例句。")
    invoke(find(dialog, "saveTeachingPreferences"), "clicked")
    qtbot.wait(30)
    assert "解释语气词" in bridge.settings["teaching_preferences"]
    assert not bridge.state["queueRunning"]
    assert engine._tingmu_warnings == []


def test_collections_are_separate_and_organizing_is_explicit(quick, qtbot, monkeypatch):
    _, bridge, engine, window = quick
    cue = Cue("one", 1, 3, "我哋去飲茶")
    for word in ("我哋", "飲茶"):
        bridge.store.save_word("synthetic.mp4", cue, Token(word), LanguagePair())
    invoke(window, "changeView", "learn")
    qtbot.wait(40)
    learning = find(window, "learningWorkspace")
    inbox = find(window, "collectionInbox")
    assert learning.property("activeTab") == "collections"
    assert find(inbox, "collectionList").property("count") == 2
    assert bridge.notebooks["notes"] == []
    assert "飲茶" not in bridge.notebooks["markdown"]
    entries = bridge.notebooks["collections"]
    first = entries[0]
    invoke(inbox, "select", first["id"], True)
    find(inbox, "collectionOrganizeInstructions").setProperty("text", "给出母语对比和生活例句")
    calls = []
    with monkeypatch.context() as scoped:
        scoped.setattr(bridge, "_command", lambda name, payload: calls.append((name, payload)))
        click(window, find(inbox, "organizeCollectionsButton"))
    assert calls == [("organizeNotebook", {"ids": [first["id"]], "instructions": "给出母语对比和生活例句"})]
    bridge.notebook_manager.append_note("yue", "日常表达", "### 母语对比\n\n我哋相当于普通话的我们。\n\n### 用法和例句\n\n我哋一齊去。", [first["id"]])
    bridge._refresh_notebooks()
    qtbot.wait(30)
    assert len(bridge.notebooks["collections"]) == 2
    assert bridge.notebooks["stats"]["unorganized"] == 1
    status = find(inbox, "collectionStatusFilter")
    invoke(status, "forceActiveFocus")
    QTest.keyClick(window, Qt.Key.Key_Down)
    qtbot.wait(20)
    assert find(inbox, "collectionList").property("count") == 1
    click(window, find(window, "notesTab"))
    assert learning.property("activeTab") == "notes"
    assert not inbox.property("visible")
    assert find(window, "organizedNotesList").property("count") == 1
    for theme in ("forest", "paper", "mist", "graphite"):
        bridge.setting("theme", theme)
        qtbot.wait(20)
        click(window, find(window, "collectionsTab"))
        assert inbox.property("visible")
        click(window, find(window, "notesTab"))
    assert engine._tingmu_warnings == []


def test_collection_note_edit_saves_revision_and_tool_action_opens_workspace(quick, qtbot):
    _, bridge, engine, window = quick
    cue = Cue("one", 1, 3, "我哋去飲茶")
    bridge.store.save_word("synthetic.mp4", cue, Token("我哋"), LanguagePair())
    invoke(window, "changeView", "learn")
    inbox = find(window, "collectionInbox")
    entry = bridge.notebooks["collections"][0]
    invoke(inbox, "editNote", entry)
    qtbot.wait(20)
    dialog = find(inbox, "collectionNoteDialog")
    find(dialog, "collectionEntryNote").setProperty("text", "类似普通话的我们，我想练习称呼。")
    invoke(find(dialog, "saveCollectionEntryNote"), "clicked")
    qtbot.wait(40)
    assert not dialog.property("opened")
    assert bridge.notebooks["collections"][0]["entry_note"] == "类似普通话的我们，我想练习称呼。"
    assert not bridge.notebooks["collections"][0]["organized"]
    assert "类似普通话" not in bridge.notebooks["markdown"]
    invoke(window, "changeView", "watch")
    player = find(window, "playerModule").property("loadedItem")
    click(window, find(window, "enterFullscreen"))
    qtbot.wait(40)
    assert player.property("fullscreen")
    bridge.appAction.emit("workspace", {"view": "prepare", "module": "chat"})
    qtbot.wait(40)
    assert not player.property("fullscreen")
    assert window.property("viewMode") == "prepare"
    assert find(window, "chatModule").property("detached")
    bridge._set_state(chatToolStatus="已保存讲解偏好", chatToolPhase="success")
    chat_panel = find(window, "chatModule").property("loadedItem")
    status = find(chat_panel, "chatToolStatus")
    assert status.property("visible") and status.property("text") == "已保存讲解偏好"
    assert engine._tingmu_warnings == []


def test_paused_fullscreen_mouse_selection_saves_exact_source_text(quick, qtbot):
    _, bridge, engine, window = quick
    bridge.media_path = "synthetic.mp4"
    bridge._set_state(mediaPath=bridge.media_path)
    bridge._set_document(StudyDocument(bridge.media_path, LanguagePair(), [Cue("one", 1, 3, "今日我哋去飲茶")]))
    bridge._sync_cue(1500)
    click(window, find(window, "enterFullscreen"))
    qtbot.wait(100)
    fullscreen = find(window, "fullscreenWindow")
    overlay = find(window, "companionOverlay")
    content = find(overlay, "overlayScroll")
    text = find(content, "selectableCueText")
    def rect(position):
        return QMetaObject.invokeMethod(text, "positionToRectangle", Qt.ConnectionType.DirectConnection,
                                       Q_RETURN_ARG(QRectF), Q_ARG("int", position))
    begin, end = rect(2), rect(4)
    start = text.mapToScene(QPointF(begin.x() + 0.1, begin.center().y())).toPoint()
    finish = text.mapToScene(QPointF(end.x() + 0.1, end.center().y())).toPoint()
    QTest.mousePress(fullscreen, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(fullscreen, finish, delay=30)
    QTest.mouseRelease(fullscreen, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, finish)
    assert text.property("selectedText") == "我哋"
    selected = find(content, "saveSelectionButton")
    assert selected.property("enabled") and selected.property("text") == "收藏选中文字"
    click(fullscreen, selected)
    qtbot.wait(30)
    assert any(entry["text"] == "我哋" for entry in bridge.store.entries())
    assert find(window, "playerModule").property("loadedItem").property("fullscreen")
    assert engine._tingmu_warnings == []


def test_long_notebook_editor_follows_keyboard_cursor_but_not_reading_scroll(quick, qtbot):
    app, bridge, engine, window = quick
    long_text = "# 长文编辑回归\n\n" + "\n".join(f"第 {index} 行：这是只用于测试的长笔记。" for index in range(180))
    bridge.notebook_manager.save("yue", long_text, bridge.notebooks["revision"])
    bridge._refresh_notebooks()
    invoke(window, "changeView", "learn")
    learning = find(window, "learningWorkspace")
    invoke(learning, "startEdit")
    editor = find(window, "notebookEditor")
    scroll = find(window, "notebookScroll")
    view = scroll.property("contentItem")
    content = view.property("contentItem")
    window.requestActivate()
    qtbot.wait(40)
    editor_point = editor.mapToScene(QPointF(20, 15)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, editor_point)
    QTest.keyClick(window, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(100)
    def cursor_visible():
        rect = editor.property("cursorRectangle")
        point = editor.mapToItem(content, QPointF(rect.x(), rect.y()))
        return (point.y() >= view.property("contentY") - 1
                and point.y() + rect.height() <= view.property("contentY") + view.height() + 1)
    assert editor.property("cursorPosition") == len(long_text)
    assert view.property("contentY") > view.height()
    assert cursor_visible()
    clipboard = app.clipboard()
    previous = clipboard.text()
    addition = "\n末尾追加内容。\n再加一行，插入点必须仍在可见范围。"
    try:
        clipboard.setText(addition)
        QTest.keyClick(window, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
    finally:
        clipboard.setText(previous)
    qtbot.wait(100)
    assert editor.property("text") == long_text + addition
    assert cursor_visible()
    assert find(window, "saveNotebookButton").property("enabled")
    QTest.keyClick(window, Qt.Key.Key_Home, Qt.KeyboardModifier.ControlModifier)
    qtbot.wait(100)
    assert editor.property("cursorPosition") == 0
    assert view.property("contentY") < 1
    assert cursor_visible()
    # Wheel reading must not move the cursor or bounce back to its position.
    local = scroll.mapToScene(QPointF(scroll.width() / 2, scroll.height() / 2))
    global_point = window.mapToGlobal(local.toPoint())
    wheel = QWheelEvent(local, QPointF(global_point), QPoint(), QPoint(0, -720),
                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QCoreApplication.sendEvent(window, wheel)
    from time import monotonic

    last_y = view.property("contentY")
    unchanged_since = monotonic()

    def wheel_has_settled():
        nonlocal last_y, unchanged_since
        current_y = view.property("contentY")
        if current_y != last_y or view.property("moving") or view.property("flicking"):
            last_y = current_y
            unchanged_since = monotonic()
            return False
        return monotonic() - unchanged_since >= 0.15

    # Wait for both Flickable motion and stable frames, including its final pixel adjustment.
    qtbot.waitUntil(wheel_has_settled, timeout=3000)
    reading_y = view.property("contentY")
    assert reading_y > 0
    assert not cursor_visible()
    qtbot.wait(250)
    assert abs(view.property("contentY") - reading_y) < 1
    assert editor.property("cursorPosition") == 0
    # Resuming keyboard editing reveals the cursor again.
    QTest.keyClick(window, Qt.Key.Key_Right)
    qtbot.wait(100)
    assert cursor_visible()
    assert engine._tingmu_warnings == []


def test_chat_stop_immediately_shows_waiting_and_blocks_duplicate_actions(quick, qtbot, monkeypatch):
    _, bridge, engine, window = quick
    invoke(window, "openModule", "chat")
    dock = find(window, "chatModule")
    panel = dock.property("loadedItem")
    floating = panel.window()
    floating.setWidth(380)
    floating.setHeight(740)
    floating.requestActivate()
    bridge._messages = [{"id": "pending-test", "role": "assistant", "content": "",
                         "blocks": [], "status": "pending"}]
    bridge.messagesChanged.emit()
    bridge._set_state(chatBusy=True, chatStopping=False)
    qtbot.wait(50)

    def response_status():
        # ListView delegates belong to the visual tree rather than QObject parenting.
        remaining = [panel]
        while remaining:
            item = remaining.pop()
            if item.objectName() == "chatResponseStatus":
                return item
            remaining.extend(item.childItems())
        raise AssertionError("No rendered assistant status")

    assert response_status().property("text") == "正在连接…"
    assert dock.property("subtitle") == "正在连接…"
    calls = []

    def command(name, payload):
        calls.append((name, payload))
        if name == "stopChat":
            bridge._set_state(chatStopping=True)

    monkeypatch.setattr(bridge, "_command", command)
    stop = find(panel, "stopChatButton")
    click(floating, stop)
    qtbot.wait(20)
    assert calls == [("stopChat", {})]
    assert stop.property("text") == "停止中" and not stop.property("enabled")
    assert dock.property("subtitle") == "正在停止…等待当前连接结束"
    assert response_status().property("text") == "正在停止…等待当前连接结束"
    assert stop.mapToScene(QPointF(stop.width(), 0)).x() <= floating.width()
    click(floating, stop)
    editor = find(panel, "chatInput")
    editor.setProperty("text", "等待结束再发送")
    invoke(panel, "send")
    assert not editor.property("enabled")
    assert not find(panel, "newChatButton").property("enabled")
    assert not find(panel, "sendChatButton").property("enabled")
    assert calls == [("stopChat", {})]
    # A partial stream stays intact, while its progress label still reflects cancellation.
    bridge._messages[0].update(content="已收到的部分回复", status="streaming")
    bridge.messagesChanged.emit()
    qtbot.wait(20)
    assert response_status().property("text") == "正在停止…等待当前连接结束"
    assert bridge.messages[0]["content"] == "已收到的部分回复"
    # The backend scopes chatStopping to the selected session; another session is unaffected.
    bridge._set_state(chatStopping=False)
    qtbot.wait(20)
    assert response_status().property("text") == "正在回复…"
    bridge._messages[0]["status"] = "stopped"
    bridge.messagesChanged.emit()
    bridge._set_state(chatBusy=False, chatStopping=False)
    qtbot.wait(20)
    assert response_status().property("text") == "已停止生成"
    assert editor.property("enabled")
    assert find(panel, "newChatButton").property("enabled")
    assert not stop.property("visible")
    assert dock.property("subtitle") == ""
    click(floating, find(panel, "sendChatButton"))
    assert calls[-1] == ("sendChat", {"id": "", "text": "等待结束再发送"})
    assert engine._tingmu_warnings == []


def visual_find(item, name):
    remaining = [item]
    while remaining:
        current = remaining.pop()
        if current.objectName() == name:
            return current
        remaining.extend(current.childItems())
    raise AssertionError(name)


def test_reasoning_collapses_streams_copies_and_keeps_stopped_history(quick, qtbot):
    app, bridge, engine, window = quick
    invoke(window, "openModule", "chat")
    panel = find(window, "chatModule").property("loadedItem")
    floating = panel.window()
    floating.setWidth(380)
    floating.setHeight(800)
    floating.requestActivate()
    message = {"id": "reasoning-test", "role": "assistant", "content": "", "blocks": [],
               "status": "streaming", "phase": "thinking", "reasoning_kind": "reasoning",
               "reasoning_elapsed_ms": 2000, "reasoning_text": "<b>这是实际返回的文本</b>\n" * 50}
    bridge._messages = [message]
    bridge.messagesChanged.emit()
    bridge._set_state(chatBusy=True)
    qtbot.wait(40)
    reasoning = visual_find(panel, "chatReasoning")
    assert reasoning.property("visible") and not reasoning.property("expanded")
    assert "思考过程" in find(reasoning, "toggleChatReasoning").property("text")
    assert reasoning.property("elapsedSeconds") == 2
    qtbot.wait(1100)
    assert reasoning.property("elapsedSeconds") >= 3
    click(floating, find(reasoning, "toggleChatReasoning"))
    qtbot.wait(30)
    assert reasoning.property("expanded")
    assert find(reasoning, "chatReasoningScroll").height() <= 220
    text = find(reasoning, "chatReasoningText")
    assert text.property("readOnly") and text.property("selectByMouse")
    quick_document = text.property("textDocument")
    document = quick_document.textDocument()
    assert document.toPlainText() == message["reasoning_text"]  # Provider HTML remains literal text.
    assert reasoning.mapToScene(QPointF(reasoning.width(), 0)).x() <= floating.width()
    clipboard = app.clipboard()
    previous = clipboard.text()
    try:
        click(floating, find(reasoning, "copyChatReasoning"))
        assert clipboard.text() == message["reasoning_text"]
    finally:
        clipboard.setText(previous)
    message["reasoning_text"] += "新返回的思考内容"
    message["reasoning_elapsed_ms"] = 3400
    bridge.messagesChanged.emit()
    qtbot.wait(40)
    reasoning = visual_find(panel, "chatReasoning")
    assert reasoning.property("expanded")
    assert find(reasoning, "chatReasoningText").property("text").endswith("新返回的思考内容")
    bridge._set_state(chatStopping=True)
    qtbot.wait(20)
    assert "正在停止" in find(reasoning, "chatReasoningProgress").property("text")
    message.update(status="stopped", phase="thinking")
    bridge.messagesChanged.emit()
    bridge._set_state(chatBusy=False, chatStopping=False)
    qtbot.wait(30)
    reasoning = visual_find(panel, "chatReasoning")
    assert reasoning.property("elapsedSeconds") == 3
    assert not reasoning.property("thinking")
    click(floating, find(reasoning, "toggleChatReasoning"))
    assert not reasoning.property("expanded")
    click(floating, find(reasoning, "toggleChatReasoning"))
    assert reasoning.property("expanded")
    # Historical Responses summaries use their own label and do not invent absent text.
    message.update(reasoning_kind="summary", status="complete", phase="answering")
    bridge.messagesChanged.emit()
    qtbot.wait(20)
    reasoning = visual_find(panel, "chatReasoning")
    assert "思考摘要" in find(reasoning, "toggleChatReasoning").property("text")
    message["reasoning_text"] = ""
    bridge.messagesChanged.emit()
    qtbot.wait(20)
    assert not visual_find(panel, "chatReasoning").property("visible")
    for phase, label in [("connecting", "正在连接"), ("thinking", "正在思考"),
                         ("acting", "正在执行学习操作"), ("answering", "正在生成回复")]:
        bridge._set_state(chatBusy=True)
        message.update(status="streaming", phase=phase)
        bridge.messagesChanged.emit()
        qtbot.wait(20)
        assert label in visual_find(panel, "chatResponseStatus").property("text")
        assert label in find(window, "chatModule").property("subtitle")
    # A worker belonging to another session must not imply activity in this history.
    message.update(status="complete")
    bridge.messagesChanged.emit()
    qtbot.wait(20)
    assert find(window, "chatModule").property("subtitle") == ""
    assert engine._tingmu_warnings == []


def test_model_protocol_selection_is_explicit_and_provider_scoped(quick, qtbot, monkeypatch):
    _, bridge, engine, window = quick
    manager = find(window, "modelManagerWindow")
    invoke(manager, "open")
    qtbot.wait(30)
    choice = find(manager, "modelProtocolChoice")
    for provider in ("openai", "custom"):
        invoke(manager, "populate", {"id": "existing", "provider": provider, "protocol": "openai",
                                     "label": "Test", "base_url": "https://example.test/v1"})
        assert manager.property("protocol") == "openai"
        assert choice.property("visible") and choice.property("currentIndex") == 0
        invoke(choice, "forceActiveFocus")
        QTest.keyClick(manager, Qt.Key.Key_Down)
        qtbot.wait(20)
        assert manager.property("protocol") == "responses"
        calls = []
        with monkeypatch.context() as scoped:
            scoped.setattr(bridge, "_command", lambda name, payload: calls.append((name, payload)))
            invoke(find(manager, "saveModelButton"), "clicked")
        assert calls[0][0] == "saveModel" and calls[0][1]["protocol"] == "responses"
    invoke(manager, "populate", {"provider": "openai", "protocol": "responses"})
    assert choice.property("currentIndex") == 1
    for provider, protocol in (("deepseek", "openai"), ("anthropic", "anthropic"), ("ollama", "openai")):
        invoke(manager, "populate", {"provider": provider, "protocol": protocol})
        assert not choice.property("visible")
        assert manager.property("protocol") == protocol
        preset = find(manager, "modelPresetChoice")
        assert bridge.modelPresets[preset.property("currentIndex")]["provider"] == provider
    # Editing/reopening a saved connection updates only the displayed preset, not its draft.
    invoke(manager, "populate", {"id": "saved-deepseek", "provider": "deepseek", "protocol": "openai",
                                 "label": "My DeepSeek", "base_url": "https://example.test/ds",
                                 "model": "deepseek-chat"})
    find(manager, "modelLabel").setProperty("text", "Unsaved DeepSeek label")
    manager.hide()
    invoke(manager, "open")
    qtbot.wait(20)
    assert preset.property("currentText") == "DeepSeek"
    assert manager.property("profileId") == "saved-deepseek"
    assert find(manager, "modelLabel").property("text") == "Unsaved DeepSeek label"
    assert find(manager, "modelBaseUrl").property("text") == "https://example.test/ds"
    assert find(manager, "modelName").property("text") == "deepseek-chat"
    # Only a user activation starts a connection from the selected preset.
    invoke(preset, "forceActiveFocus")
    QTest.keyClick(manager, Qt.Key.Key_Down)
    qtbot.wait(20)
    assert manager.property("provider") == "qwen"
    assert manager.property("profileId") == ""
    # Discovery belongs to each saved connection, never the last global result.
    ds = {"id": "deepseek-discovered", "provider": "deepseek", "protocol": "openai",
          "label": "DeepSeek", "base_url": "https://example.test/ds", "model": "typed-ds-model",
          "models": ["deepseek-chat", "deepseek-flash"]}
    other = {"id": "openai-discovered", "provider": "openai", "protocol": "responses",
             "label": "OpenAI", "base_url": "https://example.test/openai", "model": "typed-openai-model",
             "models": ["own-openai-model"]}
    bridge._model_profiles = [ds, other]
    bridge._discovered_models = ds["models"]
    bridge.modelsChanged.emit()
    invoke(manager, "populate", ds)
    discovered = find(manager, "discoveredModelChoice")
    assert discovered.property("count") == 2
    click(manager, find(manager, "newModelButton"))
    assert manager.property("provider") == "openai"
    assert manager.property("profileId") == ""
    assert discovered.property("count") == 0
    assert discovered.property("currentText") == ""
    invoke(manager, "populate", other)
    assert discovered.property("count") == 1
    assert discovered.property("currentText") == "own-openai-model"
    assert manager.property("protocol") == "responses"
    assert find(manager, "modelName").property("text") == "typed-openai-model"
    invoke(manager, "populate", ds)
    assert discovered.property("count") == 2
    assert find(manager, "modelName").property("text") == "typed-ds-model"
    assert manager.property("protocol") == "openai"
    assert engine._tingmu_warnings == []
