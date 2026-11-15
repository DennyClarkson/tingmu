"""Portable QML checks must fail before delivering a broken application folder."""
import importlib.util
import os
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location(
    "portable_qml_runtime", Path(__file__).parents[1] / "packaging/qml_runtime.py")
qml_runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qml_runtime)
log_spec = importlib.util.spec_from_file_location(
    "portable_bootstrap_logging", Path(__file__).parents[1] / "packaging/bootstrap_logging.py")
bootstrap_logging = importlib.util.module_from_spec(log_spec)
log_spec.loader.exec_module(bootstrap_logging)


def test_runtime_detects_missing_multimedia_plugin(tmp_path):
    qt = qml_runtime.qt_directory(tmp_path)
    files = [qt / "qml" / module / "qmldir" for module in qml_runtime.QML_MODULES]
    files.extend(qt / name for name in qml_runtime.QT_BINARIES)
    for path in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture", encoding="utf-8")
    assert "QtMultimedia" in qml_runtime.validate_qml_runtime(tmp_path)["modules"]
    (qt / "plugins/multimedia/ffmpegmediaplugin.dll").unlink()
    with pytest.raises(RuntimeError, match="ffmpegmediaplugin.dll"):
        qml_runtime.validate_qml_runtime(tmp_path)


def test_qml_environment_overrides_external_paths_without_touching_user_data(tmp_path, monkeypatch):
    original = tmp_path / "data/settings.json"
    original.parent.mkdir()
    original.write_text('{"preserve": true}', encoding="utf-8")
    for variable in ("QML_IMPORT_PATH", "QML2_IMPORT_PATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                     "QML_DISK_CACHE_PATH", "QT_SHADER_CACHE_PATH", "QT_QUICK_CONTROLS_STYLE"):
        monkeypatch.setenv(variable, "C:/invalid/development/path")
    qml_runtime.configure_qml_environment(tmp_path)
    for variable in ("QML_IMPORT_PATH", "QML2_IMPORT_PATH", "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                     "QML_DISK_CACHE_PATH", "QT_SHADER_CACHE_PATH"):
        assert Path(os.environ[variable]).is_relative_to(tmp_path)
    assert os.environ["QT_QUICK_CONTROLS_STYLE"] == "Basic"
    assert original.read_text(encoding="utf-8") == '{"preserve": true}'


def test_packaging_requires_qml_and_records_content_integrity(tmp_path):
    with pytest.raises(RuntimeError, match="No application QML"):
        qml_runtime.qml_asset_manifest(tmp_path)
    qml = tmp_path / "app/tingmu/quick/qml/Main.qml"
    qml.parent.mkdir(parents=True)
    qml.write_text("import QtQuick\nItem {}", encoding="utf-8")
    before = qml_runtime.qml_asset_manifest(tmp_path)
    assert list(before) == ["app/tingmu/quick/qml/Main.qml"]
    qml.write_text("import QtQuick\nRectangle {}", encoding="utf-8")
    assert qml_runtime.qml_asset_manifest(tmp_path) != before
    (qml.parent / "Style.js").write_text("var color = 'black'", encoding="utf-8")
    assert "app/tingmu/quick/qml/Style.js" in qml_runtime.qml_asset_manifest(tmp_path)


def test_bootstrap_log_redacts_environment_and_split_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "custom-openai-secret-value")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "custom-deepseek-secret-value")
    path = tmp_path / "data/logs/application.log"
    with bootstrap_logging.RedactingLog(path) as stream:
        stream.write("RuntimeError custom-openai-")
        stream.write("secret-value custom-deepseek-secret-value sk-proj-ABC123\n")
        stream.write("Traceback: local startup frame\n")
    content = path.read_text("utf-8")
    assert "secret-value" not in content and "sk-proj" not in content
    assert content.count("[redacted]") == 3
    assert "RuntimeError" in content and "local startup frame" in content


def test_bootstrap_log_rotates_at_startup_and_bounds_files(tmp_path):
    path = tmp_path / "application.log"
    path.write_text("previous session\n" * 30, encoding="utf-8")
    with bootstrap_logging.RedactingLog(path, max_bytes=128) as stream:
        assert path.with_suffix(".log.1").exists()
        for index in range(30):
            stream.write(f"Traceback frame {index:02}: startup\n")
        stream.write("oversized " * 1000 + "\n")
    files = sorted(tmp_path.glob("application.log*"))
    assert len(files) == 3
    assert all(file.stat().st_size <= 128 for file in files)
    assert "oversized log line omitted" in path.read_text("utf-8")
