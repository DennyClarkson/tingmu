"""Copy and verify the Windows Qt and FFmpeg dependencies from installed binaries."""
from __future__ import annotations

import filecmp
import mmap
from pathlib import Path
import re
import shutil
import struct

from qml_runtime import QML_MODULES, QT_BINARIES, QT_TRANSLATIONS


# avrt.dll, for example, is part of Windows, not FFmpeg.
FFMPEG_DLL = re.compile(r"(?:lib)?(?:avcodec|avdevice|avfilter|avformat|avutil|postproc|swresample|swscale)(?:-\d+)?\.dll$")
QT_CRT = {"msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "vcruntime140.dll", "vcruntime140_1.dll"}


def _read_imports(image: mmap.mmap) -> set[str]:
    """Read DLL names from the PE32/PE32+ ordinary and delay-load directories."""
    if image[:2] != b"MZ":
        raise ValueError("missing DOS header")
    pe, = struct.unpack_from("<I", image, 0x3c)
    if image[pe:pe + 4] != b"PE\0\0":
        raise ValueError("missing PE signature")
    sections, = struct.unpack_from("<H", image, pe + 6)
    header_size, = struct.unpack_from("<H", image, pe + 20)
    header = pe + 24
    magic, = struct.unpack_from("<H", image, header)
    if magic == 0x20b:
        directories = header + 112
        base, = struct.unpack_from("<Q", image, header + 24)
    elif magic == 0x10b:
        directories = header + 96
        base, = struct.unpack_from("<I", image, header + 28)
    else:
        raise ValueError("unknown optional header")
    count, = struct.unpack_from("<I", image, directories - 4)
    section_table = header + header_size
    if directories + count * 8 > section_table:
        raise ValueError("invalid data directory count")
    ranges = [struct.unpack_from("<III", image, section_table + index * 40 + 12)
              for index in range(sections)]

    def file_offset(rva: int) -> int:
        for address, size, offset in ranges:
            if address <= rva < address + size and offset + rva - address < len(image):
                return offset + rva - address
        raise ValueError(f"unmapped RVA {rva:#x}")

    names = set()
    for slot, stride, name_field in ((1, 20, 12), (13, 32, 4)):
        if slot >= count:
            continue
        rva, size = struct.unpack_from("<II", image, directories + slot * 8)
        if not rva:
            continue
        for offset in range(0, size - stride + 1, stride):
            entry = file_offset(rva + offset)
            if image[entry:entry + stride] == bytes(stride):
                break
            name_rva, = struct.unpack_from("<I", image, entry + name_field)
            if slot == 13 and not (image[entry] & 1):
                name_rva -= base  # Pre-VC7 delay descriptor stores virtual addresses.
            start = file_offset(name_rva)
            end = image.find(b"\0", start, start + 1024)
            if end <= start:
                raise ValueError("invalid imported DLL name")
            names.add(image[start:end].decode("ascii").lower())
        else:
            raise ValueError("unterminated import directory")
    return names


def _inspect(path: Path, *, check_icu: bool = False) -> tuple[set[str], bool]:
    try:
        with path.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as image:
            names = _read_imports(image)
            # ICU data is a resource, so its name does not appear in an import table.
            # QtCore also imports Windows' ICU API (icuuc.dll), whose data is provided by Windows.
            needs_icu = check_icu and (
                path.name.lower().startswith("qt6webengine")
                or image.find(b"icudtl.dat") >= 0 or image.find("icudtl.dat".encode("utf-16le")) >= 0
            )
        return names, needs_icu
    except (ValueError, struct.error, OSError) as exc:
        raise RuntimeError(f"Cannot read Windows PE imports in {path}: {exc}") from exc


def _dll_closure(seeds: set[Path], directory: Path, *, qt: bool) -> tuple[set[Path], bool]:
    """Ignore system imports, follow DLLs shipped in the binary folder or Qt root."""
    seen = set()
    needs_icu = False
    pending = list(seeds)
    indexes: dict[Path, dict[str, Path]] = {}

    def index(folder: Path) -> dict[str, Path]:
        if folder not in indexes:
            indexes[folder] = {p.name.lower(): p for p in folder.iterdir() if p.suffix.lower() == ".dll"}
        return indexes[folder]

    while pending:
        binary = pending.pop()
        if binary in seen:
            continue
        seen.add(binary)
        imports, uses_icu = _inspect(binary, check_icu=qt)
        needs_icu |= uses_icu
        for name in imports:
            dependency = index(binary.parent).get(name) or index(directory).get(name)
            if dependency:
                pending.append(dependency)
            elif FFMPEG_DLL.fullmatch(name) or (qt and (name.startswith(("qt6", "pyside6")) or name in QT_CRT)):
                raise RuntimeError(f"{binary} imports missing DLL {name}")
    return seen, needs_icu


def _require(path: Path) -> Path:
    if not path.is_file():
        raise RuntimeError(f"Portable Qt Quick runtime is incomplete: {path}")
    return path


def _qml_files(qml: Path) -> tuple[set[Path], set[str]]:
    """Resolve qmldir defaults/dependencies plus imports in the selected style's QML."""
    modules: set[str] = set()
    files = set(qml.glob("*.qmltypes"))
    pending = list(QML_MODULES)

    def add_module(identifier: str) -> None:
        # QML itself is built into QtQml. Quoted imports are local JS/resources.
        if identifier != "QML" and re.fullmatch(r"\w+(?:\.\w+)*", identifier):
            pending.append(identifier.replace(".", "/"))

    while pending:
        module = pending.pop()
        if module in modules:
            continue
        modules.add(module)
        folder = qml / module
        descriptor = _require(folder / "qmldir")
        members = {p for p in folder.iterdir() if p.is_file()}
        files.update(members)
        for line in descriptor.read_text("utf-8").splitlines():
            words = line.split("#", 1)[0].split()
            if words[:2] == ["optional", "import"]:
                continue  # e.g. Fusion, Material; the launcher selects Basic.
            if words[:1] in (["default"], ["optional"]):
                words = words[1:]
            if len(words) < 2:
                continue
            if words[0] in {"import", "depends"}:
                add_module(words[1])
            elif words[0] == "plugin":
                plugin_dir = folder / words[2] if len(words) > 2 else folder
                files.add(_require(plugin_dir / (words[1] + ".dll")))
            elif words[0] == "typeinfo" or words[-1].endswith((".qml", ".js", ".mjs")):
                files.add(_require(folder / words[-1]))
        for path in members:
            if path.suffix == ".qml":
                # Qt ships QDoc examples in block comments (some import obsolete modules).
                source = re.sub(r"/\*.*?\*/", "", path.read_text("utf-8"), flags=re.S)
                for match in re.finditer(r"(?m)^\s*import\s+([\w.]+)", source):
                    add_module(match[1])
    return files, modules


def _qt_roots(qt: Path) -> tuple[set[Path], set[str]]:
    files = {_require(qt / f) for f in (*QT_BINARIES, *QT_TRANSLATIONS, "__init__.py")}
    files.update(qt.glob("*.py"))
    files.update((qt / "support").glob("*.py"))
    for pattern in ("plugins/tls/*.dll", "plugins/networkinformation/*.dll",
                    "plugins/imageformats/qgif.dll", "plugins/imageformats/qpng.dll", "openssl/*.dll"):
        files.update(qt.glob(pattern))
    qml_files, modules = _qml_files(qt / "qml")
    return files | qml_files, modules


def _is_binary(path: Path) -> bool:
    return path.suffix.lower() in {".dll", ".pyd"}


def validate_qt_directory(qt: Path) -> dict:
    """Check every shipped PE, including binaries outside the minimum allowlist."""
    _, modules = _qt_roots(qt)
    seeds = {p for p in qt.rglob("*") if _is_binary(p) and p.is_file()}
    binaries, needs_icu = _dll_closure(seeds, qt, qt=True)
    if needs_icu:
        _require(qt / "resources/icudtl.dat")
    return {"modules": sorted(modules), "binaries": list(QT_BINARIES),
            "pe_files_checked": len(binaries), "icu_data_required": needs_icu}


def copy_pyside_runtime(source_qt: Path, destination_qt: Path) -> None:
    """Populate a fresh staging directory; the caller publishes the runtime after validation."""
    files, _ = _qt_roots(source_qt)
    binaries, needs_icu = _dll_closure({p for p in files if _is_binary(p)}, source_qt, qt=True)
    files.update(binaries)
    if needs_icu:
        files.add(_require(source_qt / "resources/icudtl.dat"))
    destination_qt.mkdir(parents=True, exist_ok=False)
    for source in sorted(files):
        target = destination_qt / source.relative_to(source_qt)
        if source.resolve() != source.absolute():
            raise ValueError(f"Unexpected runtime link: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def copy_ffmpeg_binaries(destination_bin: Path, source_bin: Path | None = None) -> None:
    """Copy static tools or their recursive sibling DLL dependencies without networking."""
    files: dict[str, Path] = {}
    for name in ("ffmpeg", "ffprobe"):
        if source_bin is None:
            found = shutil.which(name)
            if not found:
                raise RuntimeError(f"Missing {name} on PATH")
            executable = Path(found)
        else:
            executable = source_bin / (name + ".exe")
        binaries, _ = _dll_closure({executable}, executable.parent, qt=False)
        for binary in binaries:
            previous = files.get(binary.name.lower())
            if previous and previous != binary and not filecmp.cmp(previous, binary, shallow=False):
                raise RuntimeError(f"Incompatible FFmpeg copies of {binary.name}: {previous} and {binary}")
            files[binary.name.lower()] = binary
        if name == "ffmpeg":
            for folder in (executable.parent, executable.parent.parent):
                for license_name in ("FFmpeg-LICENSE.txt", "LICENSE.txt", "LICENSE"):
                    license_file = folder / license_name
                    if license_file.is_file():
                        files["ffmpeg-license.txt"] = license_file
                        break
                if "ffmpeg-license.txt" in files:
                    break
    if source_bin is not None and "ffmpeg-license.txt" not in files:
        raise FileNotFoundError(f"Missing FFmpeg license in the source distribution: {source_bin}")
    destination_bin.mkdir(parents=True, exist_ok=True)
    for name, source in files.items():
        target = destination_bin / ("FFmpeg-LICENSE.txt" if name == "ffmpeg-license.txt" else source.name)
        shutil.copy2(source, target)
