"""Offline, synthetic Before/After workloads. Run directly with the repository venv."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import ipaddress
import json
import os
from pathlib import Path
import platform
from statistics import median
import sys
import tempfile
from threading import Thread
from time import perf_counter
import tracemalloc
from unittest.mock import patch
from urllib.request import Request
import wave


def local_only(event, args):
    if event not in ("socket.connect", "socket.getaddrinfo"):
        return
    addr = args[1][0] if event == "socket.connect" else args[0]
    if addr not in (None, "localhost") and not ipaddress.ip_address(addr).is_loopback:
        raise RuntimeError("Performance measurements allow loopback only")


def milliseconds(call, repeat=7):
    call()
    samples = []
    for _ in range(repeat):
        start = perf_counter()
        call()
        samples.append((perf_counter() - start) * 1000)
    return round(median(samples), 3)


def peak_kib(call):
    tracemalloc.start()
    try:
        call()
        return round(tracemalloc.get_traced_memory()[1] / 1024, 1)
    finally:
        tracemalloc.stop()


def measure_http():
    from tingmu.providers import _open

    class Server(ThreadingHTTPServer):
        request_queue_size = 32
        accepts = 0

        def get_request(self):
            connection = super().get_request()
            self.accepts += 1
            return connection

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):
            pass

    def fetch(_):
        with _open(Request(url), timeout=3) as response:
            assert response.read() == b"ok"

    counts, durations = [], []
    with patch("urllib.request.getproxies", return_value={}):
        for _ in range(5):
            with Server(("127.0.0.1", 0), Handler) as server:
                url = f"http://127.0.0.1:{server.server_port}/sample"
                worker = Thread(target=server.serve_forever, args=(0.01,), daemon=True)
                worker.start()
                try:
                    start = perf_counter()
                    with ThreadPoolExecutor(8) as pool:
                        list(pool.map(fetch, range(48)))
                    durations.append((perf_counter() - start) * 1000)
                    counts.append(server.accepts)
                finally:
                    server.shutdown()
                    worker.join()
    return {"http_48_requests_8_workers_ms": round(median(durations), 3),
            "http_accepted_tcp_connections": median(counts)}


def measure_compute(folder):
    import numpy as np
    from tingmu import richtext, speech
    from tingmu.diagnostics import AppLog

    paragraph = "A quiet paragraph with a harmless [reference](https://example.org/a_(b)).\n"
    prose = (paragraph * 1400)[:89 * 1024]
    plain = ("A quiet paragraph with no links.\n" * 3000)[:89 * 1024]
    result = {"sanitize_89k_mixed_ms": milliseconds(lambda: richtext._sanitize(prose)),
              "sanitize_89k_plain_ms": milliseconds(lambda: richtext._sanitize(plain))}
    log = AppLog(folder)

    def suppressed_events():
        for _ in range(1000):
            log.event("progress", "synthetic progress", "DEBUG", progress=0.5)

    try:
        result["log_1000_disabled_ms"] = milliseconds(suppressed_events)
        for i in range(200):
            log.event("progress", "synthetic progress", progress=i)
        result["log_read_200_ms"] = milliseconds(log.read)
    finally:
        log.close()
    rng = np.random.default_rng(948446)
    window = np.hanning(4097)[:-1].astype("f")
    frames = rng.normal(size=(512, 4096)).astype("f") * window
    result["overlap_512x4096_ms"] = milliseconds(lambda: speech._overlap_add(frames, 1024, window))
    source, dest = folder / "source.wav", folder / "filtered.wav"
    with wave.open(str(source), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(rng.integers(-14000, 14000, size=16000 * 10, dtype="<i2").tobytes())

    def suppress():
        speech.suppress_repetitive_background(source, dest, lambda: None)

    result["suppress_10s_ms"] = milliseconds(suppress)
    result["suppress_peak_traced_kib"] = peak_kib(suppress)
    result.update(measure_http())
    return result


GROUPS = ("startup", "runtime", "persistence", "compute")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("label", choices=("before", "after"))
    parser.add_argument("--groups", nargs="+", choices=GROUPS, default=GROUPS)
    args = parser.parse_args()
    sys.addaudithook(local_only)
    cache = Path(__file__).resolve().parents[1] / ".cache/qa/performance"
    cache.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=args.label + "-", dir=cache))
    for key, directory in {"TEMP": "temp", "TMP": "temp", "TMPDIR": "temp", "TINGMU_CACHE_DIR": "cache",
                           "TINGMU_DATA_DIR": "data", "QML_DISK_CACHE_PATH": "qml"}.items():
        (work / directory).mkdir(exist_ok=True)
        os.environ[key] = str(work / directory)
    tempfile.tempdir = os.environ["TEMP"]
    os.environ.update(HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1", QT_QUICK_BACKEND="software",
                      QT_QPA_PLATFORM="windows" if sys.platform == "win32" else "offscreen")
    output = {"host": platform.platform(), "python": platform.python_version(), "metrics": {}}
    for name in args.groups:
        folder = work / name
        folder.mkdir()
        measure = measure_compute if name == "compute" else importlib.import_module("perf_" + name).measure
        metrics = measure(folder)
        output["metrics"][name] = metrics
        print(name, json.dumps(metrics, ensure_ascii=False), flush=True)
    report = work / "results.json"
    report.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
