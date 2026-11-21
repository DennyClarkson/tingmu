"""Behavior checks for synchronization, real storage, and safe worker dispatch."""

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, QThread, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QFont, QImage, QTextDocument
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame
from PySide6.QtWidgets import QApplication

from tingmu.models import Cue, LanguagePair, StudyDocument, Token
from tingmu.storage import Store
from tingmu.ui import MainWindow
from tingmu.ui.app import SeekSlider


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    # Startup and ordinary playback must not instantiate a cloud client.
    def no_cloud(*args, **kwargs):
        raise AssertionError("Unexpected cloud request")

    monkeypatch.setattr("tingmu.ui.app.LearningAgent", no_cloud)
    widget = MainWindow(Store(tmp_path / "data"))
    qtbot.addWidget(widget)
    widget.show()
    yield widget
    if widget.busy:
        widget.cancel_jobs()
        qtbot.waitUntil(lambda: not widget.busy, timeout=5000)
    widget.close()


def document(tmp_path, **metadata):
    return StudyDocument(str(tmp_path / "video.mkv"), LanguagePair(), [
        Cue("a", 1, 2, "你好", translation="你好", tokens=[Token("你好", "nei5 hou2", "打招呼")]),
        Cue("b", 3, 4, "多謝", tokens=[Token("多謝", "do1 ze6")]),
    ], metadata=metadata)


def test_sync_clears_gaps_and_applies_delay(window, tmp_path):
    window.set_document(document(tmp_path))
    window.sync_cue(1500)
    assert window.companion.cue.id == "a"
    assert window.subtitle_label.text() == "你好"
    window.sync_cue(2500)
    assert window.companion.cue is None  # A one-second gap exceeds the bridging threshold.
    assert window.subtitle_label.text() == ""
    window.offset_spin.setValue(500)
    window.sync_cue(1500)
    assert window.active_cue.id == "a"
    window.sync_cue(1000)
    assert window.active_cue is None
    window.sync_cue(3500)
    assert window.active_cue.id == "b"


def test_words_notes_and_drafts_survive_cue_changes(window, tmp_path):
    doc = document(tmp_path)
    window.media_path = tmp_path / "video.mkv"
    window.set_document(doc)
    window.sync_cue(1500)
    companion = window.companion
    companion.note.setPlainText("问候语")
    window.sync_cue(3500)
    assert companion.note.toPlainText() == ""
    window.sync_cue(1500)
    assert companion.note.toPlainText() == "问候语"
    companion.save_note()
    companion.select_token(doc.cues[0].tokens[0])
    companion.save_word()
    companion.note.clear()
    companion.save_sentence()  # A second bookmark must not erase an existing note.
    entries = window.store.entries(str(window.media_path))
    assert len(entries) == 2
    assert next(e for e in entries if e["kind"] == "note")["note"] == "问候语"
    assert next(e for e in entries if e["kind"] == "word")["reading"] == "nei5 hou2"
    window.open_notebook()
    assert window._notebook.table.rowCount() == 2


def test_worker_runs_off_gui_and_results_return_to_gui(window, qtbot):
    calls = []

    def work(progress, cancelled):
        calls.append(("worker", QThread.currentThread()))
        progress("测试进度")
        return "完成"

    def complete(result):
        calls.append((result, QThread.currentThread()))

    window.start_job("测试", work, complete)
    qtbot.waitUntil(lambda: not window.busy, timeout=5000)
    assert calls[0][1] != QApplication.instance().thread()
    assert calls[1] == ("完成", QApplication.instance().thread())
    assert window.prepare_button.isEnabled()
    assert not window.cancel_button.isEnabled()


def test_close_cancels_worker_and_defers_shutdown(window, qtbot):
    entered = threading.Event()

    def work(progress, cancelled):
        entered.set()
        while not cancelled():
            threading.Event().wait(0.01)
        return "cancelled"

    results = []
    window.start_job("取消测试", work, results.append)
    qtbot.waitUntil(entered.is_set, timeout=5000)
    window.close()
    assert window._closing
    qtbot.waitUntil(lambda: not window.busy, timeout=5000)
    qtbot.waitUntil(lambda: not window.isVisible(), timeout=5000)
    assert results == []  # No result UI is opened during shutdown.


def test_cache_respects_language_and_asr_audio_track(window, tmp_path):
    doc = document(tmp_path, transcript_spec={"audio_track": 1})
    assert not window.document_compatible(doc)
    window.asr_audio_spin.setValue(1)
    assert window.document_compatible(doc)
    doc.metadata["transcript_spec"]["subtitle"] = "video.srt"
    window.asr_audio_spin.setValue(0)
    assert window.document_compatible(doc)
    window.target_combo.setCurrentIndex(window.target_combo.findData("en"))
    assert not window.document_compatible(doc)


