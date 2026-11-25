"""Offline coverage for SDK progress variants and the shipped runtime download path."""

from contextlib import ExitStack
import json
from types import SimpleNamespace
import threading

import pytest

from tingmu import runtime_install, workspace_models
from tingmu.task_progress import TransferMeter
from test_runtime_install import local_release as local_release


@pytest.mark.parametrize("mode", ["http", "xet", "idle-aggregate", "no-content-length"])
def test_hub_resumed_unknown_size_ignores_file_counts_and_duplicate_bars(tmp_path, monkeypatch, mode):
    import huggingface_hub

    repo = workspace_models.WHISPER_MODELS["small"]
    config = tmp_path / "config.json"
    config.write_bytes(b"{}")
    plan = [SimpleNamespace(filename=config.name, local_path=config, commit_hash="rev",
                            file_size=None, will_download=False),
            SimpleNamespace(filename="model.bin", file_size=None, will_download=True)]
    seen = []
    partial_reported = threading.Event()

    def record(line, **kwargs):
        sample = json.loads(line)
        seen.append(sample)
        if sample[2] > 0:
            partial_reported.set()

    def snapshot(repository, **kwargs):
        if kwargs.get("dry_run"):
            return plan
        bar = kwargs["tqdm_class"]
        with bar(unit="files", desc="Fetching 2 files", total=2) as files:
            files.update(1)
        with ExitStack() as stack:
            http_total = 100 if mode in ("http", "idle-aggregate") else None
            http = stack.enter_context(bar(unit="B", desc="model.bin", initial=20, total=http_total))
            if mode in ("xet", "idle-aggregate"):
                network = stack.enter_context(bar(unit="B", desc="Downloading bytes"))
                reconstructed = stack.enter_context(bar(unit="B", desc="Reconstructing (incomplete total...)",
                                                        initial=20, total=0))
            if mode == "xet":
                reconstructed.total = 100
                network.update(8)
                reconstructed.update(12)
            http.update(12)
            assert partial_reported.wait(3)
            http.update(68)
            if mode == "xet":
                network.update(28)
                reconstructed.update(68)
        (tmp_path / "model.bin").write_bytes(bytes(100))
        return str(tmp_path)

    monkeypatch.setattr(workspace_models, "_assets", lambda _: (tmp_path, [repo]))
    monkeypatch.setattr(workspace_models, "print", record, raising=False)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    workspace_models._fetch_snapshot("small", repo)
    estimate = workspace_models.REPOSITORY_BYTES[repo]
    assert seen[0] == [2, estimate, 0, True]
    if mode == "no-content-length":
        assert [34, estimate, 12, True] in seen
    else:
        assert [34, 102, 8 if mode == "xet" else 12] in seen
    assert seen[-1] == [102, 102, 36 if mode == "xet" else 80]


def test_smoothed_speed_eta_and_cached_bytes(monkeypatch):
    clock = [0.]
    monkeypatch.setattr("tingmu.task_progress.time.perf_counter", lambda: clock[0])
    seen = []
    meter = TransferMeter("fixture", lambda event: seen.append(event.details))
    meter.update(300, 1000, 0)
    clock[0] = 1
    meter.update(400, 1000, 100)
    assert seen[-1]["rate"] == 100 and seen[-1]["eta"] == 6
    clock[0] = 2
    meter.update(700, 1000, 400)
    assert 100 < seen[-1]["rate"] < 300
    assert seen[-1]["eta"] in (2, 3)
    clock[0] = 2.4
    meter.update(700, 1000, 400)
    assert seen[-1]["rate"] == seen[-2]["rate"]
    meter.stage("verifying")
    assert seen[-1]["eta"] is None and seen[-1]["rate"] == 0
    assert seen[-1]["completed"] == 700


def test_runtime_reports_packages_through_self_test(tmp_path, local_release, monkeypatch):
    release, requests = local_release
    sizes = [release("local-asr", {"core.py": b"one"}, name="core.whl"),
             release("local-asr", {"cpu.py": b"two"}, name="cpu.whl")]
    monkeypatch.setattr(runtime_install, "_verify", lambda *args: None)
    seen = []
    meter = TransferMeter("local-asr", lambda event: seen.append(event.details))
    runtime_install.install(tmp_path, "local-asr", meter, lambda: False)
    assert requests == ["/core.whl", "/cpu.whl"]
    connecting = [row for row in seen if row["stage"] == "connecting"]
    assert [(e["filename"], e["package"], e["packages"], e["completed"]) for e in connecting] == [
        ("core.whl", 1, 2, 0), ("cpu.whl", 2, 2, sizes[0])]
    for package in (1, 2):
        stages = [e["stage"] for e in seen if e.get("package") == package]
        assert stages.index("connecting") < stages.index("download") < stages.index("verifying") < stages.index("installing")
    assert any(e["rate"] > 0 for e in seen if e["stage"] == "download")
    assert [e["stage"] for e in seen[-2:]] == ["testing", "ready"]
    assert seen[-1]["completed"] == seen[-1]["total"] == sum(sizes)


def test_portable_gui_uses_bundled_console_python_with_utf8(tmp_path, monkeypatch):
    exe = tmp_path / "runtime/python/python.exe"
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(workspace_models, "portable_root", lambda: tmp_path)
    monkeypatch.setattr("sys.executable", str(tmp_path / "runtime/python/pythonw.exe"))
    command, environment = workspace_models._download_python()
    assert command[0] == str(exe) and "-u" in command and "utf8" in command
    assert environment["PYTHONIOENCODING"] == "utf-8"
