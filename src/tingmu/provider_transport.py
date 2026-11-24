"""Bounded HTTP/1.1 leases for direct provider connections (no proxy or redirects)."""

from contextlib import AbstractContextManager
from functools import lru_cache
import http.client
import socket
import ssl
import threading
from time import monotonic
import urllib.request
from urllib.parse import urlsplit


_tls_lock = threading.Lock()


@lru_cache(maxsize=1)
def _tls_context():
    context = ssl.create_default_context()
    context.set_alpn_protocols(["http/1.1"])
    return context


class CancelWatch:
    """Only the current request can shut down its socket, even across pool reuse."""
    def __init__(self, cancelled):
        self.cancelled = cancelled
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._socket = None
        self._aborted = False
        threading.Thread(target=self._watch, daemon=True, name="provider-cancel").start()

    def _watch(self):
        while not self._stop.wait(0.05):
            if self.cancelled():
                with self._lock:
                    self._aborted = True
                    if self._socket is not None:
                        sock = self._socket
                        self._socket = None
                        try:
                            sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                        # A timeout-mode Winsock read can remain in select after
                        # shutdown. HTTPResponse.makefile() holds an extra ref, so
                        # socket.close() alone would defer the OS close as well.
                        descriptor = sock.detach()
                        if descriptor != -1:
                            try:
                                socket.close(descriptor)
                            except OSError:
                                pass  # The connection may already be torn down.
                return

    def bind(self, sock):
        with self._lock:
            if self._aborted or self.cancelled():
                raise InterruptedError("Request cancelled")
            self._socket = sock

    def detach(self):
        with self._lock:
            self._socket = None
            return not self._aborted and not self.cancelled()

    def stop(self):
        self._stop.set()


class WatchedOpenerHandler(urllib.request.HTTPHandler, urllib.request.HTTPSHandler):
    """Keep urllib's proxy/TLS handling while exposing the socket before response headers."""
    def __init__(self, watch):
        super().__init__()
        self._watch = watch

    def do_open(self, http_class, req, **options):
        watch = self._watch

        class WatchedConnection(http_class):
            def connect(self):
                super().connect()
                watch.bind(self.sock)

        return super().do_open(WatchedConnection, req, **options)


class _Response(AbstractContextManager):
    def __init__(self, pool, key, connection, response, watch):
        self.pool = pool
        self.key = key
        self.connection = connection
        self.response = response
        self.watch = watch

    def read(self, amount=None):
        data = self.response.read(amount)
        # HTTPResponse.read(n) can silently return valid-looking partial JSON
        # on a length-delimited response cut short by a disconnected peer.
        if amount is not None and len(data) < amount and self.response.length:
            raise http.client.IncompleteRead(data, self.response.length)
        return data

    def readline(self, *args):
        data = self.response.readline(*args)
        if not data and self.response.length:
            raise http.client.IncompleteRead(b"", self.response.length)
        return data

    def close(self):
        self.__exit__(None, None, None)

    def __exit__(self, typ, value, tb):
        if self.connection is None:
            return
        conn, self.connection = self.connection, None
        response = self.response
        reusable = (typ is None and response.isclosed() and not response.will_close and
                    response.length in (0, None) and response.status < 300 and conn.sock is not None)
        try:
            if self.watch is not None and not self.watch.detach():
                reusable = False
            response.close()
        finally:
            self.pool.release(self.key, conn, reusable)


class ConnectionPool:
    def __init__(self, capacity=16, idle_seconds=30):
        self.capacity = capacity
        self.idle_seconds = idle_seconds
        self._available = threading.Condition()
        self._active = 0
        self._idle = []
        self._timer = None

    def _expire(self):
        now = monotonic()
        while self._idle and self._idle[0][0] <= now:
            self._idle.pop(0)[2].close()

    def _schedule(self):
        if self._idle and self._timer is None:
            self._timer = threading.Timer(max(0, self._idle[0][0] - monotonic()), self._reap)
            self._timer.daemon = True
            self._timer.start()

    def _reap(self):
        with self._available:
            self._timer = None
            self._expire()
            self._schedule()

    def _acquire(self, key, timeout, watch):
        deadline = monotonic() + timeout
        with self._available:
            while self._active >= self.capacity:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Provider connection pool is busy")
                if watch is not None and watch.cancelled():
                    raise InterruptedError("Request cancelled")
                self._available.wait(min(remaining, 0.1) if watch else remaining)
            self._expire()
            self._active += 1
            for index in reversed(range(len(self._idle))):
                if self._idle[index][1] == key:
                    return self._idle.pop(index)[2]
            if self._active + len(self._idle) > self.capacity:
                self._idle.pop(0)[2].close()
            return None

    def release(self, key, connection, reusable):
        with self._available:
            self._active -= 1
            if reusable:
                self._idle.append((monotonic() + self.idle_seconds, key, connection))
                self._schedule()
            elif connection is not None:
                connection.close()
            self._available.notify()

    def open(self, request, timeout, watch=None):
        url = urlsplit(request.full_url)
        key = (url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80))
        method = request.get_method()
        headers = dict(request.header_items())
        if not any(name.lower() == "user-agent" for name in headers):
            headers["User-Agent"] = urllib.request.URLopener.version
        # Retry a stale, previously used socket once for idempotent reads only.
        for attempt in range(2):
            conn = self._acquire(key, timeout, watch)
            reused = conn is not None
            try:
                if conn is None:
                    if url.scheme == "https":
                        with _tls_lock:
                            context = _tls_context()
                        conn = http.client.HTTPSConnection(key[1], key[2], timeout=timeout, context=context)
                    else:
                        conn = http.client.HTTPConnection(key[1], key[2], timeout=timeout)
                    conn.connect()
                conn.timeout = timeout
                conn.sock.settimeout(timeout)
                if watch is not None:
                    watch.bind(conn.sock)
                conn.request(method, request.selector, request.data, headers)
                response = conn.getresponse()
                return _Response(self, key, conn, response, watch)
            except BaseException as exc:
                active = watch is None or watch.detach()
                self.release(key, conn, False)
                stale = isinstance(exc, (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError))
                if not (active and reused and attempt == 0 and method in ("GET", "HEAD") and stale):
                    raise
