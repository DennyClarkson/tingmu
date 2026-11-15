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
    "QtQml.pyd", "QtQuick.pyd", "QtQuickControls2.pyd", "QtMultimedia.pyd",
    "Qt6Qml.dll", "Qt6Quick.dll", "Qt6QuickControls2.dll", "Qt6Multimedia.dll",
    "Qt6MultimediaQuick.dll", "plugins/platforms/qwindows.dll",
    "plugins/multimedia/ffmpegmediaplugin.dll",
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
    qt = qt_directory(root)
    expected = [qt / "qml" / module / "qmldir" for module in QML_MODULES]
    expected.extend(qt / filename for filename in QT_BINARIES)
    missing = [str(path.relative_to(root)) for path in expected if not path.is_file()]
    if missing:
        raise RuntimeError("Portable Qt Quick runtime is incomplete: " + ", ".join(missing))
    return {"modules": list(QML_MODULES), "binaries": list(QT_BINARIES)}


def qml_asset_manifest(root: Path) -> dict[str, str]:
    asset_root = root / "app/tingmu/quick/qml"
    files = sorted(path for path in asset_root.rglob("*") if path.is_file())
    if not (asset_root / "Main.qml").is_file():
        raise RuntimeError("No application QML entrypoint was packaged (quick/qml/Main.qml)")
    return {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files}
