"""Windows packaging checks use small PE images, including on non-Windows builders."""
import ast
from pathlib import Path
import struct

import pytest


PACKAGING = Path(__file__).resolve().parents[1] / "packaging"


def pe_image(imports=(), delay=(), *, bits=64, legacy_delay=False):
    """One section with ordinary and delay descriptors, terminating entries, and names."""
    data = bytearray(0x800)
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3c, 0x80)
    data[0x80:0x84] = b"PE\0\0"
    header = 0x98
    directory_offset = 112 if bits == 64 else 96
    header_size = directory_offset + 16 * 8
    struct.pack_into("<HH", data, 0x84, 0x8664 if bits == 64 else 0x14c, 1)
    struct.pack_into("<H", data, 0x94, header_size)
    struct.pack_into("<H", data, header, 0x20b if bits == 64 else 0x10b)
    base = 0x400000
    if bits == 64:
        struct.pack_into("<Q", data, header + 24, base)
    else:
        struct.pack_into("<I", data, header + 28, base)
    directories = header + directory_offset
    struct.pack_into("<I", data, directories - 4, 16)
    section = header + header_size
    data[section:section + 6] = b".idata"
    struct.pack_into("<IIII", data, section + 8, 0x600, 0x1000, 0x600, 0x200)
    next_name = 0x500
    for names, slot, cursor, stride, field in ((imports, 1, 0x200, 20, 12), (delay, 13, 0x380, 32, 4)):
        if not names:
            continue
        struct.pack_into("<II", data, directories + slot * 8, 0xe00 + cursor, (len(names) + 1) * stride)
        for name in names:
            pointer = 0xe00 + next_name
            if slot == 13:
                if legacy_delay:
                    pointer += base
                else:
                    struct.pack_into("<I", data, cursor, 1)
            struct.pack_into("<I", data, cursor + field, pointer)
            raw = name.encode("ascii") + b"\0"
            data[next_name:next_name + len(raw)] = raw
            next_name += len(raw)
            cursor += stride
    return bytes(data)


@pytest.fixture
def bundle(monkeypatch):
    monkeypatch.syspath_prepend(str(PACKAGING))
    import pe_bundle
    return pe_bundle


def make_qt_tree(qt):
    import qml_runtime

    for module in qml_runtime.QML_MODULES:
        folder = qt / "qml" / module
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "qmldir").write_text("module " + module.replace("/", "."), encoding="utf-8")
    for name in (*qml_runtime.QT_BINARIES, *qml_runtime.QT_TRANSLATIONS, "__init__.py"):
        path = qt / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pe_image() if path.suffix in {".dll", ".pyd"} else b"")


@pytest.mark.parametrize("bits,legacy", [(32, False), (32, True), (64, False)])
def test_pe_import_directories(bundle, tmp_path, bits, legacy):
    binary = tmp_path / "input.pyd"
    binary.write_bytes(pe_image(["Qt6Gui.dll", "KERNEL32.dll"], ["avcodec-63.dll"], bits=bits, legacy_delay=legacy))
    assert bundle._inspect(binary)[0] == {"qt6gui.dll", "kernel32.dll", "avcodec-63.dll"}
    binary.write_bytes(binary.read_bytes()[:250])
    with pytest.raises(RuntimeError, match="Cannot read Windows PE"):
        bundle._inspect(binary)


def test_qt_closure_resolves_plugins_data_and_optional_styles(bundle, tmp_path):
    source = tmp_path / "full"
    make_qt_tree(source)
    basic = source / "qml/QtQuick/Controls/Basic"
    (basic / "qmldir").write_text("plugin qbasic\noptional import QtQuick.Controls.Fusion auto", encoding="utf-8")
    (basic / "qbasic.dll").write_bytes(pe_image(["Qt6Dynamic.dll"]))
    (basic / "Button.qml").write_text("/*\nimport Unavailable.Example\n*/\nimport QtQuick.Shapes\n", encoding="utf-8")
    shapes = source / "qml/QtQuick/Shapes"
    shapes.mkdir()
    (shapes / "qmldir").write_text("module QtQuick.Shapes", encoding="utf-8")
    (source / "Qt6Dynamic.dll").write_bytes(pe_image(delay=["avutil-61.dll"]))
    (source / "avutil-61.dll").write_bytes(pe_image(["Qt6Dynamic.dll"]))
    for unused in ("Qt6WebEngineCore.dll", "qml/QtQuick/Controls/Fusion/qmldir", "resources/icudtl.dat",
                   "translations/qtbase_es.qm", "qmlls.exe", "include/unneeded.h"):
        path = source / unused
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"unused")
    destination = tmp_path / "trimmed"
    bundle.copy_pyside_runtime(source, destination)
    assert bundle.validate_qt_directory(destination)["icu_data_required"] is False
    for unused in ("Qt6WebEngineCore.dll", "qml/QtQuick/Controls/Fusion", "resources", "qmlls.exe", "include",
                   "translations/qtbase_es.qm"):
        assert not (destination / unused).exists()
    for required in ("qml/QtQuick/Controls/Basic/qbasic.dll", "qml/QtQuick/Shapes/qmldir", "avutil-61.dll",
                     "opengl32sw.dll", "translations/qtbase_zh_TW.qm"):
        assert (destination / required).is_file()
    # The clean builder may receive a previously trimmed runtime as its source.
    clean = tmp_path / "clean"
    bundle.copy_pyside_runtime(destination, clean)
    assert bundle.validate_qt_directory(clean) == bundle.validate_qt_directory(destination)
    (clean / "avutil-61.dll").unlink()
    with pytest.raises(RuntimeError, match=r"Qt6Dynamic.dll imports missing DLL avutil-61.dll"):
        bundle.validate_qt_directory(clean)
    # Verify every retained binary, including an unexpected DLL outside the seeds.
    (destination / "extra.dll").write_bytes(pe_image(["Qt6Missing.dll"]))
    with pytest.raises(RuntimeError, match=r"extra.dll imports missing DLL qt6missing.dll"):
        bundle.validate_qt_directory(destination)


