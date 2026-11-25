"""Exercise the release gates and the boundary between the pristine and tested bundle."""
import hashlib
import json
from pathlib import Path
import zipfile

import pytest


@pytest.fixture
def release(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "packaging"))
    import build_release

    monkeypatch.setattr(build_release, "ROOT", tmp_path)
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3-rc.1"\n', encoding="utf-8")
    for name, source in build_release.RELEASE_DOCUMENTS.items():
        path = tmp_path / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    return build_release


def make_bundle(release, root):
    (root / "app").mkdir(parents=True)
    (root / "runtime/python/Lib/site-packages").mkdir(parents=True)
    for name in ("Tingmu.exe", "app/bootstrap.py", "runtime/python/Lib/site-packages/core.py"):
        (root / name).write_bytes(b"fixture")
    for name, source in release.RELEASE_DOCUMENTS.items():
        (root / name).write_bytes((release.ROOT / source).read_bytes())
    manifest = {"clean_install": True, "version": "1.2.3-rc.1", "weight_audit": {"model_payloads": 0},
                "qml_runtime": {"pe_files_checked": 42}}
    (root / "tingmu-portable.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def smoke_report(release, *, with_media=True):
    required = release.REQUIRED_CHECKS | (release.MEDIA_CHECKS if with_media else set())
    return {"version": "1.2.3-rc.1", "ready": True, "checks": dict.fromkeys(required, True),
            "tray_available": False, "errors": [], "qml_warnings": [], "new_bytecode": [],
            "blocked_network_events": []}


def test_exact_version_checked_before_build(release):
    assert release.project_version(None) == release.project_version("1.2.3-rc.1") == "1.2.3-rc.1"
    output = release.ROOT / "absent"
    with pytest.raises(ValueError, match="Expected version"):
        release.build_release(output, "1.2.3rc1")
    assert not output.exists()


@pytest.mark.parametrize(("field", "value"), [
    ("clean_install", 1), ("version", "1.2.3"), ("qml_runtime", {}), ("qml_runtime", {"pe_files_checked": 0}),
    ("weight_audit", {"model_payloads": 1}), ("weight_audit", {"model_payloads": False}),
])
def test_invalid_manifest_blocks_release(release, field, value):
    root = release.ROOT / "Tingmu"
    manifest = make_bundle(release, root)
    manifest[field] = value
    (root / "tingmu-portable.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="manifest"):
        release.audit_bundle(root, "1.2.3-rc.1")


@pytest.mark.parametrize("unwanted", [
    "data/logs/portable-smoke.json", ".cache/portable-smoke/x", "bin/ffmpeg.exe", "runtime/asr/python.exe",
    "runtime/python/Lib/site-packages/nvidia/dummy", "app/weight.safetensors", "app/debug.log",
    "app/leftover/study.db", ".tingmu-build.lock",
])
def test_audit_checks_actual_tree(release, unwanted):
    root = release.ROOT / "Tingmu"
    make_bundle(release, root)
    path = root / unwanted
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    with pytest.raises(ValueError, match="Unexpected"):
        release.audit_bundle(root, "1.2.3-rc.1")


def test_size_and_document_gates(release, monkeypatch):
    root = release.ROOT / "Tingmu"
    make_bundle(release, root)
    monkeypatch.setattr(release, "MAX_BYTES", 1)
    with pytest.raises(ValueError, match="budget"):
        release.audit_bundle(root, "1.2.3-rc.1")
    (root / "使用说明.md").write_bytes(b"stale")
    with pytest.raises(ValueError, match="document"):
        release.audit_bundle(root, "1.2.3-rc.1")


@pytest.mark.parametrize("missing", [
    "qml_window", "isolated_data", "portable_imports", "bundled_open_fonts", "first_run_guide",
    "first_run_skip_persisted", "workspace_llm_concurrency", "no_new_bytecode",
    "decode", "seek", "rate", "audio_switch", "pause",
])
def test_missing_smoke_check_blocks_publication(release, missing):
    report = smoke_report(release)
    del report["checks"][missing]
    with pytest.raises(ValueError, match=missing):
        release.check_smoke(report, "1.2.3-rc.1", True)


@pytest.mark.parametrize(("key", "value"), [
    ("ready", 1), ("version", "old"), ("qml_warnings", ["warning"]), ("new_bytecode", ["startup.pyc"]),
    ("errors", ["decode error"]), ("tray_available", True), ("blocked_network_events", ["socket.connect"]),
])
def test_smoke_independent_gates_and_optional_tray(release, key, value):
    report = smoke_report(release, with_media=False)
    release.check_smoke(report, "1.2.3-rc.1", False)
    report[key] = value
    with pytest.raises(ValueError):
        release.check_smoke(report, "1.2.3-rc.1", False)


@pytest.mark.parametrize("failure", [None, "launcher", "early bytecode"])
def test_build_only_archives_pristine_tree_and_preserves_diagnostics(release, monkeypatch, failure):
    media = release.ROOT / "双音轨 sample.mkv"
    media.touch()
    output = release.ROOT / "artifacts"
    monkeypatch.setattr(release, "build_portable", lambda clean, log: make_bundle(release, clean))

    class FakeLauncher:
        def __init__(self, command, *, cwd, env, **_):
            assert command == [cwd / "Tingmu.exe", "--portable-smoke", "--smoke-media", media]
            assert " " in cwd.name and not cwd.name.isascii()
            assert env["QT_QPA_PLATFORM"] == "offscreen" and env["QT_QUICK_BACKEND"] == "software"
            assert "TINGMU_QWEN_PYTHON" not in env and "OPENAI_API_KEY" not in env
            assert "venv" not in env["PATH"]
            (cwd / ".cache/portable-smoke").mkdir(parents=True)
            logs = cwd / "data/logs"
            logs.mkdir(parents=True)
            (logs / "application.log").write_bytes(b"isolated run")
            (logs / "portable-smoke.json").write_text(json.dumps(smoke_report(release)), encoding="utf-8")
            if failure == "early bytecode":
                (cwd / "app/early.pyc").touch()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def wait(self, timeout):
            return int(failure == "launcher")

    monkeypatch.setattr(release.subprocess, "Popen", FakeLauncher)
    monkeypatch.setenv("SystemRoot", str(release.ROOT / "windows"))
    monkeypatch.setenv("TINGMU_QWEN_PYTHON", "external interpreter")
    if failure:
        output.mkdir()
        previous = output / "SHA256SUMS.txt"
        previous.write_bytes(b"previous release")
        with pytest.raises((RuntimeError, ValueError), match="exited|New bytecode"):
            release.build_release(output, media=media)
        assert previous.read_bytes() == b"previous release"
        assert not list(output.glob("*.zip"))
    else:
        summary = release.build_release(output, media=media)
        archive = output / summary["archive"]
        assert archive.name == "Tingmu-1.2.3-rc.1-windows-x64-portable.zip"
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        assert summary["sha256"] == digest
        assert summary["pe_files_checked"] == 42 and summary["model_payloads"] == 0
        assert summary["zip_bytes"] == archive.stat().st_size
        assert (output / "SHA256SUMS.txt").read_bytes() == f"{digest}  {archive.name}\n".encode()
        assert json.loads((output / "release-summary.json").read_bytes()) == summary
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            assert names == sorted(names)
            assert all(name.startswith("Tingmu/") for name in names)
            assert not any(name.startswith(("Tingmu/data/", "Tingmu/.cache/")) for name in names)
            assert "Tingmu/使用说明.md" in names and "Tingmu/THIRD_PARTY_NOTICES.md" in names
            assert bundle.getinfo("Tingmu/使用说明.md").flag_bits & 0x800
            assert summary["file_count"] == sum(not item.is_dir() for item in bundle.infolist())
            assert summary["unpacked_bytes"] == sum(item.file_size for item in bundle.infolist())
            assert bundle.testzip() is None
    assert (output / "diagnostics/application.log").read_bytes() == b"isolated run"
    assert json.loads((output / "diagnostics/portable-smoke.json").read_bytes()) == smoke_report(release)
