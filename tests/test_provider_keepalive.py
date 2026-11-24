"""Offline HTTP/1.1 lifecycle checks at the provider request boundary."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import threading
import urllib.request
from unittest.mock import Mock

import pytest

from tingmu import providers
from tingmu.provider_transport import ConnectionPool


class LocalHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.do_GET()

    def do_GET(self):
        server = self.server
        port = self.client_address[1]
        server.calls.append((self.command, self.path, port, self.headers.get("Authorization")))
        payload = json.dumps({"port": port}).encode("ascii")
        if self.path == "/user-agent":
            payload = json.dumps(self.headers.get_all("User-Agent")).encode("ascii")
        if self.path == "/wave":
            server.wave.wait(timeout=4)
        if self.path == "/drop-after-request":
            self.close_connection = True
            return
        if self.path == "/wait-headers":
            server.waiting.set()
            server.resume.wait(5)
            self.close_connection = True
            return
        code = {"/redirect": 307, "/invalid": 400, "/wait-error-body": 400}.get(self.path, 200)
        self.send_response(code)
        if self.path == "/redirect":
            self.send_header("Location", server.location)
        self.send_header("Content-Length", str(len(payload) + (10 if self.path == "/truncated" else 0)))
        self.end_headers()
        if self.path in ("/wait-body", "/wait-error-body"):
            self.wfile.write(payload[:1])
            self.wfile.flush()
            server.waiting.set()
            server.resume.wait(5)
            self.close_connection = True
            return
        if self.path == "/truncated":
            self.close_connection = True
        self.wfile.write(payload)

    def finish(self):
        try:
            super().finish()
        finally:
            self.server.finished.set()


@contextmanager
def local_server():
    with ThreadingHTTPServer(("127.0.0.1", 0), LocalHandler) as server:
        server.calls = []
        server.finished = threading.Event()
        server.waiting = threading.Event()
        server.resume = threading.Event()
        server.config = {"base_url": f"http://127.0.0.1:{server.server_port}", "key_source": "none"}
        thread = threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.resume.set()
            server.shutdown()
            thread.join(2)


@pytest.fixture
def origin(monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {})
    monkeypatch.setattr(providers, "_pool", ConnectionPool(capacity=4, idle_seconds=2))
    with local_server() as server:
        yield server


def test_concurrent_waves_reuse_four_connections_with_bounded_capacity(origin):
    origin.wave = threading.Barrier(4)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: providers.request(origin.config, "/wave", timeout=4), range(8)))
    ports = [result["port"] for result in results]
    assert len(set(ports)) == 4
    assert all(ports.count(port) == 2 for port in ports)


def test_cross_origin_redirect_never_forwards_authorization(origin):
    with local_server() as destination:
        origin.location = destination.config["base_url"] + "/elsewhere"
        config = {**origin.config, "provider": "local_compatible", "key_source": "environment",
                  "_key": "synthetic-provider-fixture"}
        with pytest.raises(providers.ProviderError) as denied:
            providers.request(config, "/redirect", {"model": "fixture"}, timeout=2)
        assert denied.value.status == 307
        assert origin.calls[0][3] == "Bearer synthetic-provider-fixture"
        assert destination.calls == []


def test_reused_connection_sends_default_or_explicit_user_agent_per_request(origin):
    url = origin.config["base_url"] + "/user-agent"
    default = urllib.request.URLopener.version
    for headers, expected in [({}, default), ({"uSeR-aGeNt": "test-client/1"}, "test-client/1"), ({}, default)]:
        with providers._open(urllib.request.Request(url, headers=headers), 2) as response:
            assert json.loads(response.read()) == [expected]
    assert len({call[2] for call in origin.calls}) == 1


def test_error_parser_failure_releases_connection(origin, monkeypatch):
    monkeypatch.setattr(providers, "_pool", ConnectionPool(capacity=1))
    monkeypatch.setattr(providers, "_retry_after_seconds", Mock(side_effect=RuntimeError("parser failed")))
    with pytest.raises(RuntimeError, match="parser failed"):
        providers.request(origin.config, "/invalid", timeout=2)
    assert providers.request(origin.config, "/ok", timeout=2)["port"] != origin.calls[0][2]


def test_unread_response_and_error_release_without_reuse(origin):
    request = urllib.request.Request(origin.config["base_url"] + "/partial")
    with providers._open(request, 2) as response:
        assert len(response.read(1)) == 1
    with pytest.raises(providers.ProviderError) as invalid:
        providers.request(origin.config, "/invalid", timeout=2)
    assert invalid.value.status == 400
    providers.request(origin.config, "/ok", timeout=2)
    assert len({row[2] for row in origin.calls}) == 3


def test_truncated_parseable_body_is_rejected_and_connection_discarded(origin):
    with pytest.raises(providers.ProviderError):
        providers.request(origin.config, "/truncated", timeout=2)
    providers.request(origin.config, "/ok", timeout=2)
    assert origin.calls[0][2] != origin.calls[1][2]


def test_post_on_reused_connection_is_sent_once_after_disconnect(origin):
    first_port = providers.request(origin.config, "/ok", timeout=2)["port"]
    with pytest.raises(providers.ProviderError):
        providers.request(origin.config, "/drop-after-request", {"model": "fixture"}, timeout=2)
    assert [(method, path, port) for method, path, port, _ in origin.calls] == [
        ("GET", "/ok", first_port), ("POST", "/drop-after-request", first_port)]
    assert providers.request(origin.config, "/ok", timeout=2)["port"] != first_port


@pytest.mark.parametrize("path", ["/wait-headers", "/wait-body", "/wait-error-body"])
def test_cancel_interrupts_socket_and_next_lease_is_fresh(origin, path):
    first = providers.request(origin.config, "/ok", timeout=2)["port"]
    cancel = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(providers.request, origin.config, path, timeout=8, cancelled=cancel.is_set)
        try:
            assert origin.waiting.wait(2)
            cancel.set()
            with pytest.raises(providers.RequestCancelled):
                pending.result(timeout=2)
            assert origin.calls[-1][2] == first
            assert providers.request(origin.config, "/ok", timeout=2)["port"] != first
        finally:
            origin.resume.set()


def test_proxy_fallback_cancels_pending_response_headers_on_loopback(origin, monkeypatch):
    monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": "http://127.0.0.1:1"})
    cancel = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(providers.request, origin.config, "/wait-headers", timeout=8, cancelled=cancel.is_set)
        try:
            assert origin.waiting.wait(2)
            cancel.set()
            with pytest.raises(providers.RequestCancelled):
                pending.result(timeout=2)
        finally:
            origin.resume.set()


def test_idle_connection_expires_without_another_request(origin, monkeypatch):
    monkeypatch.setattr(providers, "_pool", ConnectionPool(capacity=1, idle_seconds=0.05))
    port = providers.request(origin.config, "/ok", timeout=2)["port"]
    assert origin.finished.wait(2)
    assert providers.request(origin.config, "/ok", timeout=2)["port"] != port


def test_patched_opener_receives_existing_redirect_and_loopback_handlers(origin, monkeypatch):
    build = Mock()
    build.return_value.open.return_value = io.BytesIO(b'{"compat":true}')
    monkeypatch.setattr(urllib.request, "build_opener", build)
    assert providers.request(origin.config, "/models", timeout=2) == {"compat": True}
    handlers = build.call_args.args
    assert any(isinstance(handler, providers.NoRedirect) for handler in handlers)
    assert any(isinstance(handler, urllib.request.ProxyHandler) and handler.proxies == {} for handler in handlers)
    assert origin.calls == []


def test_system_proxy_fallback_bypasses_proxy_for_loopback(origin, monkeypatch):
    with local_server() as proxy:
        monkeypatch.setattr(urllib.request, "getproxies", lambda: {"http": proxy.config["base_url"]})
        # Exercise the real urllib opener and isolate the explicit loopback
        # override from OS-specific localhost bypass behavior.
        monkeypatch.setattr(urllib.request, "proxy_bypass", lambda host: False)
        monkeypatch.setattr(providers._pool, "open", lambda *args: pytest.fail("expected urllib fallback"))
        assert providers.request(origin.config, "/ok", timeout=2)["port"] == origin.calls[0][2]
        assert proxy.calls == []


def test_non_loopback_proxy_fallback_keeps_system_proxy_and_redirect_guard(monkeypatch):
    proxies = {"https": "http://proxy.invalid:8080"}
    monkeypatch.setattr(urllib.request, "getproxies", lambda: proxies)
    monkeypatch.setattr(providers._pool, "open", lambda *args: pytest.fail("expected urllib fallback"))

    def inspect_opener(opener, request, timeout):
        assert any(isinstance(handler, urllib.request.ProxyHandler) and handler.proxies == proxies
                   for handler in opener.handlers)
        assert any(isinstance(handler, providers.NoRedirect) for handler in opener.handlers)
        assert request.full_url == "https://service.invalid/models" and timeout == 2
        return io.BytesIO(b'{"configured":true}')

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", inspect_opener)
    config = {"base_url": "https://service.invalid", "key_source": "none"}
    assert providers.request(config, "/models", timeout=2) == {"configured": True}
