"""Check that Explorer receives every frame of the official portable launcher icon."""
import hashlib
from pathlib import Path
import struct
import sys

import pytest


PACKAGING = Path(__file__).resolve().parents[1] / "packaging"


def test_icon_change_invalidates_launcher_cache(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(PACKAGING))
    from bundle_files import launcher_hash, launcher_icon

    source = tmp_path / "packaging/Launcher.cs"
    source.parent.mkdir()
    source.write_bytes(b"same source")
    icon = launcher_icon(source)
    icon.parent.mkdir(parents=True)
    icon.write_bytes(b"first icon")
    first = launcher_hash(source)
    assert first != hashlib.sha256(source.read_bytes()).hexdigest()  # Unbranded releases.
    icon.write_bytes(b"updated icon")
    assert launcher_hash(source) != first


@pytest.mark.skipif(sys.platform != "win32", reason="Requires Windows PE resource APIs and the C# compiler")
def test_compiled_launcher_contains_all_official_icon_frames(tmp_path, monkeypatch):
    import ctypes as ct
    from ctypes import wintypes as win

    monkeypatch.syspath_prepend(str(PACKAGING))
    from bundle_files import compile_launcher, launcher_icon

    source = PACKAGING / "Launcher.cs"
    target = tmp_path / "Tingmu.exe"
    compile_launcher(source, target)
    icon = launcher_icon(source).read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", icon)
    assert (reserved, kind) == (0, 1)

    kernel = ct.WinDLL("kernel32", use_last_error=True)
    ptr = ct.c_void_p
    group_name_callback = ct.WINFUNCTYPE(win.BOOL, ptr, ptr, ptr, ct.c_ssize_t)

    def api(name, result, *arguments):
        return ct.WINFUNCTYPE(result, *arguments, use_last_error=True)((name, kernel))

    load = api("LoadLibraryExW", ptr, win.LPCWSTR, ptr, win.DWORD)
    free = api("FreeLibrary", win.BOOL, ptr)
    enum = api("EnumResourceNamesW", win.BOOL, ptr, ptr, group_name_callback, ct.c_ssize_t)
    find = api("FindResourceW", ptr, ptr, ptr, ptr)
    size_of = api("SizeofResource", win.DWORD, ptr, ptr)
    load_resource = api("LoadResource", ptr, ptr, ptr)
    lock = api("LockResource", ptr, ptr)
    module = load(str(target), None, 2)  # LOAD_LIBRARY_AS_DATAFILE
    assert module, ct.WinError(ct.get_last_error())

    def resource(name, kind):
        handle = find(module, name, kind)
        assert handle
        address = lock(load_resource(module, handle))
        assert address
        return ct.string_at(address, size_of(module, handle))

    groups = []

    @group_name_callback
    def read_group(_module, kind, name, _context):
        groups.append(resource(name, kind))
        return True

    try:
        assert enum(module, 14, read_group, 0)  # RT_GROUP_ICON
        group, = groups
        assert struct.unpack_from("<HHH", group) == (0, 1, count)
        sizes = set()
        for i in range(count):
            entry = struct.unpack_from("<BBBBHHII", icon, 6 + i * 16)
            embedded = struct.unpack_from("<BBBBHHIH", group, 6 + i * 14)
            assert embedded[:4] == entry[:4]
            length, offset = entry[-2:]
            assert resource(embedded[-1], 3) == icon[offset:offset + length]  # RT_ICON
            sizes.add((entry[0] or 256, entry[1] or 256))
        assert {(16, 16), (32, 32), (48, 48), (256, 256)} <= sizes
    finally:
        assert free(module)
