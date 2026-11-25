import io
from pathlib import Path

import pytest


def wheel(root, name, paths, *, version="1", requires=()):
    info = root / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir()
    metadata = f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
    (info / "METADATA").write_text(metadata + "".join(f"Requires-Dist: {r}\n" for r in requires))
    paths = [*paths, f"{info.name}/METADATA", f"{info.name}/RECORD"]
    (info / "RECORD").write_text("".join(f"{p},,\n" for p in paths))


def test_main_wheel_ownership_keeps_runtime_extras_and_shared_files(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "packaging"))
    import runtime_pruning

    project = tmp_path / "pyproject.toml"
    project.write_text('[project]\ndependencies=["app[solver]"]\n'
                       '[project.optional-dependencies]\ngpu=["gpu"]\ndev=["tester"]\n')
    monkeypatch.setattr(runtime_pruning, "PROJECT", project)
    wheel(tmp_path, "app", ["app/core.py"], requires=["solver; extra == 'solver'"])
    wheel(tmp_path, "solver", ["namespace/active.py", "namespace/shared.py"])
    wheel(tmp_path, "gpu", ["nvidia/bin/gpu.dll"])
    wheel(tmp_path, "tester", ["test_cli.py", "_tester/main.py", "namespace/dev.py", "namespace/shared.py"],
          requires=["solver"])
    wheel(tmp_path, "unused-old-sdk", ["legacy.py"])
    ignore = runtime_pruning.site_packages_ignore(tmp_path, "python")
    assert ignore(tmp_path, ["app", "legacy.py", "test_cli.py", "_tester", "tester-1.dist-info",
                             "namespace", "nvidia", "solver-1.dist-info"]) == {
                                 "legacy.py", "test_cli.py", "_tester", "tester-1.dist-info", "nvidia"}
    assert ignore(tmp_path / "namespace", ["active.py", "dev.py", "shared.py"]) == {"dev.py"}


def test_qwen_keeps_audio_loader_stubs_and_assets(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "packaging"))
    from runtime_pruning import site_packages_ignore

    wheel(tmp_path, "qwen-asr", ["qwen_asr/model.py"], version="0.0.6")
    wheel(tmp_path, "gradio", ["gradio/demo.py"])
    wheel(tmp_path, "librosa", ["librosa/__init__.pyi", "librosa/core/__init__.pyi"])
    ignore = site_packages_ignore(tmp_path, "asr")
    assert ignore(tmp_path, ["gradio", "qwen_asr", "librosa"]) == {"gradio"}
    assert ignore(tmp_path / "librosa/core", ["__init__.pyi", "other.pyi"]) == {"other.pyi"}
    assert ignore(tmp_path / "torch/lib", ["backend.lib", "backend.dll", "backend.pdb"]) == {
        "backend.lib", "backend.pdb"}
    assert ignore(tmp_path / "faster_whisper", ["tests", "assets", "include"]) == {"tests", "include"}
    assert not ignore(tmp_path / "faster_whisper/assets", ["silero_vad.onnx"])
    assert not ignore(tmp_path / "pycantonese", ["data", "jyutping"])
    assert not ignore(tmp_path / "certifi", ["cacert.pem"])


@pytest.mark.parametrize(("status", "content_range", "body", "works"), [
    (206, "bytes 2-4/10", b"abc", True), (206, "bytes 0-2/10", b"abc", False),
    (200, "bytes 2-4/10", b"abc", False), (206, "bytes 2-4/10", b"ab", False),
    (206, "bytes 2-4/10", b"abcd", False),
])
def test_maintenance_downloader_validates_ranges(monkeypatch, status, content_range, body, works):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    import asr_download

    def open_fixture(request, timeout):
        assert request.get_header("Range") == "bytes=2-4"
        stream = io.BytesIO(body)
        stream.headers = {"Content-Range": content_range}
        stream.status = status
        return stream

    monkeypatch.setattr(asr_download, "urlopen", open_fixture)
    if works:
        assert asr_download.fetch_range("https://example.invalid/file", 2, 4, 10) == body
    else:
        with pytest.raises(ValueError):
            asr_download.fetch_range("https://example.invalid/file", 2, 4, 10)
