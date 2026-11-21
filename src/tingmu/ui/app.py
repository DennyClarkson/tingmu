"""Qt desktop UI. Media stays local; expensive preparation runs in workers."""

from __future__ import annotations

import bisect
import copy
import math
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Callable

from shiboken6 import isValid

from PySide6.QtCore import QEvent, QObject, QThread, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import (
    QActionGroup, QColor, QCloseEvent, QFont, QKeyEvent, QTextBlockFormat,
    QTextCharFormat, QTextCursor, QTextDocument,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractSpinBox, QApplication, QCheckBox, QComboBox,
    QDialog, QDockWidget, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)

from tingmu.agent import LearningAgent, provider_status
from tingmu.media import MEDIA_EXTENSIONS, SUBTITLE_EXTENSIONS, load_subtitles, save_srt, scan_media
from tingmu.models import AppSettings, Cue, LanguagePair, StudyDocument, Token
from tingmu.pipeline import prepare_media
from tingmu.storage import Store
from tingmu.ui.workspace import WorkspaceMixin
from tingmu.timeline import SubtitleTimeline


STYLE = """
QWidget { background: #151719; color: #ECEAE4; font-family: 'Microsoft YaHei UI'; font-size: 13px; }
QMainWindow, QDialog { background: #151719; }
QLabel#muted { color: #92988F; }
QLabel#subtitle { font-size: 21px; padding: 12px; background: #1B1E20; }
QLabel#sentence { font-size: 20px; line-height: 1.5; padding: 8px; }
QLabel#section { color: #86B99A; font-size: 12px; font-weight: bold; padding-top: 16px; padding-bottom: 5px; }
QLabel#companionTitle { font-size: 16px; font-weight: bold; }
QTabWidget::pane { border: 0; border-top: 1px solid #343A37; }
QTabBar::tab { color: #92988F; background: #151719; padding: 10px 22px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: #ECEAE4; border-bottom-color: #86B99A; }
QMenuBar { background: #1B1E20; padding: 3px; }
QMenuBar::item { padding: 6px 12px; background: transparent; }
QMenuBar::item:selected { background: #355043; }
QMenu { background: #1B1E20; border: 1px solid #343A37; padding: 6px; }
QMenu::item { padding: 7px 26px; }
QMenu::item:selected { background: #355043; }
QPushButton, QComboBox, QSpinBox, QLineEdit { background: #24282A; border: 1px solid #3B4240; border-radius: 5px; padding: 6px; }
QPushButton:hover { border-color: #86B99A; }
QPushButton:pressed, QPushButton:checked { background: #355043; }
QPushButton:disabled { color: #68716C; border-color: #2C3230; }
QPushButton#primary { background: #86B99A; color: #151719; font-weight: bold; }
QTextEdit, QPlainTextEdit, QListWidget, QTableWidget { background: #1B1E20; border: 1px solid #343A37; border-radius: 5px; selection-background-color: #355043; }
QHeaderView::section { background: #24282A; color: #92988F; padding: 6px; border: 0; }
QProgressBar { border: 1px solid #343A37; border-radius: 3px; text-align: center; }
QProgressBar::chunk { background: #86B99A; }
QSlider::groove:horizontal { height: 4px; background: #343A37; }
QSlider::sub-page:horizontal { background: #86B99A; }
QSlider::handle:horizontal { background: #ECEAE4; width: 12px; margin: -4px 0; border-radius: 6px; }
QDockWidget::title { padding: 8px; background: #1B1E20; }
QToolTip { color: #ECEAE4; background: #24282A; border: 1px solid #86B99A; }
"""

LANGUAGES = [("普通话", "zh-CN"), ("粤语", "yue"), ("英语", "en"),
             ("日语", "ja"), ("韩语", "ko"), ("法语", "fr"),
             ("德语", "de"), ("西班牙语", "es"), ("自动识别", "auto")]
MEDIA_FILTER = "音视频 (*.mkv *.mp4 *.mov *.avi *.webm *.m4v *.wmv *.mp3 *.wav *.m4a *.flac *.ogg);;所有文件 (*)"
SUBTITLE_FILTER = "字幕 (*.srt *.ass *.ssa *.vtt);;所有文件 (*)"


def clock_text(milliseconds: int) -> str:
    seconds = max(0, milliseconds // 1000)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def alignment_notice(document: StudyDocument | None) -> str:
    if document is None:
        return ""
    warnings = document.metadata.get("alignment_warnings", [])
    if not isinstance(warnings, list) or not warnings:
        return ""
    details = [f"{len(warnings)} 段未能可靠对齐，字幕可能缺少这些片段。"]
    ranges = []
    for warning in warnings[:3]:
        if not isinstance(warning, dict):
            continue
        start, duration = warning.get("start"), warning.get("duration")
        if (isinstance(start, (float, int)) and isinstance(duration, (float, int))
                and math.isfinite(start) and math.isfinite(duration) and duration >= 0):
            ranges.append(f"{clock_text(round(start * 1000))}–{clock_text(round((start + duration) * 1000))}")
    if ranges:
        details.append("片段：" + "、".join(ranges) + (" 等" if len(warnings) > 3 else ""))
    audit_path = document.metadata.get("asr_audit_path")
    if audit_path:
        details.append(f"原始审计已保留：{audit_path}")
    return "\n".join(details)


def button(text: str, callback: Callable, tip: str = "") -> QPushButton:
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    if tip:
        widget.setToolTip(tip)
    return widget


def language_combo(value: str) -> QComboBox:
    combo = QComboBox()
    combo.setEditable(True)
    for label, code in LANGUAGES:
        combo.addItem(label, code)
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)
    else:
        combo.setEditText(value)
    combo.setToolTip("选择语言，或输入语言代码；已知语言和学习语言可以相同")
    return combo


def language_value(combo: QComboBox) -> str:
    index = combo.currentIndex()
    if index >= 0 and combo.currentText() == combo.itemText(index):
        return str(combo.itemData(index))
    return combo.currentText().strip() or "zh-CN"


class Worker(QObject):
    result = Signal(int, object)
    error = Signal(str)
    progress = Signal(str)
    finished = Signal()

    def __init__(self, job_id: int, function: Callable):
        super().__init__()
        self.job_id = job_id
        self.function = function
        self.cancel_event = threading.Event()

    @Slot()
    def run(self):
        try:
            value = self.function(self.progress.emit, self.cancel_event.is_set)
            self.result.emit(self.job_id, value)
        except Exception as exc:
            if self.cancel_event.is_set():
                self.progress.emit("已取消；已完成的处理由流水线保留")
            else:
                self.error.emit(f"{type(exc).__name__}: {exc}")
        finally:
            self.finished.emit()


class SeekSlider(QSlider):
    """Scrub directly to the pointer, including clicks outside the handle."""

    def _set_from_pointer(self, event):
        ratio = max(0.0, min(1.0, event.position().x() / max(1, self.width())))
        self.setValue(round(self.minimum() + ratio * (self.maximum() - self.minimum())))
        self.sliderMoved.emit(self.value())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.setSliderDown(True)
            self._set_from_pointer(event)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.isSliderDown():
            self._set_from_pointer(event)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.isSliderDown():
            self._set_from_pointer(event)
            self.setSliderDown(False)
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class TextOnlyDocument(QTextDocument):
    """Rich text formatting without loading any local, remote, or data resources."""

    def loadResource(self, resource_type: int, url: QUrl):
        return None


def render_markdown_sections(widget: QTextEdit, sections: list[tuple[str, str]]):
    """Render model text without HTML, navigation, or resource loading."""
    # Attached QTextEdit Markdown import can copy image resources despite
    # loadResource(None). Parse detached and remove image objects before display.
    document = TextOnlyDocument()
    document.setDefaultFont(widget.font())
    cursor = QTextCursor(document)
    heading_format = QTextCharFormat()
    heading_format.setForeground(QColor("#86B99A"))
    heading_format.setFontWeight(QFont.Weight.Bold)
    heading_block = QTextBlockFormat()
    heading_block.setTopMargin(16)
    heading_block.setBottomMargin(6)
    body_format = QTextCharFormat()
    body_format.setForeground(QColor("#ECEAE4"))
    body_block = QTextBlockFormat()
    body_block.setBottomMargin(12)
    features = QTextDocument.MarkdownFeature.MarkdownDialectGitHub | QTextDocument.MarkdownFeature.MarkdownNoHTML
    for title, content in sections:
        if title:
            cursor.insertBlock(heading_block, heading_format)
            cursor.insertText(title)
        cursor.insertBlock(body_block, body_format)
        cursor.insertMarkdown(content, features)
    images = []
    block = document.begin()
    while block.isValid():
        fragment_iterator = block.begin()
        while not fragment_iterator.atEnd():
            fragment = fragment_iterator.fragment()
            if fragment.charFormat().isImageFormat():
                images.append((fragment.position(), fragment.length()))
            fragment_iterator += 1
        block = block.next()
    for position, length in reversed(images):
        cursor.setPosition(position)
        cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        cursor.removeSelectedText()
    previous = widget.document()
    document.setParent(widget)
    widget.setDocument(document)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                   | Qt.TextInteractionFlag.TextSelectableByKeyboard)
    if isValid(previous):
        previous.deleteLater()


