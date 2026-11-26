"""Create the local QML desktop application, with no web server or remote assets."""

import os
import sys
from pathlib import Path

from PySide6.QtCore import QMetaObject, QTimer, Qt, QUrl, Q_RETURN_ARG
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from tingmu.runtime import data_root, portable_root
from tingmu.storage import Store
from tingmu.workspaces import DEFAULT_ID, WorkspaceCatalog, ensure_folders
from .bridge import Bridge
from .localization import InterfaceLanguage, ui_text
from .system_tray import SystemTray
from .workspace_controller import WorkspaceController


def _register_bundled_font(path):
    if QFontDatabase.addApplicationFont(str(path)) >= 0:
        return
    # Qt's offscreen FreeType backend can lose Unicode paths in the native code page.
    if QFontDatabase.addApplicationFontFromData(path.read_bytes()) < 0:
        raise RuntimeError("随附字体无法读取，请检查便携文件夹是否完整")


def create_qml_app(data_dir=None, initial_path=None, *, show_setup=False, runtime_setup_dir=None):
    # QML loads its native Quick plugins. Avoid pulling the QtQuick/OpenGL
    # Python bindings into the portable runtime solely to select a style.
    portable = portable_root() is not None
    if portable:
        os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
    application = QApplication.instance() or QApplication(sys.argv)
    application.setOrganizationName("Tingmu")
    catalog = WorkspaceCatalog(data_dir or data_root())
    language = getattr(application, "_tingmu_language", None)
    if language is None or language.path.parent != catalog.root:
        if language is not None:
            application.removeTranslator(language)
        language = InterfaceLanguage(catalog.root, application)
        application.installTranslator(language)
        application._tingmu_language = language
    application.setApplicationName("Tingmu")
    application.setApplicationDisplayName(ui_text("听幕"))
    if not getattr(application, "_tingmu_fonts_loaded", False):
        if not portable:
            from PySide6.QtQuickControls2 import QQuickStyle
            if QQuickStyle.name() != "Basic":
                QQuickStyle.setStyle("Basic")
        for font_path in (Path(__file__).parent / "qml/fonts").glob("*.otf"):
            _register_bundled_font(font_path)
        application._tingmu_fonts_loaded = True
    application.setFont(QFont("Source Han Sans SC", 10))
    if not hasattr(application, "_tingmu_tray"):
        application._tingmu_tray = SystemTray(application)
    application._tingmu_tray.set_language(language)
    workspace_id = catalog.active_id
    notice = ""
    try:
        catalog.directory(workspace_id)
    except ValueError as exc:
        notice = str(exc)
        workspace_id = DEFAULT_ID
    return _open_session(application, catalog, workspace_id, bool(show_setup or notice), initial_path, notice,
                         runtime_setup_dir=runtime_setup_dir)


def _open_session(application, catalog, workspace_id, show_setup, initial_path, notice="", *, switch_summary="",
                  runtime_setup_dir=None):
    directory = catalog.directory(workspace_id)
    ensure_folders(directory)
    store = Store(directory)
    bridge = Bridge(store)
    if switch_summary:
        bridge.chat.create_setup_summary(switch_summary)
    engine = QQmlApplicationEngine()
    animations = application.platformName() not in ("offscreen", "minimal") and "--portable-smoke" not in sys.argv
    engine.rootContext().setContextProperty("onscreenAnimations", animations)
    language = application._tingmu_language
    engine.rootContext().setContextProperty("interfaceLanguage", language)
    language.changed.connect(engine.retranslate)
    def switch(new_id, pending_path, configure_models):
        # Build the replacement before closing the original session. QML drafts, video
        # position and queued callbacks all remain attached to their original Bridge.
        window = engine.rootObjects()[0]
        if not QMetaObject.invokeMethod(window, "flushAllDrafts", Qt.ConnectionType.DirectConnection,
                                        Q_RETURN_ARG("QVariant")):
            raise ValueError("当前草稿未能保存，已取消切换工作区；请重试保存后再切换。")
        summary = controller.switch_summary
        _, new_bridge, new_engine = _open_session(application, catalog, new_id,
                                                  "models" if configure_models else False, pending_path,
                                                  switch_summary=summary, runtime_setup_dir=runtime_setup_dir)
        try:
            catalog.select(new_id)
        except Exception:
            new_engine.rootObjects()[0].setProperty("closingSession", True)
            new_engine.rootObjects()[0].close()
            new_bridge.shutdown()
            new_engine.deleteLater()
            application._tingmu_quick = (bridge, engine)
            application._tingmu_tray.bind(engine.rootObjects()[0], catalog.root)
            raise
        controller.switch_summary = ""
        engine.rootObjects()[0].setProperty("closingSession", True)
        engine.rootObjects()[0].close()
        bridge.shutdown()
        engine.deleteLater()

    controller = WorkspaceController(bridge, catalog, workspace_id, show_setup, switch, initial_path,
                                     runtime_setup_dir=runtime_setup_dir)
    controller.error = notice
    bridge.workspaces = controller
    engine.rootContext().setContextProperty("workspaces", controller)
    engine.rootContext().setContextProperty("runtimeSetup", controller.runtime_setup)
    engine.rootContext().setContextProperty("desktopTray", application._tingmu_tray)
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
    application._tingmu_tray.bind(engine._tingmu_window, catalog.root)
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
        current.report_error(f"内部错误：{kind.__name__}；请在开发者模式中查看日志")
    sys.excepthook = exception_hook
    try:
        return application.exec()
    finally:
        sys.excepthook = previous_hook
        application._tingmu_quick[0].shutdown()
