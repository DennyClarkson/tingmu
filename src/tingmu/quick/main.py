"""Create the local QML desktop application, with no web server or remote assets."""

import sys
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from tingmu.runtime import data_root
from tingmu.storage import Store
from tingmu.workspaces import DEFAULT_ID, WorkspaceCatalog, ensure_folders
from .bridge import Bridge
from .workspace_controller import WorkspaceController


def create_qml_app(data_dir=None, initial_path=None, *, show_setup=False):
    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("听幕")
    application.setOrganizationName("Tingmu")
    if not getattr(application, "_tingmu_fonts_loaded", False):
        if QQuickStyle.name() != "Basic":
            QQuickStyle.setStyle("Basic")
        for font_path in (Path(__file__).parent / "qml/fonts").glob("*.otf"):
            if QFontDatabase.addApplicationFont(str(font_path)) < 0:
                raise RuntimeError("随附字体无法读取，请检查便携文件夹是否完整")
        application._tingmu_fonts_loaded = True
    application.setFont(QFont("Source Han Sans SC", 10))
    catalog = WorkspaceCatalog(data_dir or data_root())
    workspace_id = catalog.active_id
    notice = ""
    try:
        catalog.directory(workspace_id)
    except ValueError as exc:
        notice = str(exc)
        workspace_id = DEFAULT_ID
    return _open_session(application, catalog, workspace_id, bool(show_setup or notice), initial_path, notice)


def _open_session(application, catalog, workspace_id, show_setup, initial_path, notice=""):
    directory = catalog.directory(workspace_id)
    ensure_folders(directory)
    store = Store(directory)
    bridge = Bridge(store)
    engine = QQmlApplicationEngine()
    def switch(new_id, pending_path, configure_models):
        # Build the replacement before closing the original session. QML drafts, video
        # position and queued callbacks all remain attached to their original Bridge.
        _, new_bridge, new_engine = _open_session(application, catalog, new_id,
                                                  "models" if configure_models else False, pending_path)
        try:
            catalog.select(new_id)
        except Exception:
            new_engine.rootObjects()[0].close()
            new_bridge.shutdown()
            new_engine.deleteLater()
            application._tingmu_quick = (bridge, engine)
            raise
        summary = getattr(bridge, "_workspace_switch_summary", "")
        if summary:
            from .features import message
            session = new_bridge.conversations.create(new_bridge._settings.language, title="工作区准备")
            new_bridge.conversations.save(session["id"], [message("assistant", summary, model="听幕本地向导")])
            new_bridge._select_chat(session["id"])
        engine.rootObjects()[0].close()
        bridge.shutdown()
        engine.deleteLater()

    controller = WorkspaceController(bridge, catalog, workspace_id, show_setup, switch, initial_path)
    controller.error = notice
    bridge.workspaces = controller
    engine.rootContext().setContextProperty("workspaces", controller)
    warnings = []
    def qml_warning(items):
        warnings.extend(item.toString() for item in items)
        bridge.log.event("qml_warning", "界面运行警告", "WARNING", locations=[{
            "file": item.url().fileName(), "line": item.line(), "column": item.column()} for item in items])
    engine.warnings.connect(qml_warning)
    engine._tingmu_warnings = warnings
    engine.destroyed.connect(bridge.deleteLater)
    engine.rootContext().setContextProperty("backend", bridge)
    engine.load(QUrl.fromLocalFile(str(Path(__file__).parent / "qml/Main.qml")))
    if not engine.rootObjects():
        bridge.shutdown()
        raise RuntimeError("听幕界面未能加载，请检查便携文件夹是否完整\n" + "\n".join(warnings[:8]))
    engine._tingmu_window = engine.rootObjects()[0]
    application.aboutToQuit.connect(bridge.shutdown)
    application._tingmu_quick = (bridge, engine)
    if initial_path and not show_setup:
        QTimer.singleShot(0, lambda: controller.action("enter", {}))
    return application, bridge, engine


def run_app(initial_path=None, data_dir=None):
    application, bridge, engine = create_qml_app(data_dir, initial_path)
    previous_hook = sys.excepthook
    def exception_hook(kind, error, trace):
        current = application._tingmu_quick[0]
        current.log.exception(error)
        current._error(f"内部错误：{kind.__name__}；请在开发者模式中查看日志")
    sys.excepthook = exception_hook
    try:
        return application.exec()
    finally:
        sys.excepthook = previous_hook
        application._tingmu_quick[0].shutdown()