def test_partial_cache_has_explicit_status(window, tmp_path):
    doc = document(tmp_path, stage="partial", annotated_count=1)
    text = window.document_status(doc)
    assert "部分完成" in text
    assert "1/2" in text


def test_shortcuts_leave_note_typing_untouched(window, qtbot):
    window.set_document(document(window.store.data_dir))
    window.sync_cue(1500)
    window.show_companion()
    window.companion.tabs.setCurrentIndex(1)
    window.companion.note.setFocus()
    qtbot.waitUntil(lambda: window.companion.note.hasFocus())
    qtbot.keyClicks(window.companion.note, "f m r [ ]")
    assert window.companion.note.toPlainText() == "f m r [ ]"
    assert not window._fullscreen
    assert window._ab_a is None


def test_seek_click_emits_release_at_clicked_time(qtbot):
    slider = SeekSlider(Qt.Orientation.Horizontal)
    slider.resize(400, 30)
    slider.setRange(0, 10000)
    qtbot.addWidget(slider)
    slider.show()
    positions = []
    slider.sliderReleased.connect(lambda: positions.append(slider.value()))
    qtbot.mouseClick(slider, Qt.MouseButton.LeftButton, pos=QPoint(300, 15))
    assert positions == [7500]


def test_notes_and_drafts_are_isolated_by_language_and_media(window, tmp_path):
    original = document(tmp_path)
    window.media_path = tmp_path / "video.mkv"
    window.set_document(original)
    window.sync_cue(1500)
    window.companion.note.setPlainText("普通话讲解笔记")
    window.companion.save_note()
    translated = StudyDocument(original.media_path, LanguagePair("en", "yue"), original.cues)
    window.set_document(translated)
    window.sync_cue(1500)
    assert window.companion.note.toPlainText() == ""
    window.companion.note.setPlainText("English draft")
    window.set_document(original)
    window.sync_cue(1500)
    assert window.companion.note.toPlainText() == "普通话讲解笔记"
    window.set_document(translated)
    window.sync_cue(1500)
    assert window.companion.note.toPlainText() == "English draft"
    window.media_path = tmp_path / "different.mkv"
    window.companion.reset_document()
    window.set_document(StudyDocument(str(window.media_path), translated.language, translated.cues))
    window.sync_cue(1500)
    assert window.companion.note.toPlainText() == ""


def test_switch_audio_during_preparation_clears_incompatible_annotation(window, tmp_path, qtbot):
    window.media_path = tmp_path / "video.mkv"
    window.set_document(document(tmp_path, transcript_spec={"audio_track": 0}))
    window.sync_cue(1500)

    def work(progress, cancelled):
        while not cancelled():
            threading.Event().wait(0.01)

    window.start_job("测试", work, lambda result: None)
    window.asr_audio_spin.setValue(1)
    assert window.document is None
    assert window.companion.cue is None
    assert window.subtitle_label.text() == ""
    window.cancel_jobs()
    qtbot.waitUntil(lambda: not window.busy, timeout=5000)


@pytest.mark.parametrize("pipeline_cache", [False, True])
def test_restored_external_subtitle_is_used_for_preparation(window, tmp_path, qtbot, monkeypatch, pipeline_cache):
    media = tmp_path / "video.mkv"
    media.write_bytes(b"test media identity")
    subtitle = tmp_path / "unrelated-name.srt"
    subtitle.write_text("1\n00:00:01,000 --> 00:00:02,000\nhello\n", encoding="utf-8")
    metadata = {"transcript_spec": {"subtitle": str(subtitle)}} if pipeline_cache else {"subtitle_path": str(subtitle)}
    doc = document(tmp_path, **metadata)
    window.store.save_document(doc)
    window.media_path = media
    window.subtitle_path = None
    window.reload_cached_document()
    assert window.subtitle_path == subtitle
    sources = []

    def prepare(media_path, settings, store, subtitle_path=None, **kwargs):
        sources.append(subtitle_path)
        return doc

    monkeypatch.setattr("tingmu.ui.app.prepare_media", prepare)
    window.prepare()
    qtbot.waitUntil(lambda: not window.busy, timeout=5000)
    assert sources == [subtitle]


def test_overlap_restores_earlier_dialogue_until_its_end(window, tmp_path):
    doc = StudyDocument(str(tmp_path / "video.mkv"), LanguagePair(), [
        Cue("dialogue", 0, 10, "长对白"),
        Cue("sign", 2, 3, "短标牌"),
        Cue("next", 20, 21, "下一句"),
    ])
    window.set_document(doc)
    window.sync_cue(2500)
    assert window.active_cue.id == "sign"
    window.sync_cue(4000)
    assert window.active_cue.id == "dialogue"
    assert window.companion.cue.id == "dialogue"
    window.sync_cue(13000)
    assert window.active_cue is None
    window.sync_cue(20500)
    assert window.active_cue.id == "next"


