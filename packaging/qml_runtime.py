"""Validate and isolate the Qt Quick files shipped in the portable folder."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


QML_MODULES = (
    "QtQml", "QtQuick", "QtQuick/Window", "QtQuick/Layouts",
    "QtQuick/Controls", "QtQuick/Controls/Basic", "QtQuick/Templates", "QtMultimedia",
)
QT_BINARIES = (
    "QtCore.pyd", "QtGui.pyd", "QtWidgets.pyd", "QtNetwork.pyd", "QtSvg.pyd",
    "QtQml.pyd", "QtMultimedia.pyd",
    "Qt6OpenGL.dll", "opengl32sw.dll",
    "Qt6Qml.dll", "Qt6Quick.dll", "Qt6QuickControls2.dll", "Qt6Multimedia.dll",
    "Qt6MultimediaQuick.dll", "plugins/platforms/qwindows.dll",
    "plugins/platforms/qoffscreen.dll", "plugins/platforms/qminimal.dll",
    "plugins/multimedia/ffmpegmediaplugin.dll", "plugins/multimedia/windowsmediaplugin.dll",
    "plugins/imageformats/qjpeg.dll", "plugins/imageformats/qico.dll", "plugins/imageformats/qsvg.dll",
    "plugins/iconengines/qsvgicon.dll", "plugins/styles/qmodernwindowsstyle.dll",
    "plugins/tls/qschannelbackend.dll",
)
QT_TRANSLATIONS = tuple(
    f"translations/{catalog}_{language}.qm"
    for catalog in ("qt", "qtbase", "qtdeclarative", "qtmultimedia")
    for language in ("en", "zh_CN", "zh_TW")
)


def qt_directory(root: Path) -> Path:
    return root / "runtime/python/Lib/site-packages/PySide6"


def configure_qml_environment(root: Path) -> None:
    """Override inherited developer imports and keep shader/QML caches local."""
    qt = qt_directory(root)
    os.environ["QML_IMPORT_PATH"] = str(qt / "qml")
    os.environ["QML2_IMPORT_PATH"] = str(qt / "qml")
    os.environ["QT_PLUGIN_PATH"] = str(qt / "plugins")
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(qt / "plugins/platforms")
    os.environ["QT_QUICK_CONTROLS_STYLE"] = "Basic"
    for variable, folder in (("QML_DISK_CACHE_PATH", "qml"), ("QT_SHADER_CACHE_PATH", "qt-shaders")):
        path = root / ".cache" / folder
        path.mkdir(parents=True, exist_ok=True)
        os.environ[variable] = str(path)


def validate_qml_runtime(root: Path) -> dict:
    # Only the build uses the PE reader. The shipped bootstrap just sets paths.
    from pe_bundle import validate_qt_directory

    return validate_qt_directory(qt_directory(root))


def qml_asset_manifest(root: Path) -> dict[str, str]:
    asset_root = root / "app/tingmu/quick/qml"
    files = sorted(path for path in asset_root.rglob("*") if path.is_file())
    if not (asset_root / "Main.qml").is_file():
        raise RuntimeError("No application QML entrypoint was packaged (quick/qml/Main.qml)")
    if (asset_root.parent / "localization.py").is_file():
        catalogs = [asset_root.parent / "i18n" / name for name in ("en.json", "zh-TW.json")]
        for catalog in catalogs:
            if not catalog.is_file():
                raise RuntimeError(f"Missing interface language catalog: {catalog.name}")
        files.extend(catalogs)
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files}
