import hashlib
import json
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
from threading import Thread
import time

import pytest

from tingmu import workspace_models as weights
from tingmu.batch import _apply_progress, _end_batches
from tingmu.task_progress import TransferMeter, batch_progress


def test_hub_download_reports_real_file_bytes_and_network_bytes(tmp_path, monkeypatch, capsys):
    """Exercise the pinned SDK with one previously completed file, then a fully cached retry."""
    import huggingface_hub as hub
    from huggingface_hub import constants
    repo = weights.WHISPER_MODELS["small"]
    commit = "a" * 40
    payload = b"offline test model" * 32768
    oid = hashlib.sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest()
    config = b"{}"
    config_oid = hashlib.sha1(b"blob 2\0{}").hexdigest()
    size = len(config) + len(payload)
    requests = []
    slow_transfer = False

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, body=True):
            if self.path.startswith("/api/models/"):
                data = ([{"type": "file", "oid": oid, "size": len(payload), "path": "model.bin"},
                         {"type": "file", "oid": config_oid, "size": len(config), "path": "config.json"}]
                        if "/tree/" in self.path else {"id": repo, "sha": commit, "siblings": [{"rfilename": "model.bin"}]})
                content = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            else:
                start = int(self.headers.get("Range", "bytes=0-")[6:-1])
                is_config = self.path.endswith("config.json")
                content = config if is_config else payload[start:]
                self.send_response(206 if start else 200)
                self.send_header("X-Repo-Commit", commit)
                self.send_header("ETag", '"' + (config_oid if is_config else oid) + '"')
                if start:
                    self.send_header("Content-Range", f"bytes {start}-{len(payload)-1}/{len(payload)}")
                if body:
                    requests.append(self.path.rsplit("/", 1)[-1])
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            if body:
                for pos in range(0, len(content), 65536):
                    try:
                        self.wfile.write(content[pos:pos + 65536])
                    except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError):
                        return
                    if len(content) > 65536:
                        time.sleep(.25 if slow_transfer else .055)

        def do_GET(self):
            self.respond()

        def do_HEAD(self):
            self.respond(False)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    endpoint = f"http://127.0.0.1:{server.server_port}"
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        # CI disables Hub entirely. Only this explicitly loopback-bound fixture
        # needs its HTTP client; the suite's external socket audit stays active.
        monkeypatch.setattr(constants, "is_offline_mode", lambda: False)
        monkeypatch.setattr(weights, "_assets", lambda _: (tmp_path, [repo]))
        monkeypatch.setattr(hub, "snapshot_download", partial(hub.snapshot_download, endpoint=endpoint, token=False))
        monkeypatch.setattr(constants, "DOWNLOAD_CHUNK_SIZE", 65536)
        snapshot = tmp_path / ("models--" + repo.replace("/", "--")) / "snapshots" / commit
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_bytes(config)
        weights._fetch_snapshot("small", repo)
        events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert requests == ["model.bin"]
        assert events[-1] == [size, size, len(payload)]
        assert any(ready == received + len(config) and 0 < received < len(payload)
                   for ready, total, received in events)
        weights._fetch_snapshot("small", repo)
        assert json.loads(capsys.readouterr().out.splitlines()[-1]) == [size, size, 0]
        assert requests == ["model.bin"]

        # The actual worker only admits cache paths inside the project. Give it
        # a unique isolated root even when pytest's tmp_path is on another drive.
        project_cache = Path(weights.__file__).resolve().parents[2] / ".cache"
        project_cache.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=project_cache) as folder:
            child_root = Path(folder)
            monkeypatch.setenv("TINGMU_CACHE_DIR", str(child_root))
            monkeypatch.setenv("HF_HOME", str(child_root / "huggingface"))
            monkeypatch.setenv("HF_ENDPOINT", endpoint)
            monkeypatch.setenv("HF_HUB_OFFLINE", "0")
            monkeypatch.setenv("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
            monkeypatch.setenv("HF_HUB_DISABLE_TELEMETRY", "1")
            monkeypatch.setattr(weights, "cache_root", lambda: child_root)
            monkeypatch.setattr(weights, "_assets", lambda _: (child_root / "models", [repo]))
            slow_transfer = True
            samples = []
            def received():
                return any(getattr(event, "details", {}).get("received", 0) > 0 for event in samples)
            with pytest.raises(InterruptedError):
                weights._download_repo("small", repo, samples.append, received)
            assert not list(child_root.rglob("*.incomplete"))
            slow_transfer = False
            weights._download_repo("small", repo, samples.append, lambda: False)
            assert samples[-1].details["completed"] == size and samples[-1].details["received"] == len(payload)
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_rate_excludes_reused_bytes_and_stops_on_idle(monkeypatch):
    events = []
    times = iter([0., .1, 2.1, 3.1, 5.1])
    monkeypatch.setattr("tingmu.task_progress.time.perf_counter", lambda: next(times))
    meter = TransferMeter("test", events.append)
    meter.update(900, 1000, 0)
    meter.update(980, 1000, 50)
    meter.update(980, 1000, 50)
    meter.update(980, 1000, 50)
    assert [e.details["rate"] for e in events] == [0, 25, 25, 0]
    assert events[-1].details["completed"] == 980 and events[-1].details["received"] == 50


def test_stage_switches_and_parallel_retry_progress():
    job = {}
    _apply_progress(job, dict(stage="逐句讲解", done=20, total=25, unit="句"))
    for index, first, last in [(1, 21, 23), (2, 24, 25)]:
        _apply_progress(job, batch_progress(index, first, last, last-first+1).details)
    _apply_progress(job, batch_progress(1, 21, 23, 3, status="partial", done=2).details)
    _apply_progress(job, batch_progress(3, 23, 23, 1, repair=True).details)
    assert [(b["id"], b["done"]) for b in job["batches"]] == [(2, 0), (3, 0), (1, 2)]
    _end_batches(job)
    assert [r["status"] for r in job["batches"]] == ["interrupted", "interrupted", "partial"]
    _apply_progress(job, dict(stage="译稿核验", done=3, total=25, unit="句"))
    assert job["stage_done"] == 3 and job["stage_total"] == 25 and not job["batches"]