def test_provenance_distinguishes_asr_external_and_pending_analysis(window, tmp_path):
    doc = document(tmp_path, transcript_spec={"asr_model": "large-v3-turbo"}, stage="ready")
    doc.provider = "openai"
    doc.model = "gpt-5.4-mini"
    window.set_document(doc)
    window.sync_cue(1500)
    label = window.companion.provenance
    assert "机器转写" in label.text()
    assert "未经人工逐句听音确认" in label.toolTip()
    assert "讲解：openai/gpt-5.4-mini" in label.text()
    assert "书面中文" in label.toolTip()
    doc.metadata.update(stage="partial", completed_ids=[])
    window.set_document(doc)
    window.sync_cue(1500)
    assert "讲解：待处理" in label.text()
    assert "openai" not in label.text()
    doc.metadata = {"subtitle_path": str(tmp_path / "dialogue.ass")}
    doc.cues[0].original_text = r"{\i1}你好{\i0}"
    window.set_document(doc)
    window.sync_cue(1500)
    assert "外部字幕" in label.text()
    assert "机器转写" not in label.text()
    assert "原始字幕" in label.toolTip()
    assert r"{\i1}" in label.toolTip()


def test_directory_drop_playlist_order_and_nonwrapping_advance(window, tmp_path, qtbot, monkeypatch):
    folder = tmp_path / "playlist"
    folder.mkdir()
    first, second = folder / "a.mkv", folder / "b.mp4"
    second.write_bytes(b"second")
    first.write_bytes(b"first")
    (folder / "ignore.txt").write_text("not media")
    assert window.open_dropped_path(folder)
    qtbot.waitUntil(lambda: not window.busy, timeout=5000)
    assert window.library_list.count() == 2
    opened = []
    monkeypatch.setattr(window, "open_media", lambda path, position=None: opened.append((path, position)))
    window.media_path = first
    window.play_adjacent_media(1)
    assert opened == [(second, None)]
    window.advance_action.setChecked(True)
    window.on_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert opened[-1] == (second, 0)
    window.media_path = second
    count = len(opened)
    window.on_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert len(opened) == count  # End of playlist does not loop back.
    window.play_adjacent_media(-1)
    assert opened[-1] == (first, None)
    window.media_path = first
    window._ab_a, window._ab_b = 0, 1000
    count = len(opened)
    window.on_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert len(opened) == count  # An explicit loop takes priority over autoplay.


