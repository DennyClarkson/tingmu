"""Keep the current study session available from the desktop notification area."""
import json
from pathlib import Path

from PySide6.QtCore import QObject, Property, Qt, Slot
from PySide6.QtGui import QIcon, QWindow
from PySide6.QtWidgets import QMenu, QSystemTrayIcon
from shiboken6 import isValid

from tingmu.runtime import confined_path
from tingmu.storage import atomic_json
from .localization import ui_text


class SystemTray(QObject):
    def __init__(self, app):
        super().__init__(app)
        self._app = app
        self._window = None
        self._hidden = []
        self._exiting = False
        self._preference_path = None
        self._language = None
        icon = QIcon(str(Path(__file__).parent / "brand" / "app-icon.ico"))
        self._tray = QSystemTrayIcon(icon, self)
        self._tray.setToolTip("听幕")
        self._menu = QMenu()
        self._open = self._menu.addAction("打开听幕", self.restore)
        self._hide = self._menu.addAction("收起到托盘", self.hideToTray)
        self._menu.addSeparator()
        self._close_to_tray = self._menu.addAction("关闭窗口时收起到托盘")
        self._close_to_tray.setCheckable(True)
        self._close_to_tray.triggered.connect(self._save_close_policy)
        self._quit = self._menu.addAction("退出听幕", self.quitApplication)
        self._tray.setContextMenu(self._menu)
        self._tray.activated.connect(self._activated)
        app.setWindowIcon(icon)
        app.aboutToQuit.connect(self._tray.hide)

    def set_language(self, language):
        if language is not self._language:
            if self._language is not None:
                self._language.changed.disconnect(self._retranslate)
            self._language = language
            language.changed.connect(self._retranslate)
        self._retranslate()

    def _retranslate(self):
        for action, label in ((self._open, "打开听幕"), (self._hide, "收起到托盘"),
                              (self._close_to_tray, "关闭窗口时收起到托盘"), (self._quit, "退出听幕")):
            action.setText(ui_text(label))
        self._app.setApplicationDisplayName(ui_text("听幕"))
        self._tray.setToolTip(ui_text("听幕"))

    @Property(bool, constant=True)
    def available(self):
        return self._app.platformName() not in ("offscreen", "minimal") and self._tray.isSystemTrayAvailable()

    def bind(self, window, catalog_root):
        was_hidden = bool(self._hidden)
        self._hidden.clear()
        self._window = window
        self._exiting = False
        self._preference_path = confined_path(catalog_root, "tray-settings.json")
        try:
            prefs = json.loads(self._preference_path.read_text("utf-8"))
        except (OSError, ValueError):
            prefs = {}
        self._close_to_tray.setChecked(isinstance(prefs, dict) and prefs.get("close_to_tray") is True)
        if self.available:
            self._tray.setToolTip(window.title())
            window.windowTitleChanged.connect(self._tray.setToolTip)
            self._tray.show()
            if was_hidden:
                self.hideToTray()

    def _save_close_policy(self, checked):
        atomic_json(self._preference_path, {"close_to_tray": checked})

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.restore()

    @Slot(QObject, result=bool)
    def shouldHideOnClose(self, window):
        return (self.available and not self._exiting and window == self._window
                and self._close_to_tray.isChecked())

    @Slot()
    def hideToTray(self):
        window = self._window
        if not self.available or window is None or not isValid(window) or self._hidden:
            return
        children = [w for w in window.findChildren(QWindow) if w.isVisible() and w.type() != Qt.Popup]
        self._hidden = [(w, w.visibility()) for w in [window, *children]]
        for w in [*children, window]:
            w.hide()

    @Slot()
    def restore(self):
        window = self._window
        if window is None or not isValid(window):
            return
        for w, visibility in self._hidden:
            if isValid(w):
                w.setVisibility(visibility if visibility != QWindow.Minimized else QWindow.Windowed)
        self._hidden.clear()
        if window.visibility() in (QWindow.Hidden, QWindow.Minimized):
            window.showNormal()
        window.raise_()
        window.requestActivate()

    @Slot()
    def quitApplication(self):
        self._exiting = True
        if self._window is not None and isValid(self._window):
            if not self._window.close():
                self._exiting = False
                return
        self._tray.hide()
        self._app.quit()
