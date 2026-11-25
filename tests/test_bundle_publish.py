"""Publication and cache regression cases for upgrading an existing portable folder."""
import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def builder(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "packaging"))
    import build_portable
    return build_portable


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


@pytest.mark.parametrize("blocked_restore", [False, True])
def test_publish_recovers_whole_previous_program(builder, tmp_path, monkeypatch, blocked_restore):
    installation = tmp_path / "听幕"
    stage = installation / ".stage"
    for root, content in ((installation, b"old"), (stage, b"new")):
        for name in ("runtime/python/TingmuRuntime.exe", "bin/ffmpeg.exe", "app/bootstrap.py", "Tingmu.exe"):
            write(root, name, content)
    for personal in ("data/settings.json", ".cache/models/weights"):
        write(installation, personal, b"personal")
    rename = Path.rename

    def locked(path, target):
        if path == stage / "app" or (blocked_restore and path == installation / "runtime" and target == stage / "runtime"):
            raise PermissionError("locked for test")
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", locked)
    expected = RuntimeError if blocked_restore else PermissionError
    with pytest.raises(expected):
        builder.publish(stage, installation, ("runtime", "bin", "app", "Tingmu.exe"))
    backups = list(installation.glob(".tingmu-previous-*"))
    if blocked_restore:
        assert len(backups) == 1 and backups[0].parent == installation
        assert (backups[0] / "runtime/python/TingmuRuntime.exe").read_bytes() == b"old"
    else:
        assert backups == []
        assert (installation / "runtime/python/TingmuRuntime.exe").read_bytes() == b"old"
    for name in ("bin/ffmpeg.exe", "app/bootstrap.py", "Tingmu.exe"):
        assert (installation / name).read_bytes() == b"old"
    for name in ("data/settings.json", ".cache/models/weights"):
        assert (installation / name).read_bytes() == b"personal"


def test_personal_data_import_keeps_existing_cache_and_rejects_linked_targets(builder, tmp_path):
    source, target = tmp_path / "source", tmp_path / "installed"
    for name in ("old", "new", "temp.lock"):
        write(source, name, b"source")
    write(target, "old", b"personal")
    builder.copy_missing_tree(source, target)
    assert (target / "old").read_bytes() == b"personal"
    assert (target / "new").read_bytes() == b"source"
    assert not (target / "temp.lock").exists()
    outside = tmp_path / "outside"
    outside.mkdir()
    write(source, "linked/file", b"must stay local")
    link = target / "linked"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="linked build destination"):
        builder.copy_missing_tree(source, target)
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("refresh", [False, True])
@pytest.mark.parametrize("was_clean", [False, True])
def test_build_stages_owned_files_and_refresh_preserves_caches(builder, tmp_path, monkeypatch,
                                                              refresh, was_clean):
    installation, repo = tmp_path / "installed", tmp_path / "repo"
    write(installation, "runtime/python/TingmuRuntime.exe", b"old")
    write(installation, "runtime/python/python.exe", b"previous interpreter")
    write(installation, "bin/ffmpeg.exe", b"old")
    write(installation, "runtime/asr/python.exe", b"personal")
    write(installation, ".cache/runtime-deps/local-asr/example.pyd", b"personal")
    write(installation, "app/stale.py", b"old")
    write(installation, "tingmu-portable.json", json.dumps({"format": 2, "clean_install": was_clean}).encode())
    write(installation, "data/settings.json", b"personal")
    write(installation, ".cache/speech-models/old", b"personal")
    write(repo, "packaging/Launcher.cs", b"source")
    write(repo, "docs/portable.md", b"docs")
    write(repo, "THIRD_PARTY_NOTICES.md", b"notices")
    for name in ("old", "new"):
        write(repo, ".cache/speech-models/" + name, b"source")
    monkeypatch.setattr(builder, "OUT", installation)
    monkeypatch.setattr(builder, "ROOT", repo)
    monkeypatch.setattr(builder, "copy_app", lambda root: write(root, "app/bootstrap.py", b"new"))
    monkeypatch.setattr(builder, "compile_launcher", lambda source, target: target.write_bytes(b"new"))

    builds = []

    def copy_runtime(environment, target):
        assert environment == repo / ".venv"
        assert target.name == "python"
        builds.append(target)
        write(target, "pythonw.exe", b"new")
        write(target, "python.exe", b"new")

    def validate(root):
        assert [root / "runtime/python"] == builds
        assert not (root / "runtime/asr").exists()
        assert not (root / "bin").exists()
        assert not list((root / "runtime").rglob("*.onnx"))
        assert (root / "使用说明.md").read_bytes() == b"docs"
        assert (root / "THIRD_PARTY_NOTICES.md").read_bytes() == b"notices"
        return {"model_payloads": 0}

    def manifest(marker, stage, runtime):
        assert (stage / "Tingmu.exe").is_file()
        assert (stage / "app/bootstrap.py").is_file()
        assert (runtime / "runtime/python/TingmuRuntime.exe").read_bytes() == (b"old" if refresh else b"new")
        assert (installation / "runtime/python/TingmuRuntime.exe").read_bytes() == b"old"
        return marker

    monkeypatch.setattr(builder, "copy_runtime", copy_runtime)
    monkeypatch.setattr(builder, "validate_core_release", validate)
    monkeypatch.setattr(builder, "precompile", lambda app, interpreter, include_runtime: {})
    monkeypatch.setattr(builder, "release_manifest", manifest)
    builder.build(refresh)
    assert len(builds) == (0 if refresh else 1)
    assert (installation / "runtime/python/TingmuRuntime.exe").read_bytes() == (b"old" if refresh else b"new")
    assert (installation / "bin/ffmpeg.exe").read_bytes() == b"old"
    assert (installation / "runtime/asr/python.exe").read_bytes() == b"personal"
    assert (installation / ".cache/runtime-deps/local-asr/example.pyd").read_bytes() == b"personal"
    assert (installation / "Tingmu.exe").read_bytes() == b"new"
    assert (installation / "app/bootstrap.py").read_bytes() == b"new"
    assert not (installation / "app/stale.py").exists()
    assert (installation / "使用说明.md").read_bytes() == b"docs"
    assert (installation / "THIRD_PARTY_NOTICES.md").read_bytes() == b"notices"
    marker = json.loads((installation / "tingmu-portable.json").read_bytes())
    assert marker["launcher_source_sha256"]
    assert marker["clean_install"] == (was_clean if refresh else True)
    if not refresh:
        assert marker["weight_audit"] == {"model_payloads": 0}
    assert (installation / "data/settings.json").read_bytes() == b"personal"
    assert (installation / ".cache/speech-models/old").read_bytes() == b"personal"
    assert not (installation / ".cache/speech-models/new").exists()


@pytest.mark.parametrize("flags", [[], ["--include-models"], ["--import-user-data"]])
def test_cli_builds_only_model_free_release(builder, tmp_path, monkeypatch, flags):
    calls = []
    monkeypatch.setattr(builder, "OUT", tmp_path)
    monkeypatch.setattr(builder.sys, "argv", ["build_portable.py", *flags])
    monkeypatch.setattr(builder, "build", lambda refresh: calls.append(refresh))
    if flags:
        with pytest.raises(SystemExit):
            builder.main()
        assert calls == []
    else:
        builder.main()
        assert calls == [False]