class CompanionWindow(QWidget):
    """A separate, synchronized reading surface; notes survive cue changes."""

    def __init__(self, owner: MainWindow):
        super().__init__(None)
        self.owner = owner
        self.setWindowTitle("听幕 · 学习伴侣")
        self.resize(620, 800)
        self.cue: Cue | None = None
        self.token: Token | None = None
        self.drafts: dict[str, str] = {}
        self._draft_sets: dict[tuple, dict[str, str]] = {}
        self._document_key: tuple | None = None
        self._token_buttons: list[QPushButton] = []
        self.messages: list[dict] = []
        self._chat_key: tuple | None = None
        self._chat_in_flight = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)
        header = QHBoxLayout()
        title = QLabel("学习伴侣")
        title.setObjectName("companionTitle")
        header.addWidget(title)
        self.context = QLabel("")
        self.context.setObjectName("muted")
        self.context.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.context, 1)
        header.addWidget(button("生词本", owner.open_notebook))
        layout.addLayout(header)
        self.sentence = QLabel("等待字幕")
        self.sentence.setObjectName("sentence")
        self.sentence.setWordWrap(True)
        self.sentence.setTextFormat(Qt.TextFormat.PlainText)
        self.sentence.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.sentence)
        self.provenance = QLabel("")
        self.provenance.setWordWrap(True)
        self.provenance.setObjectName("muted")
        self.provenance.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.provenance)
        scroll = QScrollArea()
        self.token_scroll = scroll
        scroll.viewport().installEventFilter(self)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(150)
        scroll.setMaximumHeight(220)
        self.token_area = QWidget()
        self.token_grid = QGridLayout(self.token_area)
        self.token_grid.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        scroll.setWidget(self.token_area)
        layout.addWidget(scroll)
        selected_row = QHBoxLayout()
        self.selected = QLabel("选择词语")
        self.selected.setWordWrap(True)
        self.selected.setTextFormat(Qt.TextFormat.PlainText)
        selected_row.addWidget(self.selected, 1)
        self.word_save = button("收藏词", self.save_word)
        self.word_save.setEnabled(False)
        selected_row.addWidget(self.word_save)
        layout.addLayout(selected_row)
        navigation = QHBoxLayout()
        navigation.addWidget(button("上一句", lambda: owner.adjacent_cue(-1)))
        navigation.addWidget(button("重听", owner.replay_sentence, "从当前句开头播放"))
        self.repeat = button("单句循环", owner.toggle_sentence_loop)
        self.repeat.setCheckable(True)
        navigation.addWidget(self.repeat)
        navigation.addWidget(button("下一句", lambda: owner.adjacent_cue(1)))
        layout.addLayout(navigation)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setDrawBase(False)
        layout.addWidget(self.tabs, 1)
        explanation_page = QWidget()
        explanation_layout = QVBoxLayout(explanation_page)
        explanation_layout.setContentsMargins(0, 14, 0, 0)
        self.analysis = QTextEdit()
        self.analysis.setReadOnly(True)
        self.analysis.setMinimumHeight(110)
        self.analysis.setPlaceholderText("翻译与讲解")
        self.analysis.setToolTip("准备学习后显示翻译与讲解；本地模式仅提供可核查的词典内容")
        explanation_layout.addWidget(self.analysis, 1)
        explanation_actions = QHBoxLayout()
        explanation_actions.addWidget(button("问伴侣", self.ask_current_sentence))
        explanation_actions.addWidget(button("收藏句子", self.save_sentence))
        explanation_actions.addStretch()
        explanation_layout.addLayout(explanation_actions)
        self.tabs.addTab(explanation_page, "讲解")
        chat_page = QWidget()
        chat_layout = QVBoxLayout(chat_page)
        chat_layout.setContentsMargins(0, 14, 0, 0)
        self.chat_log = QTextEdit()
        self.chat_log.setReadOnly(True)
        self.chat_log.setDocument(TextOnlyDocument(self.chat_log))
        self.chat_log.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                             | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.chat_log.setPlaceholderText("问原句、练表达，或了解听幕怎么用。")
        chat_layout.addWidget(self.chat_log, 1)
        self.chat_status = QLabel("")
        self.chat_status.setWordWrap(True)
        self.chat_status.setObjectName("muted")
        self.chat_status.setTextFormat(Qt.TextFormat.PlainText)
        chat_layout.addWidget(self.chat_status)
        chat_row = QHBoxLayout()
        self.chat_input = QLineEdit()
        self.chat_input.setPlaceholderText("问这句，或问怎么用听幕")
        self.chat_input.returnPressed.connect(self.send_chat)
        self.chat_send = button("发送", self.send_chat)
        self.chat_send.setObjectName("primary")
        chat_row.addWidget(self.chat_input, 1)
        chat_row.addWidget(self.chat_send)
        chat_layout.addLayout(chat_row)
        self.chat_page = chat_page
        notes_page = QWidget()
        notes_layout = QVBoxLayout(notes_page)
        notes_layout.setContentsMargins(0, 14, 0, 0)
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText("句子笔记")
        notes_layout.addWidget(self.note, 1)
        saves = QHBoxLayout()
        saves.addWidget(button("收藏句子", self.save_sentence))
        saves.addWidget(button("保存笔记", self.save_note))
        notes_layout.addLayout(saves)
        self.tabs.addTab(notes_page, "笔记")
        self.feedback = QLabel("")
        self.feedback.setObjectName("muted")
        layout.addWidget(self.feedback)
        self.refresh_conversation()

    def refresh_conversation(self):
        media = str(self.owner.media_path) if self.owner.media_path else ""
        language = self.owner.document_language()
        key = (media, language.source, language.target, language.level)
        if key == self._chat_key:
            return
        self._chat_key = key
        self.messages = self.owner.store.load_conversation(media, language)
        self.chat_status.clear()
        self.render_conversation()

    def render_conversation(self):
        render_markdown_sections(self.chat_log, [
            ("你" if message.get("role") == "user" else "伴侣", str(message.get("content", "")))
            for message in self.messages
        ])
        self.chat_log.verticalScrollBar().setValue(self.chat_log.verticalScrollBar().maximum())

    def ask_current_sentence(self):
        self.owner.show_chat()
        self.chat_input.setText("请解释当前这句的表达和用法。" if self.cue else "怎么用听幕开始学习？")
        self.chat_input.setFocus()

    def send_chat(self):
        message = self.chat_input.text().strip()
        if not message:
            return
        settings = self.owner.read_settings()
        if not settings.cloud_text_enabled:
            self.chat_status.setText("请先在设置开启云端文本分析")
            return
        if self._chat_in_flight:
            self.chat_status.setText("正在回复，请稍候")
            return
        self.refresh_conversation()
        key = self._chat_key
        media = str(self.owner.media_path) if self.owner.media_path else ""
        language = copy.deepcopy(self.owner.document_language())
        cue = copy.deepcopy(self.cue)
        history = [dict(row) for row in self.messages]
        pending = history + [{"role": "user", "content": message}]
        self.messages = pending
        self.owner.store.save_conversation(media, language, pending)
        self.render_conversation()
        self.chat_input.clear()
        self._chat_in_flight = True
        self.chat_send.setEnabled(False)
        self.chat_status.setText("正在思考…")
        context = {"media_name": self.owner.media_path.name if self.owner.media_path else "",
                   "position_seconds": self.owner.player.position() / 1000,
                   "subtitle_source": "external" if self.owner.subtitle_path else "asr" if self.owner.document else "none",
                   "cloud_text_enabled": settings.cloud_text_enabled,
                   "available_controls": ["打开视频", "视频目录", "准备学习", "设置", "更多", "导入字幕",
                                          "单句循环", "A-B 循环", "收藏词", "保存笔记", "生词本", "自动连播"]}

        def work(progress, cancelled):
            try:
                from tingmu.companion import CompanionAgent
                if cancelled():
                    return None
                agent = CompanionAgent(settings.companion_provider, settings.companion_model or None)
                response = agent.reply(message, history, language, current_cue=cue, context=context)
                if cancelled():
                    return None
                completed = pending + [{"role": "assistant", "content": response}]
                self.owner.store.save_conversation(media, language, completed)
                return {"messages": completed, "model": agent.model}
            except Exception as exc:
                return {"error": str(exc)}

        def completed(result):
            if self._chat_key != key:
                return
            if result is None:
                self.chat_status.setText("已取消")
            elif "error" in result:
                self.chat_status.setText(result["error"])
            else:
                self.messages = result["messages"]
                self.render_conversation()
                self.chat_status.setText(result["model"])

        self.owner.start_job("伴侣对话", work, completed, self._chat_settled, allow_parallel=True)

    def _chat_settled(self):
        self._chat_in_flight = False
        self.chat_send.setEnabled(True)

    def reset_document(self):
        if self.cue:
            self.drafts[self.cue.id] = self.note.toPlainText()
        self.cue = None
        self.drafts = {}
        self._document_key = None
        self.note.clear()
        self.set_cue(None, force=True)

    def set_document_context(self, key: tuple):
        if key == self._document_key:
            return
        if self.cue:
            self.drafts[self.cue.id] = self.note.toPlainText()
        self.cue = None
        self.note.clear()
        self._document_key = key
        self.drafts = self._draft_sets.setdefault(key, {})

    def set_cue(self, cue: Cue | None, force: bool = False):
        if not force and ((self.cue is None and cue is None) or
                          (self.cue and cue and self.cue.id == cue.id)):
            return
        if self.cue:
            self.drafts[self.cue.id] = self.note.toPlainText()
        self.cue = cue
        self.token = None
        self.word_save.setEnabled(False)
        self.feedback.clear()
        self.selected.setText("选择词语" if cue else "")
        while self.token_grid.count():
            item = self.token_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._token_buttons.clear()
        if cue is None:
            self.sentence.setText("暂无字幕")
            self.context.clear()
            self.provenance.clear()
            self.provenance.setToolTip("")
            self.analysis.clear()
            self.note.clear()
            self.note.setEnabled(False)
            return
        self.note.setEnabled(True)
        self.sentence.setText(cue.text)
        self.context.setText(f"{clock_text(int(cue.start * 1000))} — {clock_text(int(cue.end * 1000))}")
        document = self.owner.document
        metadata = document.metadata if document else {}
        spec = metadata.get("transcript_spec", {})
        external = bool(metadata.get("subtitle_path") or spec.get("subtitle"))
        machine = bool(spec.get("asr_model")) and not external
        origin = "外部字幕" if external else "机器转写" if machine else "字幕"
        source = document.provider if document else "local"
        if source == "local":
            analysis_source = "分词：本地" if cue.tokens else "讲解：未生成"
        elif metadata.get("stage") in ("partial", "annotating", "transcribed") and not all(source_id in metadata.get("completed_ids", [])
                for source_id in self.owner.timeline.source_ids(cue.id) or (cue.id,)):
            analysis_source = "讲解：待处理"
        else:
            model = f"/{document.model}" if document and document.model else ""
            analysis_source = f"讲解：{source}{model}"
        details = [origin, analysis_source]
        notice = alignment_notice(document)
        tips = ["文字来源与分析记录；未经人工逐句听音确认。"]
        if notice:
            tips.append(notice)
        source_ids = self.owner.timeline.source_ids(cue.id) or (cue.id,)
        active_review = metadata.get("annotation_spec", {}).get("review")
        if active_review and metadata.get("review_stage") == "ready":
            review_spec = metadata.get("review_spec", {})
            review_model = review_spec.get("model", "未记录")
            model_label = {"gpt-6-astra": "Astra", "gpt-6.1-sol": "Sol"}.get(review_model, review_model)
            details.insert(1, f"{model_label} 文本复核")
            tips.append(f"文本复核模型：{review_model}；仅基于文本，不代表人工听音核验。")
            records = metadata.get("review_records", {})
            for source_id in source_ids:
                reason = records.get(source_id, {}).get("reason")
                if reason:
                    tips.append(f"复核说明：{reason}")
        if machine:
            tips.append(f"语音模型：{spec.get('asr_model', '未知')}")
        if machine and document and document.language.target.startswith("yue"):
            tips.append("粤语可能被规范成书面中文；读音与讲解针对转写文字。")
        if cue.uncertain:
            tips.append("此句保留来源中的低置信度标记。")
        if cue.original_text and cue.original_text != cue.text:
            label = "原始字幕" if external else "原始转写"
            tips.append(f"{label}：{cue.original_text}")
        self.provenance.setText(" · ".join(details))
        self.provenance.setToolTip("\n".join(tips))
        tokens = cue.tokens or [Token(text=cue.text)]
        for index, token in enumerate(tokens):
            syllables = token.reading.split()
            reading = "\n".join(" ".join(syllables[start:start + 3]) for start in range(0, len(syllables), 3))
            label = token.text + (f"\n{reading}" if reading else "")
            tile = button(label, lambda checked=False, t=token: self.select_token(t))
            tile.setMinimumWidth(70)
            tile.setCheckable(True)
            tile.setToolTip(token.meaning or "尚无释义；可收藏后复习")
            tile.ensurePolished()
            tile.setMinimumWidth(max(70, max(tile.fontMetrics().horizontalAdvance(line)
                                             for line in label.splitlines()) + 20))
            self._token_buttons.append(tile)
        self.reflow_tokens()
        self.analysis.setPlainText("\n\n".join(part for part in (
            f"翻译\n{cue.translation}" if cue.translation else "",
            f"讲解\n{cue.explanation}" if cue.explanation else "",
        ) if part))
        self.note.setPlainText(self.drafts.get(cue.id, ""))

    def reflow_tokens(self, available_width=None):
        if not self._token_buttons:
            return
        width = max(tile.minimumWidth() for tile in self._token_buttons)
        available = max(1, (available_width or self.token_scroll.viewport().width()) - 24)
        columns = max(1, min(5, available // (width + self.token_grid.horizontalSpacing())))
        while self.token_grid.count():
            self.token_grid.takeAt(0)
        for index, tile in enumerate(self._token_buttons):
            self.token_grid.addWidget(tile, index // columns, index % columns)

    def eventFilter(self, watched, event):
        if watched is self.token_scroll.viewport() and event.type() == QEvent.Type.Resize:
            self.reflow_tokens(event.size().width())
        return super().eventFilter(watched, event)

    def select_token(self, token: Token):
        self.token = token
        self.owner.player.pause()
        tokens = self.cue.tokens or [token] if self.cue else []
        for tile, candidate in zip(self._token_buttons, tokens):
            tile.setChecked(candidate is token)
        self.selected.setText(" · ".join(part for part in
                              (token.text, token.reading, token.meaning or "尚无释义") if part))
        self.word_save.setEnabled(True)

    def save_word(self):
        if self.cue and self.token and self.owner.media_path:
            self.owner.store.save_word(str(self.owner.media_path), self.cue, self.token, self.owner.document_language())
            self.feedback.setText("已收藏词语")
            self.owner.refresh_notebook()

    def save_sentence(self):
        if self.cue and self.owner.media_path:
            note = self.note.toPlainText().strip()
            if not note:
                note = next((entry.get("note", "") for entry in self.owner.store.entries(str(self.owner.media_path))
                             if entry.get("kind") == "note" and entry.get("cue_id") == self.cue.id
                             and entry.get("source_language") == self.owner.document_language().source
                             and entry.get("target_language") == self.owner.document_language().target), "")
            self.owner.store.save_note(str(self.owner.media_path), self.cue, note, self.owner.document_language())
            self.feedback.setText("已收藏句子")
            self.owner.refresh_notebook()

    def save_note(self):
        if not self.cue or not self.owner.media_path:
            return
        note = self.note.toPlainText().strip()
        if not note:
            self.feedback.setText("先写下笔记，或使用收藏句子")
            return
        self.owner.store.save_note(str(self.owner.media_path), self.cue, note, self.owner.document_language())
        self.drafts[self.cue.id] = note
        self.feedback.setText("笔记已保存")
        self.owner.refresh_notebook()

    def closeEvent(self, event: QCloseEvent):
        if self.cue:
            self.drafts[self.cue.id] = self.note.toPlainText()
        event.accept()


class NotebookDialog(QDialog):
    def __init__(self, owner: MainWindow):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("听幕 · 生词与笔记")
        self.resize(950, 650)
        self.rows: list[dict] = []
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.current_only = QCheckBox("仅当前视频")
        self.current_only.setChecked(True)
        self.current_only.toggled.connect(self.refresh)
        toolbar.addWidget(self.current_only)
        toolbar.addWidget(button("刷新", self.refresh))
        toolbar.addStretch()
        toolbar.addWidget(button("导出 CSV", lambda: self.export("csv")))
        toolbar.addWidget(button("导出 Markdown", lambda: self.export("md")))
        toolbar.addWidget(button("删除所选", self.delete_selected))
        layout.addLayout(toolbar)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["类型", "词语 / 句子", "读音", "释义 / 笔记", "时间", "视频"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.cellDoubleClicked.connect(self.jump_to_entry)
        self.table.setToolTip("双击回到对应视频与时间")
        layout.addWidget(self.table, 2)
        review_row = QHBoxLayout()
        self.summarize_button = button("伴侣复盘", self.summarize)
        review_row.addWidget(self.summarize_button)
        self.summary_status = QLabel("")
        self.summarize_button.setToolTip("使用设置中的对话模型（Astra / Sol）复盘；仅发送本页收藏文本")
        self.summary_status.setWordWrap(True)
        self.summary_status.setObjectName("muted")
        review_row.addWidget(self.summary_status, 1)
        layout.addLayout(review_row)
        self.review = QTextEdit()
        self.review.setReadOnly(True)
        self.review.setPlaceholderText("复盘")
        self.review.setToolTip("复盘结果自动保存到本地数据目录")
        layout.addWidget(self.review, 1)
        self.refresh()

    def media_filter(self) -> str | None:
        return str(self.owner.media_path) if self.current_only.isChecked() and self.owner.media_path else None

    def refresh(self):
        self.rows = self.owner.store.entries(self.media_filter())
        self.table.setRowCount(len(self.rows))
        for index, entry in enumerate(self.rows):
            values = ["词语" if entry.get("kind") == "word" else "笔记", entry.get("text") or entry.get("sentence", ""),
                      entry.get("reading", ""), entry.get("note") or entry.get("meaning", ""),
                      clock_text(int(float(entry.get("start", 0)) * 1000)), Path(entry.get("media_path", "")).name]
            for col, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setToolTip(str(value))
                self.table.setItem(index, col, cell)
        self.table.setColumnWidth(1, 230)
        self.table.setColumnWidth(3, 260)

    def delete_selected(self):
        indices = {item.row() for item in self.table.selectedItems()}
        if not indices:
            return
        if QMessageBox.question(self, "删除收藏", f"删除选中的 {len(indices)} 条收藏？") != QMessageBox.StandardButton.Yes:
            return
        for index in indices:
            self.owner.store.delete_entry(int(self.rows[index]["id"]))
        self.refresh()

    def jump_to_entry(self, row: int, _column: int):
        entry = self.rows[row]
        path = Path(entry["media_path"])
        position = int(float(entry.get("start", 0)) * 1000)
        if self.owner.media_path != path:
            self.owner.open_media(path, position=position)
        else:
            self.owner.player.setPosition(position)
        self.owner.player.play()

    def export(self, kind: str):
        from tingmu.export_paths import export_path
        default = export_path(self.owner.store.data_dir, f"听幕收藏.{kind}", (".md", ".csv"))
        path, _ = QFileDialog.getSaveFileName(self, "导出收藏", str(default),
                                             "CSV (*.csv)" if kind == "csv" else "Markdown (*.md)")
        if not path:
            return
        try:
            path = export_path(self.owner.store.data_dir, path, (".md", ".csv"))
            if kind == "csv":
                self.owner.store.export_csv(Path(path), self.media_filter())
            else:
                self.owner.store.export_markdown(Path(path), self.media_filter())
            self.summary_status.setText(f"已导出：{path}")
        except Exception as exc:
            self.owner.show_error(str(exc))

    def summarize(self):
        if not self.rows:
            self.summary_status.setText("先收藏词语或句子")
            return
        settings = self.owner.read_settings()
        if not settings.cloud_text_enabled:
            self.summary_status.setText("请先在设置开启云端文本分析")
            return
        if self.owner.busy:
            self.summary_status.setText("请等待当前任务完成")
            return
        rows = list(self.rows)
        language = self.owner.current_language()
        media = self.media_filter() or "all"
        self.summarize_button.setEnabled(False)
        self.summary_status.setText("正在生成复盘…")

        def work(progress, cancelled):
            progress("正在根据收藏生成复盘；仅发送文本")
            if cancelled():
                return None
            result = LearningAgent(settings.companion_provider, settings.companion_model or "gpt-6-astra").summarize(rows, language)
            if cancelled():
                return None
            path = self.owner.store.save_review(media, result)
            return result, path

        def done(result):
            if result:
                render_markdown_sections(self.review, [("", result[0])])
                self.summary_status.setText(f"已保存：{result[1]}")

        self.owner.start_job("复盘", work, done, lambda: self.summarize_button.setEnabled(True))


class MainWindow(WorkspaceMixin, QMainWindow):
    def __init__(self, store: Store, *, restore_library: bool = True):
        super().__init__()
        self.store = store
        self.settings = store.load_settings()
        self.media_path: Path | None = None
        self.document: StudyDocument | None = None
        self.subtitle_path: Path | None = None
        self.cues: list[Cue] = []
        self.cue_starts: list[float] = []
        self.timeline = SubtitleTimeline([])
        self.active_cue: Cue | None = None
        self._last_cue_id: str | None = None
        self._resume_position = 0
        self._resume_pending = False
        self._tracks_initialized = False
        self._last_saved_position = -10000
        self._jobs: dict[int, tuple[QThread, Worker]] = {}
        self._job_callbacks: dict[int, tuple[Callable, Callable | None]] = {}
        self._job_counter = 0
        self._closing = False
        self._fullscreen = False
        self._library_visible = True
        self._settings_visible = False
        self._sentence_loop: Cue | None = None
        self._ab_a: int | None = None
        self._ab_b: int | None = None
        self._notebook: NotebookDialog | None = None
        self._auto_advance = False
        self.setWindowTitle("听幕")
        self.resize(1500, 920)
        self.setMinimumSize(860, 600)
        self.setStyleSheet(STYLE)
        self.setAcceptDrops(True)
        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.audio_output.setVolume(0.8)
        self.player.setAudioOutput(self.audio_output)
        self._build_ui()
        self.companion = CompanionWindow(self)
        self.companion.setStyleSheet(STYLE)
        self.build_workspace()
        self.player.positionChanged.connect(self.on_position)
        self.player.durationChanged.connect(self.on_duration)
        self.player.playbackStateChanged.connect(self.on_playback_state)
        self.player.mediaStatusChanged.connect(self.on_media_status)
        self.player.tracksChanged.connect(self.update_tracks)
        self.player.errorOccurred.connect(lambda _error, message: self.show_error(message))
        for combo in (self.source_combo, self.target_combo):
            combo.activated.connect(self.reload_cached_document)
            combo.lineEdit().editingFinished.connect(self.reload_cached_document)
            combo.currentTextChanged.connect(self.refresh_language_label)
        self.level_combo.activated.connect(self.reload_cached_document)
        self.asr_audio_spin.valueChanged.connect(self.reload_cached_document)
        self.statusBar().showMessage("本地播放 · 文本分析按需开启")
        self._autosave = QTimer(self)
        self._autosave.setInterval(5000)
        self._autosave.timeout.connect(self.save_position)
        self._autosave.start()
        QApplication.instance().installEventFilter(self)
        if restore_library and self.settings.library_path and Path(self.settings.library_path).is_dir():
            QTimer.singleShot(0, lambda: self.load_library(Path(self.settings.library_path)))

    @property
    def busy(self) -> bool:
        return bool(self._jobs)

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 10, 12, 10)
        header = QHBoxLayout()

        self.media_title = QLabel("听幕")
        self.media_title.setTextFormat(Qt.TextFormat.PlainText)
        header.addWidget(self.media_title, 1)
        self.library_toggle = button("目录", lambda: self.library_dock.setVisible(not self.library_dock.isVisible()))
        self.library_toggle.hide()
        self.language_button = button("", self.toggle_settings)
        header.addWidget(self.language_button)
        self.prepare_button = button("准备学习", self.prepare)
        self.prepare_button.setObjectName("primary")
        header.addWidget(self.prepare_button)

        self.more_button = QPushButton("更多")
        self.more_button.hide()
        layout.addLayout(header)
        self.config_widget = QWidget()
        config = QFormLayout(self.config_widget)
        config.setContentsMargins(14, 4, 14, 14)
        config.setHorizontalSpacing(12)
        config.setVerticalSpacing(10)
        config.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        config.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)

        def section(text):
            label = QLabel(text)
            label.setObjectName("section")
            config.addRow(label)

        section("学习")
        self.source_combo = language_combo(self.settings.source_language)
        self.target_combo = language_combo(self.settings.target_language)
        self.level_combo = QComboBox()
        for label, value in [("入门", "beginner"), ("进阶", "intermediate"), ("熟练", "advanced")]:
            self.level_combo.addItem(label, value)
        self.level_combo.setCurrentIndex(max(0, self.level_combo.findData(self.settings.level)))
        config.addRow("已知语言", self.source_combo)
        config.addRow("学习语言", self.target_combo)
        config.addRow("程度", self.level_combo)
        section("文本与对话")
        self.provider_combo = QComboBox()
        for label, value in [("DeepSeek", "deepseek"), ("OpenAI", "openai"), ("本地", "local")]:
            self.provider_combo.addItem(label, value)
        self.provider_combo.setCurrentIndex(max(0, self.provider_combo.findData(self.settings.provider)))
        self.model_edit = QLineEdit(self.settings.model)
        self.model_edit.setPlaceholderText("模型名")
        self.model_edit.setToolTip("批量字幕讲解模型；与伴侣对话模型独立")
        config.addRow("字幕服务", self.provider_combo)
        config.addRow("字幕模型", self.model_edit)
        self.companion_model_combo = QComboBox()
        self.companion_model_combo.addItem("Astra", "gpt-6-astra")
        self.companion_model_combo.addItem("Sol", "gpt-6.1-sol")
        companion_model = self.settings.companion_model or "gpt-6-astra"
        companion_index = self.companion_model_combo.findData(companion_model)
        if companion_index < 0:
            self.companion_model_combo.addItem(companion_model, companion_model)
            companion_index = self.companion_model_combo.count() - 1
        self.companion_model_combo.setCurrentIndex(companion_index)
        self.companion_model_combo.setToolTip("OpenAI 对话伴侣：gpt-6-astra / gpt-6.1-sol；不改变批量字幕模型")
        config.addRow("对话模型", self.companion_model_combo)
        self.cloud_check = QCheckBox("云端文本分析")
        self.cloud_check.setChecked(self.settings.cloud_text_enabled)
        self.cloud_check.setToolTip("只发送字幕与收藏文本；音视频不上传")
        config.addRow(self.cloud_check)
        section("本地识别")
        self.asr_combo = QComboBox()
        self.asr_combo.setEditable(True)
        self.asr_combo.addItems(["qwen3-asr-1.7b", "qwen3-asr-0.6b", "large-v3-turbo", "large-v3", "medium", "small", "base", "tiny"])
        self.asr_combo.setCurrentText(self.settings.asr_model)
        self.asr_combo.setMinimumWidth(self.asr_combo.fontMetrics().horizontalAdvance("large-v3-turbo") + 65)
        self.asr_combo.lineEdit().setCursorPosition(0)
        self.asr_combo.setToolTip("本地语音识别模型；首次使用需要下载模型")
        self.device_combo = QComboBox()
        self.device_combo.addItems(["auto", "cuda", "cpu"])
        self.device_combo.setCurrentText(self.settings.device)
        self.asr_audio_spin = QSpinBox()
        self.asr_audio_spin.setRange(0, 99)
        self.asr_audio_spin.setValue(self.settings.audio_track)
        self.asr_audio_spin.setToolTip("识别使用的音轨序号，从 0 开始；与下方播放音轨对应")
        config.addRow("模型", self.asr_combo)
        config.addRow("设备", self.device_combo)
        config.addRow("识别音轨", self.asr_audio_spin)
        self.provider_combo.currentIndexChanged.connect(self.on_provider_changed)
        self.refresh_language_label()
        self.video = QVideoWidget()
        self.video.setMinimumHeight(230)
        self.video.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.player.setVideoOutput(self.video)
        layout.addWidget(self.video, 1)
        self.subtitle_label = QLabel("")
        self.subtitle_label.setToolTip("支持 SRT / ASS / VTT 字幕，也可在本机识别视频音轨")
        self.subtitle_label.setObjectName("subtitle")
        self.subtitle_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setTextFormat(Qt.TextFormat.PlainText)
        self.subtitle_label.setMinimumHeight(58)
        layout.addWidget(self.subtitle_label)
        seek_row = QHBoxLayout()
        self.position_label = QLabel("00:00")
        self.duration_label = QLabel("00:00")
        self.seek_slider = SeekSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.sliderMoved.connect(lambda value: self.position_label.setText(clock_text(value)))
        self.seek_slider.sliderReleased.connect(lambda: self.player.setPosition(self.seek_slider.value()))
        seek_row.addWidget(self.position_label)
        seek_row.addWidget(self.seek_slider, 1)
        seek_row.addWidget(self.duration_label)
        layout.addLayout(seek_row)
        controls = QHBoxLayout()
        self.play_button = button("播放", self.toggle_playback, "空格")
        controls.addWidget(self.play_button)
        controls.addWidget(button("−10 秒", lambda: self.seek_relative(-10000), "←"))
        controls.addWidget(button("+10 秒", lambda: self.seek_relative(10000), "→"))
        self.speed_combo = QComboBox()
        for rate in [0.5, 0.65, 0.75, 0.85, 1.0, 1.15, 1.25, 1.5, 1.75, 2.0]:
            self.speed_combo.addItem(f"{rate:g}×", rate)
        self.speed_combo.setCurrentIndex(4)
        self.speed_combo.currentIndexChanged.connect(lambda: self.player.setPlaybackRate(float(self.speed_combo.currentData())))
        controls.addWidget(self.speed_combo)
        self.mute_button = button("静音", self.toggle_mute, "M")
        self.mute_button.setCheckable(True)
        controls.addWidget(self.mute_button)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(80)
        self.volume_slider.setMaximumWidth(100)
        self.volume_slider.valueChanged.connect(lambda value: self.audio_output.setVolume(value / 100))
        controls.addWidget(self.volume_slider)
        controls.addStretch()
        self.a_button = button("设 A", self.set_a, "[：设置循环起点")
        self.b_button = button("设 B", self.set_b, "]：设置循环终点并开启循环")
        self.a_button.hide()
        self.b_button.hide()

        controls.addWidget(button("全屏", self.toggle_fullscreen, "F；Esc 退出"))
        layout.addLayout(controls)
        section("播放与字幕")
        self.audio_combo = QComboBox()
        self.audio_combo.setMinimumWidth(130)
        self.audio_combo.currentIndexChanged.connect(self.change_audio_track)
        self.embedded_combo = QComboBox()
        self.embedded_combo.setMinimumWidth(120)
        self.embedded_combo.currentIndexChanged.connect(self.change_subtitle_track)
        config.addRow("播放音轨", self.audio_combo)
        config.addRow("内嵌字幕", self.embedded_combo)
        self.offset_spin = QSpinBox()
        self.offset_spin.setRange(-600000, 600000)
        self.offset_spin.setSingleStep(100)
        self.offset_spin.setSuffix(" ms")
        self.offset_spin.setValue(self.settings.subtitle_offset_ms)
        self.offset_spin.setToolTip("正值使外部学习字幕晚出现；不影响内嵌字幕")
        self.offset_spin.valueChanged.connect(lambda: self.sync_cue(self.player.position()))
        config.addRow("字幕延迟", self.offset_spin)
        subtitle_actions = QHBoxLayout()
        subtitle_actions.addWidget(button("导入字幕", self.import_subtitles))
        subtitle_actions.addWidget(button("导出 SRT", self.export_subtitles))
        config.addRow(subtitle_actions)
        self.settings_dock = QDockWidget("设置", self)
        self.settings_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea | Qt.DockWidgetArea.LeftDockWidgetArea)
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(self.config_widget)
        self.settings_dock.setWidget(settings_scroll)
        self.settings_dock.setMinimumWidth(310)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.settings_dock)
        self.settings_dock.hide()
        progress_row = QHBoxLayout()
        self.progress_label = QLabel("就绪")
        self.progress_label.setObjectName("muted")
        self.progress_label.setWordWrap(True)
        self.progress_label.setTextFormat(Qt.TextFormat.PlainText)
        progress_row.addWidget(self.progress_label, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximumWidth(140)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        progress_row.addWidget(self.progress_bar)
        self.cancel_button = button("取消", self.cancel_jobs)
        self.cancel_button.setEnabled(False)
        progress_row.addWidget(self.cancel_button)
        layout.addLayout(progress_row)
        self.library_dock = QDockWidget("视频目录", self)
        self.library_dock.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        library_widget = QWidget()
        library_layout = QVBoxLayout(library_widget)
        library_layout.addWidget(button("选择目录", self.choose_folder))
        self.folder_label = QLabel(self.settings.library_path or "尚未选择目录")
        self.folder_label.setWordWrap(True)
        self.folder_label.setTextFormat(Qt.TextFormat.PlainText)
        self.folder_label.setObjectName("muted")
        library_layout.addWidget(self.folder_label)
        self.library_list = QListWidget()
        self.library_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.library_list.itemDoubleClicked.connect(lambda item: self.open_media(Path(item.data(Qt.ItemDataRole.UserRole))))
        library_layout.addWidget(self.library_list)
        self.library_dock.setWidget(library_widget)
        self.library_dock.setMinimumWidth(210)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.library_dock)
        self.library_dock.hide()
        self._build_player_menu()
        self.update_tracks()

    def _build_player_menu(self):
        self.player_menu = QMenu(self)
        self.previous_media_action = self.player_menu.addAction("上一视频")
        self.previous_media_action.triggered.connect(lambda: self.play_adjacent_media(-1))
        self.next_media_action = self.player_menu.addAction("下一视频")
        self.next_media_action.triggered.connect(lambda: self.play_adjacent_media(1))
        self.advance_action = self.player_menu.addAction("自动连播")
        self.advance_action.setCheckable(True)
        self.advance_action.toggled.connect(lambda checked: setattr(self, "_auto_advance", checked))
        self.player_menu.addSeparator()
        self.player_menu.addAction("导入字幕", self.import_subtitles)
        self.player_menu.addAction("导出 SRT", self.export_subtitles)
        self.player_menu.addAction("生词本", self.open_notebook)
        self.player_menu.addAction("设置", self.toggle_settings)
        self.player_menu.addSeparator()
        self.snapshot_action = self.player_menu.addAction("保存当前画面…")
        self.snapshot_action.triggered.connect(self.save_current_frame)
        aspect_menu = self.player_menu.addMenu("画面比例")
        self._aspect_group = QActionGroup(self)
        self._aspect_group.setExclusive(True)
        for title, mode in [("保持比例", Qt.AspectRatioMode.KeepAspectRatio),
                            ("填满画面", Qt.AspectRatioMode.KeepAspectRatioByExpanding)]:
            action = aspect_menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(mode == Qt.AspectRatioMode.KeepAspectRatio)
            action.triggered.connect(lambda checked=False, value=mode: self.video.setAspectRatioMode(value))
            self._aspect_group.addAction(action)
        self.player_menu.aboutToShow.connect(self.refresh_player_menu)
        self.more_button.setMenu(self.player_menu)
        self.video.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.video.customContextMenuRequested.connect(lambda position: self.player_menu.exec(self.video.mapToGlobal(position)))

    def refresh_player_menu(self):
        self.previous_media_action.setEnabled(not self.busy and self.adjacent_media_path(-1) is not None)
        self.next_media_action.setEnabled(not self.busy and self.adjacent_media_path(1) is not None)
        self.snapshot_action.setEnabled(self.video.videoSink().videoFrame().isValid())

    def toggle_settings(self):
        self.settings_dock.setVisible(not self.settings_dock.isVisible())

    def refresh_language_label(self, *_args):
        self.language_button.setText(f"{self.source_combo.currentText()} → {self.target_combo.currentText()}")
        self.language_button.setToolTip("学习语言与设置")

    def adjacent_media_path(self, direction: int) -> Path | None:
        paths = [Path(self.library_list.item(index).data(Qt.ItemDataRole.UserRole))
                 for index in range(self.library_list.count())]
        if not paths:
            return None
        try:
            index = paths.index(self.media_path)
        except ValueError:
            return paths[0] if direction > 0 else None
        adjacent = index + direction
        return paths[adjacent] if 0 <= adjacent < len(paths) else None

    def play_adjacent_media(self, direction: int, *, automatic: bool = False):
        if self.busy:
            self.statusBar().showMessage("请先完成或取消当前处理")
            return
        path = self.adjacent_media_path(direction)
        if path:
            self.open_media(path, position=0 if automatic else None)

    def save_current_frame(self):
        frame = self.video.videoSink().videoFrame()
        image = frame.toImage() if frame.isValid() else None
        if image is None or image.isNull():
            self.statusBar().showMessage("暂无可保存画面")
            return
        stem = self.media_path.stem if self.media_path else "听幕"
        timestamp = clock_text(self.player.position()).replace(":", "-")
        from tingmu.export_paths import export_path
        default = export_path(self.store.data_dir, f"{stem}-{timestamp}.png", (".png",))
        path, _ = QFileDialog.getSaveFileName(self, "保存当前画面", str(default), "PNG (*.png)")
        if not path:
            return
        if Path(path).suffix.lower() != ".png":
            path += ".png"
        try:
            path = str(export_path(self.store.data_dir, path, (".png",)))
        except ValueError as exc:
            self.show_error(str(exc))
            return
        if image.save(path, "PNG"):
            self.statusBar().showMessage(f"画面已保存：{path}")
        else:
            self.show_error("画面保存失败，请检查目标目录")

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if not self.busy and len(urls) == 1 and urls[0].isLocalFile():
            path = Path(urls[0].toLocalFile())
            if path.is_dir() or (path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS | SUBTITLE_EXTENSIONS):
                event.acceptProposedAction()
                return
        event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if len(urls) == 1 and urls[0].isLocalFile() and self.open_dropped_path(Path(urls[0].toLocalFile())):
            event.acceptProposedAction()
        else:
            event.ignore()

    def open_dropped_path(self, path: Path) -> bool:
        if self.busy:
            self.statusBar().showMessage("请先完成或取消当前处理")
            return False
        path = path.expanduser().resolve()
        if path.is_dir():
            self.load_library(path)
        elif path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS:
            self.open_media(path)
        elif path.is_file() and path.suffix.lower() in SUBTITLE_EXTENSIONS:
            if not self.media_path:
                self.statusBar().showMessage("请先打开对应的视频")
                return False
            self.load_subtitle_file(path)
        else:
            return False
        return True

    def read_settings(self) -> AppSettings:
        self.settings = replace(self.settings, source_language=language_value(self.source_combo),
                                target_language=language_value(self.target_combo), level=str(self.level_combo.currentData()),
                                provider=str(self.provider_combo.currentData()), model=self.model_edit.text().strip(),
                                asr_model=self.asr_combo.currentText().strip() or "large-v3-turbo",
                                device=self.device_combo.currentText(), audio_track=self.asr_audio_spin.value(),
                                subtitle_offset_ms=self.offset_spin.value(), cloud_text_enabled=self.cloud_check.isChecked(),
                                companion_provider="openai", companion_model=str(self.companion_model_combo.currentData()))
        options = {"overlay_opacity": self.overlay_opacity.value() / 100,
                   "overlay_font_size": self.overlay_font.value(),
                   "review_enabled": self.review_check.isChecked(),
                   "review_model": self.review_model_edit.text().strip() or "gpt-6-astra"}
        options.update({name: spin.value() for name, spin in self.reading_spins.items()})
        for name, value in options.items():
            if hasattr(self.settings, name):
                setattr(self.settings, name, value)
        return self.settings

    def current_language(self) -> LanguagePair:
        return LanguagePair(language_value(self.source_combo), language_value(self.target_combo), str(self.level_combo.currentData()))

    def document_language(self) -> LanguagePair:
        return self.document.language if self.document else self.current_language()

    def on_provider_changed(self):
        details = provider_status().get(str(self.provider_combo.currentData()), {})
        self.model_edit.setText(details.get("default_model", ""))

    def choose_media(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开本地视频", self.settings.library_path, MEDIA_FILTER)
        if path:
            self.open_media(Path(path))

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择视频目录", self.settings.library_path)
        if folder:
            self.load_library(Path(folder))

    def load_library(self, folder: Path):
        if self.busy:
            self.statusBar().showMessage("请等待当前任务完成后再扫描目录")
            return
        self.settings.library_path = str(folder)
        self.store.save_settings(self.read_settings())
        self.folder_label.setText(str(folder))
        self.library_dock.show()

        def completed(paths):
            self.library_list.clear()
            for path in paths:
                item = QListWidgetItem(Path(path).name)
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                item.setToolTip(str(path))
                self.library_list.addItem(item)
            self.statusBar().showMessage(f"目录中找到 {len(paths)} 个音视频文件；双击播放")

        self.start_job("扫描目录", lambda progress, cancelled: scan_media(folder), completed)

    def open_media(self, path: Path, position: int | None = None):
        path = path.expanduser().resolve()
        if not path.is_file():
            self.show_error(f"文件不存在：{path}")
            return
        if self.busy:
            self.statusBar().showMessage("请先完成或取消当前处理，再切换视频")
            return
        self.save_position()
        # stop() emits position/state changes; do not replace the saved resume point with zero.
        self._resume_pending = True
        self.player.stop()
        self.media_path = path
        for index in range(self.library_list.count()):
            if Path(self.library_list.item(index).data(Qt.ItemDataRole.UserRole)) == path:
                self.library_list.setCurrentRow(index)
                break
        self.subtitle_path = None
        self.clear_loops()
        self.companion.reset_document()
        self.document = None
        self.set_document(None)
        self.media_title.setText(path.name)
        self.media_title.setToolTip(str(path))
        self.setWindowTitle(f"{path.name} · 听幕")
        self._resume_position = position if position is not None else self.store.load_position(str(path))
        self._resume_pending = True
        self._last_saved_position = -10000
        self._tracks_initialized = False
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.reload_cached_document()
        self.player.play()

    def document_compatible(self, document: StudyDocument) -> bool:
        spec = document.metadata.get("transcript_spec", {})
        if document.language != self.current_language():
            return False
        # Imported subtitles are independent of audio selection; ASR caches are not.
        return bool(spec.get("subtitle")) or "audio_track" not in spec or spec["audio_track"] == self.asr_audio_spin.value()

    def document_status(self, document: StudyDocument) -> str:
        stage = document.metadata.get("stage", "ready")
        label = "已就绪" if stage == "ready" else "部分完成 · 可继续准备"
        count = document.metadata.get("annotated_count")
        detail = f" · 已分析 {count}/{len(document.cues)} 句" if count is not None and stage != "ready" else ""
        warnings = document.metadata.get("alignment_warnings", [])
        alignment = f" · {len(warnings)} 段未对齐" if isinstance(warnings, list) and warnings else ""
        active_review = document.metadata.get("annotation_spec", {}).get("review")
        reviewed = document.metadata.get("reviewed_count", 0) if active_review else 0
        review = f" · 文本复核 {reviewed}/{len(document.cues)}" if reviewed else ""
        return f"{label} · {len(document.cues)} 句 · {document.provider}{review}{detail}{alignment}"

    def reload_cached_document(self, *_args):
        if not self.media_path:
            self.companion.refresh_conversation()
            return
        if self.busy:
            if self.document and not self.document_compatible(self.document):
                self.clear_loops()
                self.set_document(None)
            return
        document = self.store.load_document(str(self.media_path), self.current_language())
        self.clear_loops()
        if document and self.document_compatible(document):
            self.set_document(document)
            self.progress_label.setText(self.document_status(document))
        else:
            self.set_document(None)
            self.progress_label.setText("暂无匹配字幕 · 导入字幕或准备学习")

    def set_document(self, document: StudyDocument | None):
        if document:
            from tingmu.language import learning_text, normalize_cues
            document = replace(document, cues=normalize_cues(document.cues, document.language, self.settings.target_script))
        self.document = document
        self.progress_label.setToolTip(alignment_notice(document))
        language = document.language if document else self.current_language()
        spec = document.metadata.get("transcript_spec", {}) if document else {}
        if document:
            subtitle_source = document.metadata.get("subtitle_path") or spec.get("subtitle")
            # Preserve the explicit source even if it is missing: preparation should report
            # that failure, rather than silently replacing imported subtitles with ASR.
            self.subtitle_path = Path(subtitle_source).expanduser() if subtitle_source else None
        key = (str(self.media_path or (document.media_path if document else "")), language.source,
               language.target, language.level, spec.get("audio_track", self.asr_audio_spin.value()))
        self.companion.set_document_context(key)
        self.timeline = SubtitleTimeline(document.cues if document else [],
            min_duration_ms=self.reading_spins["subtitle_min_ms"].value(),
            gap_fill_ms=self.reading_spins["subtitle_gap_ms"].value(),
            companion_hold_ms=self.reading_spins["companion_hold_ms"].value())
        self.cues = self.timeline.cues
        self.cue_starts = [cue.start for cue in self.cues]
        self.rebuild_subtitle_list()
        self._last_cue_id = None
        self.active_cue = None
        self.companion.set_cue(None, force=True)
        if document and self.media_path:
            cue_texts = {cue.id: cue.text for cue in self.cues}
            for entry in self.store.entries(str(self.media_path)):
                if (entry.get("kind") == "note" and entry.get("note")
                        and entry.get("source_language") == language.source
                        and entry.get("target_language") == language.target
                        and learning_text(str(entry.get("sentence", "")), language, self.settings.target_script)
                        == cue_texts.get(entry.get("cue_id"))):
                    self.companion.drafts.setdefault(str(entry.get("cue_id", "")), str(entry["note"]))
        self.sync_cue(self.player.position(), force=True)
        self.companion.refresh_conversation()

    def prepare(self):
        if not self.media_path:
            self.choose_media()
            if not self.media_path:
                return
        if self.busy:
            return
        settings = replace(self.read_settings())
        self.store.save_settings(settings)
        media = self.media_path
        subtitle = self.subtitle_path

        def work(progress, cancelled):
            try:
                return prepare_media(media, settings, self.store, subtitle_path=subtitle,
                                     progress=progress, cancelled=cancelled)
            except Exception:
                cached = self.store.load_document(str(media), settings.language)
                spec = cached.metadata.get("transcript_spec", {}) if cached else {}
                if (cached and cached.cues and cached.metadata.get("stage") in ("partial", "transcribed", "annotating")
                        and (spec.get("subtitle") or spec.get("audio_track") == settings.audio_track)):
                    # The pipeline checkpoints before cloud calls. Keep these usable on interruption.
                    return cached
                raise

        def completed(document):
            if document is None:
                return
            if not self.document_compatible(document):
                self.clear_loops()
                self.set_document(None)
                self.progress_label.setText("处理结果已保存；当前语言或音轨已改变，重新选择后可载入")
                return
            self.set_document(document)
            self.progress_label.setText(self.document_status(document))
            self.show_companion()

        self.start_job("准备学习", work, completed)

    def import_subtitles(self):
        if not self.media_path:
            self.statusBar().showMessage("请先打开对应的视频")
            return
        if self.busy:
            return
        path, _ = QFileDialog.getOpenFileName(self, "导入外部字幕", str(self.media_path.parent), SUBTITLE_FILTER)
        if not path:
            return
        self.load_subtitle_file(Path(path))

    def load_subtitle_file(self, subtitle: Path):
        if not self.media_path or self.busy:
            return
        subtitle = subtitle.expanduser().resolve()
        media = str(self.media_path)
        language = self.current_language()

        def work(progress, cancelled):
            progress("读取外部字幕；保留原始时间轴")
            cues = load_subtitles(subtitle)
            if not cues:
                raise ValueError("字幕文件中没有可用的句子")
            from tingmu.agent import annotate_local
            return StudyDocument(media, language, annotate_local(cues, language), provider="local", metadata={"subtitle_path": str(subtitle)})

        def completed(document):
            self.subtitle_path = subtitle
            self.store.save_document(document)
            self.set_document(document)
            self.progress_label.setText(f"已导入 {len(document.cues)} 句；准备学习可补充云端讲解")
            self.show_companion()

        self.start_job("导入字幕", work, completed)

    def export_subtitles(self):
        if not self.cues:
            self.statusBar().showMessage("当前没有可导出的学习字幕")
            return
        filename = (self.media_path.stem if self.media_path else "听幕") + ".srt"
        from tingmu.export_paths import export_path
        path, _ = QFileDialog.getSaveFileName(self, "导出学习字幕", str(export_path(self.store.data_dir, filename, (".srt",))), "SRT (*.srt)")
        if path:
            try:
                save_srt(self.document.cues if self.document else self.cues, export_path(self.store.data_dir, path, (".srt",)))
                self.statusBar().showMessage(f"字幕已导出：{path}（保留原始时间轴，未叠加播放延迟）")
            except Exception as exc:
                self.show_error(str(exc))

    def start_job(self, label: str, function: Callable, completed: Callable, settled: Callable | None = None,
                  *, allow_parallel: bool = False):
        if self.busy and not allow_parallel:
            return
        self._job_counter += 1
        job_id = self._job_counter
        thread = QThread(self)
        worker = Worker(job_id, function)
        worker.moveToThread(thread)
        self._jobs[job_id] = (thread, worker)
        self._job_callbacks[job_id] = (completed, settled)
        thread.setProperty("job_id", job_id)
        thread.started.connect(worker.run)
        if not allow_parallel:
            worker.progress.connect(self.progress_label.setText)
        worker.error.connect(self.show_error)
        worker.result.connect(self._job_result)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self._thread_finished)
        thread.finished.connect(thread.deleteLater)
        self.prepare_button.setEnabled(False)
        self.config_widget.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress_bar.setRange(0, 0)
        if not allow_parallel:
            self.progress_label.setText(f"{label}…")
        thread.start()

    @Slot(int, object)
    def _job_result(self, job_id: int, result):
        if not self._closing and job_id in self._job_callbacks:
            try:
                self._job_callbacks[job_id][0](result)
            except Exception as exc:
                self.show_error(f"处理结果未能载入：{exc}")

    @Slot()
    def _thread_finished(self):
        thread = self.sender()
        job_id = int(thread.property("job_id"))
        _, settled = self._job_callbacks.pop(job_id, (None, None))
        self._jobs.pop(job_id, None)
        self.prepare_button.setEnabled(not self.busy)
        self.config_widget.setEnabled(not self.busy)
        self.cancel_button.setEnabled(any(not worker.cancel_event.is_set() for _, worker in self._jobs.values()))
        if not self.busy:
            self.progress_bar.setRange(0, 1)
            self.progress_bar.setValue(1)
        if settled:
            settled()
        if self._closing and not self.busy:
            QTimer.singleShot(0, self.close)

    def cancel_jobs(self):
        for thread, worker in self._jobs.values():
            worker.cancel_event.set()
            thread.requestInterruption()
        self.cancel_button.setEnabled(False)
        self.progress_label.setText("正在取消；等待当前识别步骤或网络请求结束…")

    @Slot(str)
    def show_error(self, message: str):
        self.progress_label.setText(f"未完成：{message}")
        self.statusBar().showMessage(message, 15000)
        if not self._closing:
            QMessageBox.warning(self, "听幕", message)

    def on_duration(self, duration: int):
        self.seek_slider.setRange(0, max(0, duration))
        self.duration_label.setText(clock_text(duration))
        self._restore_position()

    def on_media_status(self, status: QMediaPlayer.MediaStatus):
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            self._restore_position()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._sentence_loop or self._ab_b is not None:
                start = self.cue_player_start(self._sentence_loop) if self._sentence_loop else self._ab_a or 0
                self.player.setPosition(start)
                self.player.play()
            elif self._auto_advance:
                self.play_adjacent_media(1, automatic=True)

    def _restore_position(self):
        if self._resume_pending and self.player.duration() > 0:
            duration = self.player.duration()
            position = min(max(0, self._resume_position), max(0, duration - 1000))
            self._resume_pending = False
            self.player.setPosition(position)

    def on_playback_state(self, state):
        self.play_button.setText("暂停" if state == QMediaPlayer.PlaybackState.PlayingState else "播放")
        if state != QMediaPlayer.PlaybackState.PlayingState:
            self.save_position()

    def on_position(self, position: int):
        if not self.seek_slider.isSliderDown():
            self.seek_slider.setValue(position)
        self.position_label.setText(clock_text(position))
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            if self._sentence_loop:
                start = self.cue_player_start(self._sentence_loop)
                end = int(self._sentence_loop.end * 1000) + self.offset_spin.value()
                if position >= end or position < start - 250:
                    self.player.setPosition(start)
                    return
            elif self._ab_a is not None and self._ab_b is not None:
                if position >= self._ab_b or position < self._ab_a - 250:
                    self.player.setPosition(self._ab_a)
                    return
        self.sync_cue(position)

    def sync_cue(self, position: int, force: bool = False):
        adjusted = position - self.offset_spin.value()
        subtitle = self.timeline.subtitle_at(adjusted)
        cue = self.timeline.companion_at(adjusted)
        self.subtitle_label.setText(subtitle.text if subtitle else "")
        cue_id = cue.id if cue else None
        self.active_cue = cue
        if force or cue_id != self._last_cue_id:
            self._last_cue_id = cue_id
            self.select_subtitle_cue(cue)
            self.overlay.set_cue(cue)
            self.companion.set_cue(cue, force=force)

    def cue_player_start(self, cue: Cue) -> int:
        return max(0, int(cue.start * 1000) + self.offset_spin.value())

    def toggle_playback(self):
        if not self.media_path:
            self.choose_media()
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def seek_relative(self, milliseconds: int):
        self.clear_loops()
        self.player.setPosition(max(0, min(self.player.duration(), self.player.position() + milliseconds)))

    def toggle_mute(self):
        muted = not self.audio_output.isMuted()
        self.audio_output.setMuted(muted)
        self.mute_button.setChecked(muted)
        self.mute_button.setText("取消静音" if muted else "静音")

    def adjacent_cue(self, direction: int):
        if not self.cues:
            return
        seconds = (self.player.position() - self.offset_spin.value()) / 1000
        index = bisect.bisect_right(self.cue_starts, seconds) - 1
        index = max(0, min(len(self.cues) - 1, index + direction))
        loop = bool(self._sentence_loop)
        self.clear_loops()
        cue = self.cues[index]
        if loop:
            self._sentence_loop = cue
            self.companion.repeat.setChecked(True)
        self.player.setPosition(self.cue_player_start(cue))
        self.sync_cue(self.cue_player_start(cue), force=True)
        self.player.play()

    def replay_sentence(self):
        cue = self.active_cue or self._sentence_loop
        if cue:
            self.player.setPosition(self.cue_player_start(cue))
            self.player.play()

    def toggle_sentence_loop(self):
        if self._sentence_loop:
            self.clear_loops()
        elif self.active_cue:
            self.clear_loops()
            self._sentence_loop = self.active_cue
            self.companion.repeat.setChecked(True)
            self.replay_sentence()
        else:
            self.companion.repeat.setChecked(False)
            self.statusBar().showMessage("当前时间没有可循环的学习字幕")

    def set_a(self):
        self.clear_loops()
        self._ab_a = self.player.position()
        self.a_button.setText(f"A {clock_text(self._ab_a)}")
        self.statusBar().showMessage("起点已设置；到终点时点击设 B")

    def set_b(self):
        if self._ab_a is None:
            self.statusBar().showMessage("请先设置 A 点")
            return
        position = self.player.position()
        if position - self._ab_a < 300:
            self.statusBar().showMessage("B 点需至少晚于 A 点 0.3 秒")
            return
        self._ab_b = position
        self.b_button.setText(f"B {clock_text(position)}")
        self.player.setPosition(self._ab_a)
        self.player.play()

    def clear_loops(self):
        self._sentence_loop = None
        self._ab_a = self._ab_b = None
        self.a_button.setText("设 A")
        self.b_button.setText("设 B")
        if hasattr(self, "companion"):
            self.companion.repeat.setChecked(False)

    def update_tracks(self):
        if not self._tracks_initialized and self.player.audioTracks():
            desired = min(self.asr_audio_spin.value(), len(self.player.audioTracks()) - 1)
            self._tracks_initialized = True
            self.player.setActiveAudioTrack(desired)
        self.audio_combo.blockSignals(True)
        self.audio_combo.clear()
        for index, metadata in enumerate(self.player.audioTracks()):
            self.audio_combo.addItem(self.track_label(metadata, index), index)
        if not self.audio_combo.count():
            self.audio_combo.addItem("无音轨", -1)
        self.audio_combo.setCurrentIndex(max(0, self.audio_combo.findData(self.player.activeAudioTrack())))
        self.audio_combo.blockSignals(False)
        self.embedded_combo.blockSignals(True)
        self.embedded_combo.clear()
        self.embedded_combo.addItem("关闭", -1)
        for index, metadata in enumerate(self.player.subtitleTracks()):
            self.embedded_combo.addItem(self.track_label(metadata, index), index)
        self.embedded_combo.setCurrentIndex(max(0, self.embedded_combo.findData(self.player.activeSubtitleTrack())))
        self.embedded_combo.blockSignals(False)

    @staticmethod
    def track_label(metadata: QMediaMetaData, index: int) -> str:
        title = metadata.stringValue(QMediaMetaData.Key.Title)
        language = metadata.stringValue(QMediaMetaData.Key.Language)
        description = " · ".join(value for value in (title, language) if value)
        return f"{index}: {description or '未标注'}"

    def change_audio_track(self):
        value = self.audio_combo.currentData()
        if value is not None and int(value) >= 0:
            self.player.setActiveAudioTrack(int(value))
            self.asr_audio_spin.setValue(int(value))

    def change_subtitle_track(self):
        value = self.embedded_combo.currentData()
        if value is not None:
            self.player.setActiveSubtitleTrack(int(value))

    def show_companion(self):
        self.companion_dock.show()
        self.companion_dock.raise_()

    def open_notebook(self):
        if self._notebook is None:
            self._notebook = NotebookDialog(self)
        self._notebook.refresh()
        self._notebook.show()
        self._notebook.raise_()
        self._notebook.activateWindow()

    def refresh_notebook(self):
        if self._notebook and self._notebook.isVisible():
            self._notebook.refresh()

    def save_position(self):
        if self.media_path and not self._resume_pending:
            position = self.player.position()
            if position != self._last_saved_position:
                self.store.save_position(str(self.media_path), position)
                self._last_saved_position = position

    def eventFilter(self, watched: QObject, event: QEvent):
        if (self._fullscreen and event.type() == QEvent.Type.MouseMove
                and isinstance(watched, QWidget)
                and (watched is self.fullscreen_surface or self.fullscreen_surface.isAncestorOf(watched))):
            self.fullscreen_surface.reveal_controls()
        if watched is self.video and event.type() == QEvent.Type.MouseButtonDblClick:
            self.toggle_fullscreen()
            return True
        if event.type() != QEvent.Type.KeyPress or not self.owns_active_window():
            return super().eventFilter(watched, event)
        key_event: QKeyEvent = event
        focus = QApplication.focusWidget()
        ancestor = focus
        while ancestor:
            if isinstance(ancestor, (QLineEdit, QTextEdit, QPlainTextEdit, QAbstractSpinBox)):
                return False
            ancestor = ancestor.parentWidget()
        key = key_event.key()
        modifiers = key_event.modifiers()
        if modifiers == Qt.KeyboardModifier.ControlModifier and key == Qt.Key.Key_O:
            self.choose_media()
            return True
        if modifiers not in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.KeypadModifier):
            return False
        actions = {Qt.Key.Key_Space: self.toggle_playback, Qt.Key.Key_Left: lambda: self.seek_relative(-10000),
                   Qt.Key.Key_Right: lambda: self.seek_relative(10000), Qt.Key.Key_F: self.toggle_fullscreen,
                   Qt.Key.Key_M: self.toggle_mute, Qt.Key.Key_BracketLeft: self.set_a,
                   Qt.Key.Key_BracketRight: self.set_b, Qt.Key.Key_R: self.replay_sentence,
                   Qt.Key.Key_Up: lambda: self.volume_slider.setValue(self.volume_slider.value() + 5),
                   Qt.Key.Key_Down: lambda: self.volume_slider.setValue(self.volume_slider.value() - 5)}
        if key == Qt.Key.Key_Escape and self._fullscreen:
            self.toggle_fullscreen()
            return True
        if key in actions:
            if isinstance(focus, QComboBox):
                return False
            actions[key]()
            return True
        return super().eventFilter(watched, event)

    def closeEvent(self, event: QCloseEvent):
        if self.busy:
            self._closing = True
            self.player.pause()
            self.cancel_jobs()
            self.progress_label.setText("正在结束后台任务，完成后自动关闭…")
            event.ignore()
            return
        if self._fullscreen:
            self.toggle_fullscreen()
        self.save_workspace()
        self.queue_timer.stop()
        self.overlay.close()
        self.fullscreen_surface.close()
        self._autosave.stop()
        self.save_position()
        self.store.save_settings(self.read_settings())
        self._resume_pending = True
        self.player.stop()
        self.companion.close()
        if self._notebook:
            self._notebook.close()
        QApplication.instance().removeEventFilter(self)
        event.accept()


def run_app(initial_path: str | None = None, data_dir: Path | None = None) -> int:
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("听幕")
    application.setOrganizationName("Tingmu")
    application.setFont(QFont("Microsoft YaHei UI", 10))
    window = MainWindow(Store(data_dir), restore_library=initial_path is None)
    window.show()
    # Keep the window alive when embedding the app in an existing interpreter.
    application._tingmu_window = window
    if initial_path:
        path = Path(initial_path)
        QTimer.singleShot(0, lambda: window.load_library(path) if path.is_dir() else window.open_media(path))
    return application.exec()