@pytest.mark.parametrize("missing", ["QtQuick.pyd", "QtOpenGL.pyd"])
def test_qt_requires_markdown_python_bindings(bundle, tmp_path, missing):
    make_qt_tree(tmp_path)
    (tmp_path / missing).unlink()
    with pytest.raises(RuntimeError, match=missing):
        bundle.validate_qt_directory(tmp_path)


def test_qt_copies_referenced_icu_data(bundle, tmp_path):
    source = tmp_path / "full"
    make_qt_tree(source)
    (source / "Qt6Quick.dll").write_bytes(pe_image() + "icudtl.dat".encode("utf-16le"))
    with pytest.raises(RuntimeError, match="icudtl.dat"):
        bundle.copy_pyside_runtime(source, tmp_path / "trimmed")
    (source / "resources").mkdir()
    (source / "resources/icudtl.dat").write_bytes(b"icu test data")
    bundle.copy_pyside_runtime(source, tmp_path / "trimmed")
    assert bundle.validate_qt_directory(tmp_path / "trimmed")["icu_data_required"] is True


@pytest.mark.parametrize("shared", [True, False])
def test_ffmpeg_copies_matching_binary_closure(bundle, tmp_path, monkeypatch, shared):
    source = tmp_path / "from-path"
    source.mkdir()
    imports = ["avcodec-63.dll"] if shared else ["AVRT.dll"]
    (source / "ffmpeg.exe").write_bytes(pe_image(imports))
    (source / "ffprobe.exe").write_bytes(pe_image(delay=imports))
    (source / "avcodec-63.dll").write_bytes(pe_image(["swscale-10.dll"]))
    (source / "swscale-10.dll").write_bytes(pe_image())
    (source / "unrelated.dll").write_bytes(b"never copied")
    (source / "LICENSE").write_text("FFmpeg license")
    monkeypatch.setattr(bundle.shutil, "which", lambda name: str(source / (name + ".exe")))
    target = tmp_path / "packaged"
    bundle.copy_ffmpeg_binaries(target, None if shared else source)
    expected = {"ffmpeg.exe", "ffprobe.exe", "FFmpeg-LICENSE.txt"}
    if shared:
        expected |= {"avcodec-63.dll", "swscale-10.dll"}
    assert {p.name for p in target.iterdir()} == expected
    if shared:
        (source / "swscale-10.dll").unlink()
        with pytest.raises(RuntimeError, match="swscale-10.dll"):
            bundle.copy_ffmpeg_binaries(tmp_path / "bad", source)
    else:
        (source / "LICENSE").unlink()
        with pytest.raises(FileNotFoundError, match="FFmpeg license"):
            bundle.copy_ffmpeg_binaries(tmp_path / "unlicensed", source)


def test_shipped_python_imports_have_pyside_extensions(bundle):
    from qml_runtime import QT_BINARIES

    imported = set()
    for folder in (PACKAGING, PACKAGING.parent / "src"):
        for path in folder.rglob("*.py"):
            if path.is_relative_to(PACKAGING.parent / "src/tingmu/ui"):
                continue  # legacy QWidget sources are excluded by copy_app
            for node in ast.walk(ast.parse(path.read_text("utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("PySide6."):
                    imported.add(node.module.split(".")[1])
                if isinstance(node, ast.ImportFrom) and node.module == "PySide6":
                    imported.update(alias.name for alias in node.names if alias.name.startswith("Qt"))
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[1] for alias in node.names if alias.name.startswith("PySide6."))
    # Desktop development uses QQuickStyle; the portable bootstrap selects Basic
    # through the environment and loads the native QML plugins directly.
    assert {name + ".pyd" for name in imported - {"QtQuickControls2"}} <= set(QT_BINARIES)
    assert {"QtQuick.pyd", "QtOpenGL.pyd"} <= set(QT_BINARIES)
    assert not {"QtMultimediaWidgets.pyd", "QtQuickControls2.pyd"} & set(QT_BINARIES)