def test_file_drops_route_subtitles_and_reject_remote_urls(window, tmp_path, monkeypatch):
    media = tmp_path / "movie.mkv"
    media.write_bytes(b"test")
    subtitle = tmp_path / "dialogue.ass"
    subtitle.write_text("test")
    routed = []
    monkeypatch.setattr(window, "open_media", lambda path: routed.append(("media", path)))
    monkeypatch.setattr(window, "load_subtitle_file", lambda path: routed.append(("subtitle", path)))
    assert not window.open_dropped_path(subtitle)
    assert window.open_dropped_path(media)
    window.media_path = media
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(subtitle))])
    enter = QDragEnterEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dragEnterEvent(enter)
    assert enter.isAccepted()
    drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dropEvent(drop)
    assert drop.isAccepted()
    assert routed == [("media", media), ("subtitle", subtitle)]
    remote = QMimeData()
    remote.setUrls([QUrl("https://example.invalid/movie.mp4")])
    enter = QDragEnterEvent(QPointF(10, 10), Qt.DropAction.CopyAction, remote,
                           Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    window.dragEnterEvent(enter)
    assert not enter.isAccepted()


def test_snapshot_saves_video_sink_pixels(window, tmp_path, monkeypatch):
    image = QImage(16, 12, QImage.Format.Format_RGBA8888)
    image.fill("#86B99A")
    window.video.videoSink().setVideoFrame(QVideoFrame(image))
    output = window.store.data_dir / "exports" / "frame.png"
    monkeypatch.setattr("tingmu.ui.app.QFileDialog.getSaveFileName", lambda *args: (str(output), "PNG (*.png)"))
    window.save_current_frame()
    saved = QImage(str(output))
    assert saved.size() == image.size()
    assert saved.pixelColor(3, 3).name() == "#86b99a"


def test_chat_cloud_gate_real_worker_context_and_persistent_history(window, tmp_path, qtbot, monkeypatch):
    calls = []

    class Tutor:
        def __init__(self, provider, model):
            self.model = model
            assert provider == "openai"

        def reply(self, message, history, language, current_cue=None, context=None):
            calls.append((message, list(history), current_cue, context, QThread.currentThread()))
            return "可以在「更多」导入字幕。"

    monkeypatch.setattr("tingmu.companion.CompanionAgent", Tutor)
    companion = window.companion
    window.cloud_check.setChecked(False)
    companion.chat_input.setText("怎么导入字幕？")
    companion.send_chat()
    assert not calls
    assert companion.messages == []
    assert "开启" in companion.chat_status.text()
    window.cloud_check.setChecked(True)
    companion.send_chat()
    qtbot.waitUntil(lambda: not window.busy, timeout=5000)
    assert len(calls) == 1
    assert calls[0][2] is None  # Application help also works before opening media.
    assert "导入字幕" in calls[0][3]["available_controls"]
    assert calls[0][4] != QApplication.instance().thread()
    stored = window.store.load_conversation("", window.current_language())
    assert [entry["role"] for entry in stored] == ["user", "assistant"]
    assert window.read_settings().companion_model == "gpt-6-astra"
    assert window.read_settings().model != window.read_settings().companion_model
    window.media_path = tmp_path / "another.mkv"
    window.set_document(None)
    assert companion.messages == []
    window.media_path = None
    window.set_document(None)
    assert companion.messages == stored
    companion.chat_input.setText("下一步呢？")
    companion.send_chat()
    qtbot.waitUntil(lambda: not window.busy, timeout=5000)
    assert calls[1][1] == stored


def test_traditional_display_keeps_raw_and_existing_notes(window, tmp_path):
    cue = Cue("simplified", 1, 2, "多谢", tokens=[Token("多谢", "do1 ze6")])
    doc = StudyDocument(str(tmp_path / "video.mkv"), LanguagePair(), [cue])
    window.media_path = tmp_path / "video.mkv"
    window.store.save_note(str(window.media_path), cue, "已有笔记", doc.language)
    window.set_document(doc)
    window.sync_cue(1500)
    assert window.active_cue.text == "多謝"
    assert window.active_cue.tokens[0].text == "多謝"
    assert window.active_cue.original_text == "多谢"
    assert window.companion.note.toPlainText() == "已有笔记"


def test_chat_markdown_formats_text_without_html_resources_or_link_navigation(window, tmp_path):
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill("red")
    local_image = tmp_path / "private.png"
    image.save(str(local_image))
    local_url = QUrl.fromLocalFile(str(local_image)).toString()
    window.companion.messages = [
        {"role": "user", "content": "- 一个问题"},
        {"role": "assistant", "content": "**要点**\n\n- 原句\n- 例子\n\n"
         f"<script>ignored</script>\n\n<img src=\"{local_url}\">\n\n"
         f"![blocked]({local_url})\n\n[链接](https://example.invalid)"},
    ]
    window.companion.render_conversation()
    widget = window.companion.chat_log
    doc = widget.document()
    assert "**要点**" not in doc.toPlainText()
    assert "要点" in doc.toPlainText()
    assert "<script>" not in doc.toHtml()
    block = doc.begin()
    bold_text = False
    while block.isValid():
        if block.text() in ("你", "伴侣"):
            assert block.textList() is None
        fragment_iterator = block.begin()
        while not fragment_iterator.atEnd():
            fragment = fragment_iterator.fragment()
            if "要点" in fragment.text():
                bold_text = fragment.charFormat().fontWeight() > QFont.Weight.Normal
            fragment_iterator += 1
        block = block.next()
    assert bold_text
    for resource in (local_url, "https://example.invalid/picture.png", "data:image/png;base64,AAAA"):
        assert doc.resource(QTextDocument.ResourceType.ImageResource.value, QUrl(resource)) is None
    assert not widget.textInteractionFlags() & Qt.TextInteractionFlag.LinksAccessibleByMouse
    assert not widget.textInteractionFlags() & Qt.TextInteractionFlag.LinksAccessibleByKeyboard


def test_long_jyutping_wraps_without_expanding_companion_window(window, tmp_path, qtbot):
    tokens = [Token("廣東話學習者", "gwong2 dung1 waa2 hok6 zaap6 ze2") for _ in range(6)]
    doc = StudyDocument(str(tmp_path / "video.mkv"), LanguagePair(),
                        [Cue("long", 0, 5, "".join(token.text for token in tokens), tokens=tokens)])
    window.set_document(doc)
    window.sync_cue(1000)
    window.companion.resize(520, 800)
    window.companion.show()
    qtbot.waitUntil(lambda: window.companion.isVisible())
    QApplication.processEvents()
    assert window.companion.width() <= 520
    for tile in window.companion._token_buttons:
        lines = tile.text().splitlines()
        assert len(lines) == 3  # Original word followed by two readable Jyutping lines.
        assert all(len(line.split()) <= 3 for line in lines[1:])
        assert tile.width() >= tile.fontMetrics().horizontalAdvance(max(lines, key=len))
    if window.companion.token_area.width() > window.companion.token_scroll.viewport().width():
        assert window.companion.token_scroll.horizontalScrollBar().maximum() > 0


def test_one_companion_request_runs_during_preparation_without_releasing_busy_controls(window, qtbot, monkeypatch):
    finish_preparation = threading.Event()
    finish_reply = threading.Event()
    reply_entered = threading.Event()
    calls = []

    def preparation(progress, cancelled):
        progress("正在准备测试")
        while not finish_preparation.wait(0.01):
            if cancelled():
                return

    class Tutor:
        def __init__(self, provider, model):
            self.model = model

        def reply(self, message, history, language, current_cue=None, context=None):
            calls.append(message)
            reply_entered.set()
            finish_reply.wait(2)
            return "可以边等待边查看操作说明。"

    monkeypatch.setattr("tingmu.companion.CompanionAgent", Tutor)
    window.start_job("准备测试", preparation, lambda result: None)
    qtbot.waitUntil(lambda: window.progress_label.text() == "正在准备测试", timeout=5000)
    window.companion.chat_input.setText("等待时可以做什么？")
    window.companion.send_chat()
    qtbot.waitUntil(reply_entered.is_set, timeout=5000)
    assert len(window._jobs) == 2
    window.companion.chat_input.setText("第二个问题")
    window.companion.send_chat()
    assert len(calls) == 1
    assert len(window.companion.messages) == 1
    assert window.progress_label.text() == "正在准备测试"
    finish_reply.set()
    qtbot.waitUntil(lambda: not window.companion._chat_in_flight, timeout=5000)
    assert window.busy
    assert not window.prepare_button.isEnabled()
    assert not window.config_widget.isEnabled()
    assert window.progress_label.text() == "正在准备测试"
    finish_preparation.set()
    qtbot.waitUntil(lambda: not window.busy, timeout=5000)
    assert window.prepare_button.isEnabled()
    assert window.config_widget.isEnabled()


@pytest.mark.parametrize("model", ["gpt-6-astra", "gpt-6.1-sol"])
def test_notebook_review_uses_strong_companion_model_and_safe_markdown(window, tmp_path, qtbot, monkeypatch, model):
    errors = []
    monkeypatch.setattr(window, "show_error", errors.append)
    window.media_path = tmp_path / "video.mkv"
    cue = Cue("saved", 1, 2, "多謝")
    window.store.save_word(str(window.media_path), cue, Token("多謝", "do1 ze6"), window.current_language())
    window.provider_combo.setCurrentIndex(window.provider_combo.findData("local"))
    window.companion_model_combo.setCurrentIndex(window.companion_model_combo.findData(model))
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill("red")
    local_image = tmp_path / "private.png"
    image.save(str(local_image))
    local_url = QUrl.fromLocalFile(str(local_image)).toString()
    calls = []

    class ReviewAgent:
        def __init__(self, provider, requested_model):
            calls.append((provider, requested_model))

        def summarize(self, entries, language):
            assert len(entries) == 1
            return f"**复习**\n\n- 多謝\n\n![blocked]({local_url})\n\n<img src=\"{local_url}\">"

    monkeypatch.setattr("tingmu.ui.app.LearningAgent", ReviewAgent)
    window.open_notebook()
    notebook = window._notebook
    window.cloud_check.setChecked(False)
    notebook.summarize()
    assert calls == []
    window.cloud_check.setChecked(True)
    notebook.summarize()
    qtbot.waitUntil(lambda: not window.busy, timeout=5000)
    assert errors == []
    assert calls == [("openai", model)]
    doc = notebook.review.document()
    assert "复习" in doc.toPlainText()
    assert "**复习**" not in doc.toPlainText()
    for resource in (local_url, "https://example.invalid/image.png", "data:image/png;base64,AAAA"):
        assert doc.resource(QTextDocument.ResourceType.ImageResource.value, QUrl(resource)) is None
    assert not notebook.review.textInteractionFlags() & Qt.TextInteractionFlag.LinksAccessibleByMouse
    assert not notebook.review.textInteractionFlags() & Qt.TextInteractionFlag.LinksAccessibleByKeyboard
    assert "对话模型" in notebook.summarize_button.toolTip()


def test_alignment_warning_is_visible_without_changing_cue_timing_or_selection(window, tmp_path):
    audit_path = str(tmp_path / "alignment-audit.json")
    doc = document(tmp_path, transcript_spec={"asr_model": "qwen3-asr-1.7b"}, stage="ready",
                   alignment_warnings=[{"start": 2550.0, "duration": 24.2973125, "error": "cannot align"}],
                   asr_audit_path=audit_path)
    original = doc.to_dict()
    window.set_document(doc)
    window.sync_cue(1500)
    assert window.active_cue.id == "a"
    assert [(cue.id, cue.start, cue.end) for cue in window.cues] == [("a", 1, 2), ("b", 3, 4)]
    assert doc.to_dict() == original
    assert "1 段未对齐" in window.document_status(doc)
    for tooltip in (window.progress_label.toolTip(), window.companion.provenance.toolTip()):
        assert "未能可靠对齐" in tooltip
        assert "42:30–42:54" in tooltip
        assert "原始审计已保留" in tooltip
        assert audit_path in tooltip
    window.set_document(document(tmp_path))
    window.sync_cue(1500)
    assert "未对齐" not in window.document_status(window.document)
    assert window.progress_label.toolTip() == ""
    assert audit_path not in window.companion.provenance.toolTip()


def test_workspace_has_independent_named_movable_docks_and_menubar(window, qtbot):
    from PySide6.QtWidgets import QDockWidget
    assert [action.text() for action in window.menuBar().actions()] == ["文件", "播放", "学习", "布局", "设置", "帮助", "关于"]
    assert len({dock.objectName() for dock in window.docks}) == 7
    for dock in window.docks:
        assert dock.features() & QDockWidget.DockWidgetFeature.DockWidgetMovable
        assert dock.features() & QDockWidget.DockWidgetFeature.DockWidgetFloatable
    assert window.chat_dock.widget() is window.companion.chat_page
    assert [window.companion.tabs.tabText(i) for i in range(window.companion.tabs.count())] == ["讲解", "笔记"]
    window.chat_dock.setFloating(True)
    window.chat_dock.show()
    qtbot.waitUntil(window.chat_dock.isFloating)
    window.companion.ask_current_sentence()
    assert window.chat_dock.isVisible()
    assert window.companion.chat_input.text()


def test_workspace_layout_roundtrip_preserves_floating_and_tabs(window, qtbot):
    window.library_dock.show()
    window.queue_dock.show()
    window.tabifyDockWidget(window.library_dock, window.queue_dock)
    window.chat_dock.setFloating(True)
    window.chat_dock.resize(480, 320)
    window.chat_dock.show()
    QApplication.processEvents()
    window.overlay_enabled.setChecked(False)
    window.save_workspace()
    window.apply_layout_preset("cinema")
    assert not window.companion_dock.isVisible()
    assert not window.chat_dock.isFloating()
    assert window.restore_workspace()
    assert window.chat_dock.isFloating()
    assert window.queue_dock in window.tabifiedDockWidgets(window.library_dock)
    assert not window.overlay_enabled.isChecked()
    window.apply_layout_preset("study")
    assert window.companion_dock.isVisible()
    assert window.subtitle_dock.isVisible()
    assert window.chat_dock.isVisible()


def test_fullscreen_is_video_only_with_configurable_overlay(window, tmp_path, qtbot):
    window.set_document(document(tmp_path))
    window.sync_cue(1500)
    window.toggle_fullscreen()
    QApplication.processEvents()
    assert window._fullscreen
    assert window.fullscreen_surface.isFullScreen()
    assert window.video.parentWidget() is window.fullscreen_surface
    assert window.fullscreen_surface.layout().count() == 2
    assert window.fullscreen_surface.layout().itemAt(0).widget() is window.video
    assert window.fullscreen_surface.layout().itemAt(1).widget() is window.fullscreen_surface.controls
    assert window.menuBar().parentWidget() is window
    assert window.overlay.isVisible()
    assert window.overlay.sentence.text() == "你好"
    window.overlay_font.setValue(28)
    window.overlay_opacity.setValue(62)
    assert "28px" in window.overlay.sentence.styleSheet()
    assert "158" in window.overlay.panel.styleSheet()
    window.overlay_enabled.setChecked(False)
    assert not window.overlay.isVisible()
    window.toggle_fullscreen()
    assert not window.fullscreen_surface.isVisible()
    assert window.video.parentWidget() is window.player_widget
    assert window.player_widget.layout().indexOf(window.video) == 1
    assert window.read_settings().overlay_font_size == 28


def test_subtitle_search_click_seeks_original_acoustic_start(window, tmp_path, monkeypatch):
    window.set_document(document(tmp_path))
    window.subtitle_search.setText("do1")
    assert window.subtitle_list.item(0).isHidden()
    item = window.subtitle_list.item(1)
    assert not item.isHidden()
    positions = []
    monkeypatch.setattr(window.player, "setPosition", positions.append)
    window.offset_spin.setValue(500)
    window.seek_subtitle_item(item)
    assert positions[-1] == 3500
    assert window.companion.cue.id == "b"
    assert window.subtitle_list.currentItem() is item
    window.subtitle_search.clear()
    assert not window.subtitle_list.item(0).isHidden()


def test_queue_uses_real_engine_worker_and_keeps_selected_media(window, tmp_path, qtbot, monkeypatch):
    first, second = tmp_path / "first.mp4", tmp_path / "second.mp4"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    calls = []

    def prepare(media_path, settings, store, **kwargs):
        calls.append((Path(media_path), QThread.currentThread()))
        doc = StudyDocument(str(media_path), settings.language, [Cue("a", 0, 1, "你好")], metadata={"stage": "ready"})
        store.save_document(doc)
        return doc

    from pathlib import Path
    monkeypatch.setattr("tingmu.batch.prepare_media", prepare)
    window.media_path = first
    window.add_queue_paths([first, second, first])
    assert window.queue_list.count() == 2
    window.start_queue()
    qtbot.waitUntil(lambda: not window.busy, timeout=5000)
    assert [path for path, _ in calls] == [first, second]
    assert all(thread != QApplication.instance().thread() for _, thread in calls)
    assert all(row["status"] == "completed" for row in window.preparation_queue.snapshot())
    assert window.media_path == first
    assert window.document is not None


def test_fullscreen_escape_and_overlay_drag(window, qtbot):
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QEvent
    window.toggle_fullscreen()
    QApplication.processEvents()
    overlay = window.overlay
    origin = overlay.pos()
    local = QPointF(20, 20)
    global_point = QPointF(overlay.mapToGlobal(QPoint(20, 20)))
    press = QMouseEvent(QEvent.Type.MouseButtonPress, local, global_point,
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    move = QMouseEvent(QEvent.Type.MouseMove, local + QPointF(80, -30), global_point + QPointF(80, -30),
                       Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    overlay.mousePressEvent(press)
    overlay.mouseMoveEvent(move)
    assert overlay.pos() == origin + QPoint(80, -30)
    window.fullscreen_surface.activateWindow()
    QApplication.processEvents()
    qtbot.keyClick(window.fullscreen_surface, Qt.Key.Key_Escape)
    assert not window._fullscreen
    assert window.video.parentWidget() is window.player_widget


def test_independent_learning_notes_keep_source_document_unchanged(window, tmp_path):
    window.media_path = tmp_path / "grouped.mkv"
    doc = StudyDocument(str(window.media_path), LanguagePair(), [
        Cue("one", 1, 1.4, "多謝", tokens=[Token("多謝", "do1 ze6")]),
        Cue("two", 1.45, 2, "你", tokens=[Token("你", "nei5")]),
    ])
    original = doc.to_dict()
    window.set_document(doc)
    assert len(window.cues) == 2
    window.sync_cue(1200)
    assert window.companion.cue.text == "多謝"
    assert window.timeline.source_ids(window.companion.cue.id) == ("one",)
    window.companion.note.setPlainText("连起来读")
    window.companion.save_note()
    window.set_document(doc)
    window.sync_cue(1200)
    assert window.companion.note.toPlainText() == "连起来读"
    assert doc.to_dict() == original
    window.reading_spins["subtitle_min_ms"].setValue(0)
    assert len(window.cues) == 2
    assert doc.to_dict() == original


def test_review_provenance_names_actual_model_and_group_reasons(window, tmp_path):
    doc = StudyDocument(str(tmp_path / "reviewed.mkv"), LanguagePair(), [
        Cue("one", 1, 1.4, "多謝", uncertain=True), Cue("two", 1.45, 2, "你"),
    ], metadata={"review_stage": "ready", "review_spec": {"model": "gpt-6-astra"},
                 "annotation_spec": {"review": {"model": "gpt-6-astra"}},
                 "review_records": {"one": {"reason": "保留口语原文"}, "two": {"reason": "上下文用字一致"}},
                 "transcript_spec": {"asr_model": "qwen3-asr-1.7b"}})
    window.set_document(doc)
    window.sync_cue(1200)
    assert "Astra 文本复核" in window.companion.provenance.text()
    assert "待核对" not in window.subtitle_label.text()
    tooltip = window.companion.provenance.toolTip()
    assert "保留口语原文" in tooltip
    assert "上下文用字一致" not in tooltip
    window.sync_cue(1500)
    assert "上下文用字一致" in window.companion.provenance.toolTip()
    assert "不代表人工听音核验" in tooltip


@pytest.mark.parametrize("annotation_spec", [{}, {"review": None}])
def test_inactive_cached_review_does_not_label_current_raw_text(window, tmp_path, annotation_spec):
    doc = document(tmp_path, review_stage="ready", review_spec={"model": "gpt-6-astra"},
                   review_completed_ids=["a", "b"], reviewed_count=2,
                   review_records={"a": {"reason": "Cached review reason"}},
                   annotation_spec=annotation_spec)
    window.set_document(doc)
    window.sync_cue(1500)
    assert "文本复核" not in window.companion.provenance.text()
    assert "Cached review reason" not in window.companion.provenance.toolTip()
    assert "文本复核" not in window.document_status(doc)
    assert doc.metadata["review_stage"] == "ready"  # Cache is kept for re-enabling.


def test_independent_note_reloads_and_keeps_legacy_group_note(window, tmp_path, qtbot):
    media = tmp_path / "grouped-reopen.mkv"
    doc = StudyDocument(str(media), LanguagePair(), [Cue("one", 1, 1.4, "多謝"), Cue("two", 1.45, 2, "你")])
    window.media_path = media
    legacy = Cue("display:legacy", 1, 2, "多謝你")
    window.store.save_note(str(media), legacy, "以前的合并笔记", doc.language)
    window.set_document(doc)
    window.sync_cue(1200)
    group_id = window.companion.cue.id
    window.companion.note.setPlainText("连读笔记")
    window.companion.save_note()
    window.store.save_document(doc)
    reopened = MainWindow(Store(window.store.data_dir), restore_library=False)
    qtbot.addWidget(reopened)
    try:
        reopened.media_path = media
        reopened.set_document(reopened.store.load_document(str(media), doc.language))
        reopened.sync_cue(1200)
        assert reopened.companion.cue.id == group_id
        assert reopened.companion.note.toPlainText() == "连读笔记"
        reopened.reading_spins["subtitle_min_ms"].setValue(0)
        reopened.sync_cue(1200)
        assert reopened.companion.cue.id == "one"
        assert reopened.companion.note.toPlainText() == "连读笔记"
        assert len(reopened.store.entries(str(media))) == 2
        assert any(e["note"] == "以前的合并笔记" for e in reopened.store.entries(str(media)))
    finally:
        reopened.close()


def test_fullscreen_controls_share_player_seek_and_reappear_on_mouse(window, qtbot, monkeypatch):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QMouseEvent
    player = window.player
    toggles, seeks = [], []
    monkeypatch.setattr(window, "toggle_playback", lambda: toggles.append("toggle"))
    monkeypatch.setattr(player, "setPosition", seeks.append)
    monkeypatch.setattr(player, "playbackState", lambda: QMediaPlayer.PlaybackState.PlayingState)
    window.toggle_fullscreen()
    controls = window.fullscreen_surface
    QApplication.processEvents()
    controls.update_duration(120000)
    controls.update_position(30000)
    controls.update_playback_state(QMediaPlayer.PlaybackState.PlayingState)
    assert controls.position_label.text() == "00:30"
    assert controls.duration_label.text() == "02:00"
    assert controls.play_button.text() == "暂停"
    qtbot.mouseClick(controls.play_button, Qt.MouseButton.LeftButton)
    assert toggles == ["toggle"]
    qtbot.mouseClick(controls.seek_slider, Qt.MouseButton.LeftButton,
                    pos=QPoint(controls.seek_slider.width() // 2, controls.seek_slider.height() // 2))
    assert abs(seeks[-1] - 60000) <= 100
    monkeypatch.setattr(controls.controls, "underMouse", lambda: False)
    controls.hide_idle_controls()
    assert controls.controls.isHidden()
    event = QMouseEvent(QEvent.Type.MouseMove, QPointF(50, 50), QPointF(50, 50),
                        Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(window.video, event)
    assert not controls.controls.isHidden()
    controls.update_playback_state(QMediaPlayer.PlaybackState.PausedState)
    assert controls.play_button.text() == "播放"
    qtbot.mouseClick(controls.exit_button, Qt.MouseButton.LeftButton)
    assert not window._fullscreen
    assert window.player is player
    assert not controls.idle_timer.isActive()


def test_overlay_explanation_toggle_persists_with_layout(window, tmp_path):
    doc = document(tmp_path)
    doc.cues[0].explanation = "这是一句问候语。"
    window.set_document(doc)
    window.sync_cue(1500)
    assert window.overlay.explanation.isHidden()
    window.overlay_explanation.setChecked(True)
    assert not window.overlay.explanation.isHidden()
    assert window.overlay.explanation.text() == "这是一句问候语。"
    window.save_workspace()
    window.overlay_explanation.setChecked(False)
    assert window.overlay.explanation.isHidden()
    assert window.restore_workspace()
    assert window.overlay_explanation.isChecked()
    window.toggle_fullscreen()
    QApplication.processEvents()
    assert window.overlay.explanation.isVisible()
    window.toggle_fullscreen()
