"""Dock workspace and video-only fullscreen surfaces."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QByteArray, QPoint, QTimer, Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDockWidget, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)


from tingmu.batch import PreparationQueue


def timestamp(seconds):
    value = max(0, int(seconds))
    return f"{value // 60:02d}:{value % 60:02d}"


class FullscreenSurface(QWidget):
    """A native top-level video surface with no workspace chrome."""

    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.owner = owner
        self.setObjectName("fullscreenSurface")
        self.setStyleSheet("#fullscreenSurface { background: black; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.controls = QWidget(self)
        self.controls.setObjectName("fullscreenControls")
        self.controls.setStyleSheet("#fullscreenControls { background: #1B1E20; }")
        row = QHBoxLayout(self.controls)
        row.setContentsMargins(18, 8, 18, 8)
        self.play_button = QPushButton("播放")
        self.play_button.clicked.connect(lambda: owner.toggle_playback())
        row.addWidget(self.play_button)
        self.position_label = QLabel("00:00")
        row.addWidget(self.position_label)
        self.seek_slider = type(owner.seek_slider)(Qt.Orientation.Horizontal)
        self.seek_slider.setRange(0, max(0, owner.player.duration()))
        self.seek_slider.sliderMoved.connect(lambda value: self.position_label.setText(timestamp(value / 1000)))
        self.seek_slider.sliderReleased.connect(lambda: owner.player.setPosition(self.seek_slider.value()))
        row.addWidget(self.seek_slider, 1)
        self.duration_label = QLabel(timestamp(owner.player.duration() / 1000))
        row.addWidget(self.duration_label)
        self.exit_button = QPushButton("退出全屏")
        self.exit_button.clicked.connect(owner.toggle_fullscreen)
        row.addWidget(self.exit_button)
        layout.addWidget(self.controls)
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.setInterval(3000)
        self.idle_timer.timeout.connect(self.hide_idle_controls)
        owner.player.positionChanged.connect(self.update_position)
        owner.player.durationChanged.connect(self.update_duration)
        owner.player.playbackStateChanged.connect(self.update_playback_state)
        owner.video.setMouseTracking(True)
        self.setMouseTracking(True)
        self.update_playback_state(owner.player.playbackState())

    def update_position(self, value):
        if not self.seek_slider.isSliderDown():
            self.seek_slider.setValue(value)
            self.position_label.setText(timestamp(value / 1000))

    def update_duration(self, value):
        self.seek_slider.setRange(0, max(0, value))
        self.duration_label.setText(timestamp(value / 1000))

    def update_playback_state(self, state):
        self.play_button.setText("暂停" if state == QMediaPlayer.PlaybackState.PlayingState else "播放")
        if self.owner._fullscreen:
            self.reveal_controls()

    def reveal_controls(self):
        self.controls.show()
        self.idle_timer.start()

    def hide_idle_controls(self):
        if (self.owner._fullscreen
                and self.owner.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                and not self.seek_slider.isSliderDown() and not self.controls.underMouse()):
            self.controls.hide()
        elif self.owner._fullscreen:
            self.idle_timer.start()

    def closeEvent(self, event):
        if self.owner._fullscreen:
            self.owner.toggle_fullscreen()
            event.ignore()
        else:
            event.accept()

    def mouseDoubleClickEvent(self, event):
        self.owner.toggle_fullscreen()


class CompanionOverlay(QWidget):
    """Movable translucent text overlay; dragging does not seek or stop video."""

    def __init__(self, owner):
        super().__init__(owner.fullscreen_surface, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint)
        self.owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("companionOverlay")
        self.setStyleSheet("#companionOverlay { background: transparent; }")
        self._drag = None
        self.resize(680, 180)
        layout = QVBoxLayout(self)
        self.panel = QWidget()
        self.panel.setObjectName("overlayPanel")
        layout.addWidget(self.panel)
        content = QVBoxLayout(self.panel)
        content.setContentsMargins(18, 12, 18, 14)
        title = QLabel("学习伴侣 · 拖动移动 · Esc 退出全屏")
        title.setStyleSheet("color: #92988F; font-size: 11px; background: transparent;")
        content.addWidget(title)
        self.sentence = QLabel("")
        self.reading = QLabel("")
        self.translation = QLabel("")
        self.explanation = QLabel("")
        self.explanation.setVisible(owner.overlay_explanation.isChecked())
        for label in (self.sentence, self.reading, self.translation, self.explanation):
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            content.addWidget(label)
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.apply_style()

    def apply_style(self):
        opacity = self.owner.overlay_opacity.value() / 100
        size = self.owner.overlay_font.value()
        self.panel.setStyleSheet(f"#overlayPanel {{ background: rgba(21,23,25,{round(opacity * 255)}); border: 1px solid #343A37; border-radius: 10px; }}")
        self.sentence.setStyleSheet(f"background: transparent; color: #ECEAE4; font-size: {size}px;")
        self.reading.setStyleSheet(f"background: transparent; color: #86B99A; font-size: {max(12, size - 4)}px;")
        self.translation.setStyleSheet(f"background: transparent; color: #ECEAE4; font-size: {max(12, size - 3)}px;")
        self.explanation.setStyleSheet(f"background: transparent; color: #BFC5BE; font-size: {max(12, size - 4)}px;")

    def set_cue(self, cue):
        self.sentence.setText(cue.text if cue else "")
        self.reading.setText(" · ".join(token.reading for token in cue.tokens if token.reading) if cue else "")
        self.translation.setText(cue.translation if cue else "")
        self.explanation.setText(cue.explanation if cue else "")
        self.adjustSize()

    def show_explanation(self, checked):
        self.explanation.setVisible(checked)
        self.adjustSize()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = event.globalPosition().toPoint() - self.pos()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag = None


class WorkspaceMixin:
    def make_dock(self, name, title, widget, area):
        dock = QDockWidget(title, self)
        dock.setObjectName(name)
        dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                         | QDockWidget.DockWidgetFeature.DockWidgetClosable)
        dock.setWidget(widget)
        self.addDockWidget(area, dock)
        return dock

    def build_workspace(self):
        self.setDockOptions(QMainWindow.DockOption.AllowNestedDocks | QMainWindow.DockOption.AllowTabbedDocks
                            | QMainWindow.DockOption.AnimatedDocks | QMainWindow.DockOption.GroupedDragging)
        player_widget = self.takeCentralWidget()
        self.player_widget = player_widget
        self.player_dock = self.make_dock("player", "播放器", player_widget, Qt.DockWidgetArea.LeftDockWidgetArea)
        self.companion_dock = self.make_dock("companion", "学习伴侣", self.companion, Qt.DockWidgetArea.RightDockWidgetArea)
        self.companion.setMinimumWidth(340)
        self.chat_dock = self.make_dock("chat", "伴侣对话", self.companion.chat_page, Qt.DockWidgetArea.RightDockWidgetArea)
        self.chat_dock.setMinimumWidth(300)
        self.library_dock.setObjectName("library")
        self.library_dock.setWindowTitle("媒体库")
        self.settings_dock.setObjectName("settings")
        for dock in (self.library_dock, self.settings_dock):
            dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
        subtitle_widget = QWidget()
        subtitle_layout = QVBoxLayout(subtitle_widget)
        self.subtitle_search = QLineEdit()
        self.subtitle_search.setPlaceholderText("搜索原文、译文或读音")
        self.subtitle_search.textChanged.connect(self.filter_subtitles)
        subtitle_layout.addWidget(self.subtitle_search)
        self.subtitle_list = QListWidget()
        self.subtitle_list.setWordWrap(True)
        self.subtitle_list.setSpacing(4)
        self.subtitle_list.itemClicked.connect(self.seek_subtitle_item)
        subtitle_layout.addWidget(self.subtitle_list)
        self.subtitle_count = QLabel("暂无字幕")
        subtitle_count_style = "color: #92988F;"
        self.subtitle_count.setStyleSheet(subtitle_count_style)
        subtitle_layout.addWidget(self.subtitle_count)
        self.subtitle_dock = self.make_dock("subtitles", "字幕列表", subtitle_widget, Qt.DockWidgetArea.RightDockWidgetArea)
        self.subtitle_dock.setMinimumWidth(230)
        self._subtitle_items = {}
        self.build_queue_panel()
        self.build_reading_settings()
        self.fullscreen_surface = FullscreenSurface(self)
        self.overlay = CompanionOverlay(self)
        self.overlay_opacity.valueChanged.connect(self.overlay.apply_style)
        self.overlay_font.valueChanged.connect(self.overlay.apply_style)
        self.overlay_enabled.toggled.connect(self.update_overlay_visibility)
        self.overlay_explanation.toggled.connect(self.overlay.show_explanation)
        self.docks = [self.player_dock, self.companion_dock, self.subtitle_dock,
                      self.chat_dock, self.library_dock, self.queue_dock, self.settings_dock]
        self.build_menu_bar()
        self.apply_layout_preset("study")
        self.restore_workspace()
        for spin in self.reading_spins.values():
            spin.valueChanged.connect(self.refresh_reading_timeline)

    def build_reading_settings(self):
        form = self.config_widget.layout()
        form.addRow(QLabel("阅读与全屏"))
        self.overlay_enabled = QCheckBox("全屏显示学习伴侣")
        self.overlay_enabled.setChecked(True)
        form.addRow(self.overlay_enabled)
        self.overlay_explanation = QCheckBox("浮层显示讲解")
        self.overlay_explanation.setChecked(False)
        form.addRow(self.overlay_explanation)
        self.overlay_opacity = QSlider(Qt.Orientation.Horizontal)
        self.overlay_opacity.setRange(15, 100)
        self.overlay_opacity.setValue(round(getattr(self.settings, "overlay_opacity", .86) * 100))
        form.addRow("浮层不透明度", self.overlay_opacity)
        self.overlay_font = QSpinBox()
        self.overlay_font.setRange(14, 42)
        self.overlay_font.setSuffix(" px")
        self.overlay_font.setValue(getattr(self.settings, "overlay_font_size", 20))
        form.addRow("浮层字号", self.overlay_font)
        self.reading_spins = {}
        for name, label, default in [("subtitle_min_ms", "字幕最短展示", 1200),
                                      ("subtitle_gap_ms", "短停顿衔接", 180),
                                      ("companion_hold_ms", "伴侣阅读保留", 2200)]:
            spin = QSpinBox()
            spin.setRange(0, 10000)
            spin.setSingleStep(100)
            spin.setSuffix(" ms")
            spin.setValue(getattr(self.settings, name, default))
            self.reading_spins[name] = spin
            form.addRow(label, spin)
        self.review_check = QCheckBox("准备时使用强模型复核文本")
        self.review_check.setChecked(getattr(self.settings, "review_enabled", True))
        form.addRow(self.review_check)
        self.review_model_edit = QLineEdit(getattr(self.settings, "review_model", "gpt-6-astra"))
        form.addRow("复核模型", self.review_model_edit)

    def build_queue_panel(self):
        widget = QWidget()
        layout = QVBoxLayout(widget)
        caption = QLabel("按顺序准备媒体；完成内容保留，可继续处理。")
        caption.setWordWrap(True)
        layout.addWidget(caption)
        self.queue_list = QListWidget()
        self.queue_list.setWordWrap(True)
        layout.addWidget(self.queue_list, 1)
        row = QHBoxLayout()
        for label, callback in [("添加所选", self.queue_selected), ("添加目录", self.queue_library),
                                ("开始 / 继续", self.start_queue), ("暂停", self.pause_queue)]:
            control = QPushButton(label)
            control.clicked.connect(callback)
            row.addWidget(control)
        layout.addLayout(row)
        actions = QHBoxLayout()
        for label, callback in [("重试失败项", self.retry_failed_queue), ("移除所选", self.remove_selected_queue)]:
            control = QPushButton(label)
            control.clicked.connect(callback)
            actions.addWidget(control)
        actions.addStretch()
        layout.addLayout(actions)
        self.queue_status = QLabel("队列为空")
        layout.addWidget(self.queue_status)
        self.queue_dock = self.make_dock("queue", "处理队列", widget, Qt.DockWidgetArea.BottomDockWidgetArea)
        self.preparation_queue = PreparationQueue(self.store)
        self._queue_active = False
        self.queue_timer = QTimer(self)
        self.queue_timer.setInterval(500)
        self.queue_timer.timeout.connect(self.refresh_queue)
        self.queue_timer.start()
        self.refresh_queue()

    def queue_selected(self):
        self.add_queue_paths([Path(item.data(Qt.ItemDataRole.UserRole)) for item in self.library_list.selectedItems()])

    def queue_library(self):
        self.add_queue_paths([Path(self.library_list.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(self.library_list.count())])

    def add_queue_paths(self, paths):
        subtitle_paths = ({str(self.media_path.resolve()): str(self.subtitle_path)}
                          if self.media_path and self.subtitle_path else None)
        try:
            self.preparation_queue.enqueue(paths, self.read_settings(), subtitle_paths=subtitle_paths)
        except (OSError, ValueError) as exc:
            self.show_error(str(exc))
        self.refresh_queue()
        self.queue_dock.show()
        self.queue_dock.raise_()

    def refresh_queue(self):
        jobs = self.preparation_queue.snapshot()
        selected = self.queue_list.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self.queue_list.clear()
        labels = {"queued": "等待", "running": "处理中", "paused": "已暂停", "failed": "未完成", "completed": "已完成"}
        for job in jobs:
            detail = job.get("error") or job.get("progress") or ""
            item = QListWidgetItem(f"{labels.get(job['status'], job['status'])} · {job['name']}\n{detail}")
            item.setData(Qt.ItemDataRole.UserRole, job["id"])
            item.setToolTip(str(job["media_path"]))
            self.queue_list.addItem(item)
            if job["id"] == selected_id:
                self.queue_list.setCurrentItem(item)
        completed = sum(job["status"] == "completed" for job in jobs)
        self.queue_status.setText(f"{len(jobs)} 个媒体 · {completed} 已完成 · {self.preparation_queue.pending_count} 待处理")

    def start_queue(self):
        if self.busy:
            self.queue_status.setText("已有任务处理中；完成后可继续队列")
            return
        if not self.preparation_queue.pending_count:
            self.queue_status.setText("没有等待任务；可添加媒体或重试失败项")
            return
        self._queue_active = True

        def work(progress, cancelled):
            return self.preparation_queue.run(progress=progress, cancelled=cancelled)

        def settled():
            self._queue_active = False
            self.refresh_queue()
            self.reload_cached_document()

        self.start_job("处理队列", work, lambda result: self.refresh_queue(), settled)

    def pause_queue(self):
        self.preparation_queue.pause()
        self.queue_status.setText("正在暂停；当前步骤结束后保存进度")

    def retry_failed_queue(self):
        self.preparation_queue.retry_failed()
        self.refresh_queue()

    def remove_selected_queue(self):
        item = self.queue_list.currentItem()
        if item:
            try:
                self.preparation_queue.remove(item.data(Qt.ItemDataRole.UserRole))
            except ValueError as exc:
                self.queue_status.setText(str(exc))
            self.refresh_queue()

    def refresh_reading_timeline(self):
        if self.document:
            self.set_document(self.document)

    def add_menu_action(self, menu, title, callback, shortcut=None):
        action = menu.addAction(title)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(shortcut)
        return action

    def build_menu_bar(self):
        bar = self.menuBar()
        bar.setNativeMenuBar(False)
        file_menu = bar.addMenu("文件")
        for title, callback, shortcut in [("打开媒体…", self.choose_media, "Ctrl+O"),
                                           ("打开目录…", self.choose_folder, None),
                                           ("导入字幕…", self.import_subtitles, None),
                                           ("导出字幕…", self.export_subtitles, None),
                                           ("保存画面…", self.save_current_frame, None)]:
            self.add_menu_action(file_menu, title, callback, shortcut)
        file_menu.addSeparator()
        self.add_menu_action(file_menu, "退出", self.close)
        playback = bar.addMenu("播放")
        for title, callback in [("播放 / 暂停", self.toggle_playback), ("后退 10 秒", lambda: self.seek_relative(-10000)),
                                ("前进 10 秒", lambda: self.seek_relative(10000)), ("视频全屏", self.toggle_fullscreen),
                                ("设 A 点", self.set_a), ("设 B 点", self.set_b), ("清除循环", self.clear_loops)]:
            self.add_menu_action(playback, title, callback)
        playback.addSeparator()
        playback.addActions([self.previous_media_action, self.next_media_action, self.advance_action])
        playback.aboutToShow.connect(self.refresh_player_menu)
        playback.addMenu(self.build_aspect_menu())
        learning = bar.addMenu("学习")
        for title, callback in [("准备当前媒体", self.prepare), ("强模型复核当前字幕", self.review_current),
                                ("处理队列", lambda: self.queue_dock.show()),
                                ("学习伴侣", self.show_companion), ("伴侣对话", self.show_chat),
                                ("生词与笔记", self.open_notebook), ("重听当前句", self.replay_sentence),
                                ("单句循环", self.toggle_sentence_loop)]:
            self.add_menu_action(learning, title, callback)
        layout = bar.addMenu("布局")
        for dock in self.docks:
            layout.addAction(dock.toggleViewAction())
        layout.addSeparator()
        self.add_menu_action(layout, "影院布局", lambda: self.apply_layout_preset("cinema"))
        self.add_menu_action(layout, "学习布局", lambda: self.apply_layout_preset("study"))
        self.add_menu_action(layout, "保存布局", self.save_workspace)
        self.add_menu_action(layout, "恢复已保存布局", self.restore_workspace)
        self.add_menu_action(layout, "重置布局", lambda: self.apply_layout_preset("study"))
        settings = bar.addMenu("设置")
        self.add_menu_action(settings, "语言、模型与阅读…", self.toggle_settings)
        self.overlay_action = settings.addAction("全屏学习浮层")
        self.overlay_action.setCheckable(True)
        self.overlay_action.setChecked(True)
        self.overlay_action.toggled.connect(self.overlay_enabled.setChecked)
        self.overlay_enabled.toggled.connect(self.overlay_action.setChecked)
        help_menu = bar.addMenu("帮助")
        self.add_menu_action(help_menu, "操作说明", lambda: QMessageBox.information(self, "操作说明", "拖动面板标题可重新排列、合并为标签或浮动。\n空格：播放 / 暂停；F：视频全屏；Esc：退出全屏。\n单击字幕定位，点击词语暂停并查看读音。\n全屏浮层可拖动，字号与透明度在设置调整。"))
        about = bar.addMenu("关于")
        self.add_menu_action(about, "关于听幕", lambda: QMessageBox.about(self, "听幕", "听幕 · 本地播放器与语言学习伴侣\n音视频在本机处理，云端仅接收已授权的文本。"))

    def build_aspect_menu(self):
        menu = QMenu("画面比例", self)
        menu.addActions(self._aspect_group.actions())
        return menu

    def review_current(self):
        if not self.document:
            self.statusBar().showMessage("请先打开已有字幕的媒体，或准备学习")
            return
        self.review_check.setChecked(True)
        self.prepare()

    def show_chat(self):
        self.chat_dock.show()
        self.chat_dock.raise_()
        self.companion.chat_input.setFocus()

    def apply_layout_preset(self, preset):
        for dock in self.docks:
            dock.setFloating(False)
            self.removeDockWidget(dock)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, self.player_dock)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.companion_dock)
        self.splitDockWidget(self.companion_dock, self.subtitle_dock, Qt.Orientation.Horizontal)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, self.library_dock)
        self.tabifyDockWidget(self.library_dock, self.queue_dock)
        self.splitDockWidget(self.companion_dock, self.chat_dock, Qt.Orientation.Vertical)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.settings_dock)
        for dock in self.docks:
            dock.setVisible(dock in (self.player_dock, self.companion_dock, self.subtitle_dock, self.chat_dock)
                            if preset == "study" else dock is self.player_dock)
        self.resizeDocks([self.player_dock, self.companion_dock, self.subtitle_dock], [720, 420, 300], Qt.Orientation.Horizontal)
        self.resizeDocks([self.companion_dock, self.chat_dock], [720, 180], Qt.Orientation.Vertical)
        self._layout_preset = preset

    def save_workspace(self):
        if self._fullscreen:
            return
        path = self.store.data_dir / "workspace.json"
        value = {"version": 1, "geometry": bytes(self.saveGeometry().toBase64()).decode(),
                 "state": bytes(self.saveState(1).toBase64()).decode(),
                 "overlay_enabled": self.overlay_enabled.isChecked(),
                 "overlay_explanation": self.overlay_explanation.isChecked()}
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(value), encoding="utf-8")
        temporary.replace(path)
        self.statusBar().showMessage("布局已保存", 2500)

    def restore_workspace(self):
        path = self.store.data_dir / "workspace.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("version") != 1:
                return False
            geometry = QByteArray.fromBase64(str(value["geometry"]).encode())
            state = QByteArray.fromBase64(str(value["state"]).encode())
            self.restoreGeometry(geometry)
            restored = self.restoreState(state, 1)
            self.overlay_enabled.setChecked(bool(value.get("overlay_enabled", True)))
            self.overlay_explanation.setChecked(bool(value.get("overlay_explanation", False)))
            return restored
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def rebuild_subtitle_list(self):
        self.subtitle_list.clear()
        self._subtitle_items = {}
        for cue in self.cues:
            text = f"{timestamp(cue.start)}  {cue.text}"
            if cue.translation:
                text += f"\n{cue.translation}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, cue.id)
            item.setToolTip(f"{timestamp(cue.start)}–{timestamp(cue.end)}\n单击定位原始时间轴")
            self.subtitle_list.addItem(item)
            self._subtitle_items[cue.id] = item
        self.filter_subtitles()

    def filter_subtitles(self):
        query = self.subtitle_search.text().strip().casefold()
        visible = 0
        for cue in self.cues:
            item = self._subtitle_items.get(cue.id)
            if item:
                haystack = " ".join([cue.text, cue.translation, cue.original_text] + [t.reading for t in cue.tokens]).casefold()
                hidden = bool(query and query not in haystack)
                item.setHidden(hidden)
                visible += not hidden
        self.subtitle_count.setText(f"{visible} / {len(self.cues)} 句" if self.cues else "暂无字幕")

    def select_subtitle_cue(self, cue):
        item = self._subtitle_items.get(cue.id) if cue else None
        self.subtitle_list.setCurrentItem(item)
        if item and not item.isHidden() and not self.subtitle_search.hasFocus():
            self.subtitle_list.scrollToItem(item)

    def seek_subtitle_item(self, item):
        cue = next((cue for cue in self.cues if cue.id == item.data(Qt.ItemDataRole.UserRole)), None)
        if cue:
            self.clear_loops()
            position = self.cue_player_start(cue)
            self.player.setPosition(position)
            self.sync_cue(position, force=True)

    def toggle_fullscreen(self):
        if not self._fullscreen:
            self._fullscreen = True
            self._hidden_floating = [dock for dock in self.docks if dock.isFloating() and dock.isVisible()]
            for dock in self._hidden_floating:
                dock.hide()
            self.player_widget.layout().removeWidget(self.video)
            self.fullscreen_surface.layout().insertWidget(0, self.video, 1)
            self.video.show()
            self.fullscreen_surface.showFullScreen()
            self.fullscreen_surface.reveal_controls()
            screen = self.fullscreen_surface.screen().availableGeometry()
            self.overlay.move(screen.center() - QPoint(self.overlay.width() // 2, -screen.height() // 4))
            self.update_overlay_visibility()
            self.fullscreen_surface.activateWindow()
        else:
            self._fullscreen = False
            self.overlay.hide()
            self.fullscreen_surface.layout().removeWidget(self.video)
            self.player_widget.layout().insertWidget(1, self.video, 1)
            self.video.show()
            self.fullscreen_surface.idle_timer.stop()
            self.fullscreen_surface.hide()
            for dock in self._hidden_floating:
                dock.show()
            self.activateWindow()

    def update_overlay_visibility(self, *_args):
        self.overlay.setVisible(self._fullscreen and self.overlay_enabled.isChecked())
        if self.overlay.isVisible():
            self.overlay.raise_()

    def owns_active_window(self):
        active = QApplication.activeWindow()
        return active in (self, self.companion, self.fullscreen_surface, self.overlay) or active in self.docks
