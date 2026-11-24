"""Streaming retains rendered QTextDocuments, and completed fences remain stable across appends."""

from unittest.mock import patch

from PySide6.QtCore import QObject

from tingmu import richtext
from tingmu.quick.list_models import MessageMarkdownCache
from test_qml_v4 import find, invoke, quick as quick
from test_qml_v5 import visual_find


def test_streaming_fence_checkpoints_match_full_parse():
    cache = MessageMarkdownCache()
    row = {"id": "one", "content": ""}
    text = "heading\r\n~~~py\r\n<&'\r\n~~~\r\n[x](file:/x)\n```\n```\nlast paragraph"
    for char in text:
        row["content"] += char
        assert cache.display([row])[0]["blocks"] == richtext.markdown_blocks(row["content"])
    with patch.object(richtext, "_sanitize", wraps=richtext._sanitize) as sanitize:
        row["content"] += " grows"
        cache.display([row])
        sanitize.assert_called_once_with("last paragraph grows")


def test_streaming_keeps_preceding_markdown_document_and_copy_button(quick, qtbot):
    _, bridge, engine, window = quick
    invoke(window, "openModule", "chat")
    chat = find(window, "chatModule").property("loadedItem")
    message = {"id": "stream", "role": "assistant", "status": "streaming",
               "content": "[source](tingmu://entry/1)\n\n~~~py\nprint(42)\n~~~\ntail"}
    bridge._messages = [message]
    bridge.messagesChanged.emit()
    qtbot.wait(30)
    card = visual_find(chat, "chatMessage:stream")
    tail = visual_find(card, "markdownBody")
    blocks = tail.parentItem().parentItem()
    head_block = next(item for item in blocks.childItems() if (item.property("bodyText") or "").startswith("[source]"))
    head = visual_find(head_block, "markdownBody")
    wrapper = head.property("textDocument")
    doc = wrapper.textDocument()
    qtbot.waitUntil(lambda: str(doc.property("tingmu_format_stamp")).startswith(f"{doc.revision()}|"))
    revision = doc.revision()
    copy = visual_find(card, "copyCodeButton")
    for chunk in (" one", " two", " three"):
        message["content"] += chunk
        bridge.messagesChanged.emit()
        qtbot.wait(12)
        assert visual_find(card, "markdownBody") is tail
        assert visual_find(head_block, "markdownBody") is head
        assert visual_find(card, "copyCodeButton") is copy
        assert doc.revision() == revision
    bridge.setting("theme", "paper")
    qtbot.waitUntil(lambda: doc.revision() > revision)
    assert engine._tingmu_warnings == []


def test_heavy_views_load_on_first_use_and_retain_editors(quick):
    _, _, engine, window = quick
    chat = find(window, "chatPanel")
    editor = find(window, "notebookEditor")
    composer = find(chat, "chatInput")
    for name in ("chatHistory", "learningSidebar", "queueList", "modelBaseUrl", "developerLogList",
                 "workspaceName", "audioOutputChoice"):
        assert window.findChild(QObject, name) is None, name
    composer.setProperty("text", "unsent")
    invoke(window, "openModule", "chat")
    history = find(chat, "chatHistory")
    assert find(chat, "chatInput") is composer and composer.property("text") == "unsent"
    invoke(window, "openModule", "queue")
    find(window, "queueList")
    invoke(window, "changeView", "learn")
    find(window, "learningSidebar")
    assert find(window, "notebookEditor") is editor
    invoke(window, "changeView", "watch")
    assert find(chat, "chatHistory") is history
    assert engine._tingmu_warnings == []
