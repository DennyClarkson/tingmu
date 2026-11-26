"""The fresh core distribution owns no user assets or optional engines."""
import json
from pathlib import Path

import pytest


@pytest.fixture
def core(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "packaging"))
    import build_clean_install

    (tmp_path / "runtime/python/Lib/site-packages").mkdir(parents=True)
    build_clean_install.copy_release_documents(build_clean_install.ROOT, tmp_path)
    return build_clean_install


@pytest.mark.parametrize("document", ["使用说明.md", "THIRD_PARTY_NOTICES.md"])
@pytest.mark.parametrize("empty", [False, True])
def test_build_audit_requires_both_release_documents(tmp_path, core, document, empty):
    if empty:
        (tmp_path / document).write_bytes(b"")
    else:
        (tmp_path / document).unlink()
    with pytest.raises(ValueError, match=document):
        core.validate_core_release(tmp_path)


@pytest.mark.parametrize("unexpected", [
    "bin/ffprobe.exe", "bin/ffmpeg.exe", "runtime/asr/python.exe", "data/settings.json",
    ".cache/models/a", "runtime/python/Lib/site-packages/nvidia/cudnn/bin/cudnn64_9.dll",
    "runtime/python/Lib/site-packages/onnxruntime/capi", "runtime/python/Lib/site-packages/av.libs",
    "runtime/python/Lib/site-packages/faster_whisper-1.dist-info/RECORD",
    "runtime/python/Lib/site-packages/tokenizers", "runtime/python/Lib/site-packages/ctranslate2",
    "runtime/python/Lib/site-packages/hf_xet", "app/weights.safetensors",
])
def test_build_audit_rejects_optional_payloads(tmp_path, core, unexpected):
    assert core.validate_core_release(tmp_path)["model_payloads"] == 0
    payload = tmp_path / unexpected
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="Unexpected"):
        core.validate_core_release(tmp_path)


def test_clean_builder_ships_current_release_documents(tmp_path, core, monkeypatch):
    source = tmp_path / "installed"
    (source / "runtime/python/Lib/site-packages").mkdir(parents=True)
    (source / "tingmu-portable.json").write_text("{}", encoding="utf-8")
    (source / "使用说明.md").write_bytes(b"outdated")
    monkeypatch.setattr(core, "copy_packages", lambda _src, dst, *args: dst.mkdir())
    monkeypatch.setattr(core, "compile_launcher", lambda _src, dst: dst.write_bytes(b"launcher"))
    monkeypatch.setattr(core, "validate_qml_runtime", lambda root: {})
    destination = tmp_path / "release"
    core.build(destination, source, bytecode=False)
    guide = (destination / "使用说明.md").read_text("utf-8")
    for file, external, internal in (("portable.md", "(portable.en.md)", "(#english)"),
                                     ("portable.en.md", "(portable.md)", "(#chinese)")):
        original = (core.ROOT / "docs" / file).read_text("utf-8")
        assert external in original and original.rstrip().replace(external, internal) in guide
        assert f'id="{internal[2:-1]}"' in guide
    notices = "THIRD_PARTY_NOTICES.md"
    assert (destination / notices).read_bytes() == (core.ROOT / notices).read_bytes()
    manifest = json.loads((destination / "tingmu-portable.json").read_text("utf-8"))
    assert manifest["weight_audit"] == core.validate_core_release(destination)


def test_copy_app_only_includes_qml_frontend_and_ships_installer_lock(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "packaging"))
    from build_clean_install import copy_app

    copy_app(tmp_path)
    app = tmp_path / "app/tingmu"
    assert not (app / "ui").exists()
    assert (app / "quick/qml/Main.qml").is_file()
    for name in ("app-icon.svg", "app-icon.ico"):
        assert (app / "quick/brand" / name).read_bytes() == (
            Path(__file__).resolve().parents[1] / "src/tingmu/quick/brand" / name
        ).read_bytes()
    assert (app / "assets/runtime-deps/windows-cp312-x64.json").is_file()
    assert len(list((app / "quick/qml/fonts").glob("*.otf"))) == 4
