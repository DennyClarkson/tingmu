"""Application notifications and diagnostic commands through explicit ports."""

from dataclasses import dataclass
from typing import Callable
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from tingmu import __version__


@dataclass(frozen=True)
class ApplicationPorts:
    store: Callable
    log: Callable
    refresh_diagnostics: Callable
    export_diagnostics: Callable
    set_state: Callable


class ApplicationController:
    def __init__(self, state, ports):
        self.state, self.ports = state, ports

    def command_open_data_folder(self, name, p):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ports.store().data_dir)))

    def command_open_log_folder(self, name, p):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.ports.log().directory)))

    def command_refresh_diagnostics(self, name, p):
        self.ports.refresh_diagnostics()

    def command_export_diagnostics(self, name, p):
        self.ports.export_diagnostics()

    def command_dismiss_error(self, name, p):
        self.ports.set_state(error="", status="")

    def command_open_about(self, name, p):
        self.ports.set_state(status=f"听幕 {__version__} · 当前工作区：{self.ports.store().data_dir}")
