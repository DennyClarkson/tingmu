"""Create the local QML desktop application, with no web server or remote assets."""

import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from tingmu.storage import Store
from .bridge import Bridge


def create_qml_app(data_dir=None, initial_path=None):
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("听幕")
    application.setOrganizationName("Tingmu")
    if not getattr(application, "_tingmu_fonts_loaded", False):
        for font_path in (Path(__file__).parent / "qml/fonts").glob("*.otf"):
            if QFontDatabase.addApplicationFont(str(font_path)) < 0:
                raise RuntimeError("随附字体无法读取，请检查便携文件夹是否完整")
        application._tingmu_fonts_loaded = True
    application.setFont(QFont("Source Han Sans SC", 10))
    QQuickStyle.setStyle("Basic")
    bridge = Bridge(Store(Path(data_dir) if data_dir else None))
    engine = QQmlApplicationEngine()
    warnings = []
    def qml_warning(items):
        warnings.extend(item.toString() for item in items)
        bridge.log.event("qml_warning", "界面运行警告", "WARNING", locations=[{
            "file": item.url().fileName(), "line": item.line(), "column": item.column()} for item in items])
    engine.warnings.connect(qml_warning)
    engine._tingmu_warnings = warnings
    engine.rootContext().setContextProperty("backend", bridge)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "qml/Main.qml")))
    if not engine.rootObjects():
        bridge.shutdown()
        raise RuntimeError("听幕界面未能加载，请检查便携文件夹是否完整\n" + "\n".join(warnings[:8]))
    application.aboutToQuit.connect(bridge.shutdown)
    application._tingmu_quick = (bridge, engine)
    if initial_path:
        path = Path(initial_path)
        if path.is_dir():
            QTimer.singleShot(0, lambda: bridge.add_paths([str(path)]))
        else:
            QTimer.singleShot(0, lambda: bridge.open_media(str(path)))
    return application, bridge, engine


def run_app(initial_path=None, data_dir=None):
    application, bridge, engine = create_qml_app(data_dir, initial_path)
    previous_hook = sys.excepthook
    def exception_hook(kind, error, trace):
        bridge.log.exception(error)
        bridge._error(f"内部错误：{kind.__name__}；请在开发者模式中查看日志")
    sys.excepthook = exception_hook
    try:
        return application.exec()
    finally:
        sys.excepthook = previous_hook
        bridge.shutdown()
