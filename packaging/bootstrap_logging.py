"""Bounded, secret-redacted stdout/stderr for the windowed Python launcher."""
from __future__ import annotations

import io
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import threading


def redact_environment_secrets(text: str) -> str:
    """Fallback used even when importing the application diagnostics fails."""
    for name in ("OPENAI_API_KEY", "OpenAI", "OPENAI", "DEEPSEEK_API_KEY", "DEEPSEEK"):
        value = os.environ.get(name, "").strip()
        if value:
            text = text.replace(value, "[redacted]")
    return re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", text)


class _QuietRotatingHandler(RotatingFileHandler):
    def handleError(self, record):
        # logging's default writes back to sys.stderr, which is this stream.
        # A locked file or full disk must never recurse or block app startup.
        self.failed_records = getattr(self, "failed_records", 0) + 1


class RedactingLog(io.TextIOBase):
    """Line buffering also redacts secrets split across print() write calls."""

    def __init__(self, path: Path, max_bytes: int = 2 * 1024 * 1024):
        super().__init__()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.redactor = redact_environment_secrets
        self._pending = ""
        self._lock = threading.RLock()
        # Three files total: active application.log and two rotated backups.
        self._handler = _QuietRotatingHandler(path, maxBytes=max_bytes, backupCount=2, encoding="utf-8")
        self._handler.setFormatter(logging.Formatter("%(message)s"))
        self._handler.terminator = ""
        if path.stat().st_size >= max_bytes:
            try:
                self._handler.doRollover()
            except OSError:
                # Another running portable instance may still hold the old log.
                # Its log is preserved; failed writes remain nonfatal.
                pass

    @property
    def encoding(self):
        return "utf-8"

    def writable(self):
        return True

    def write(self, text):
        if self.closed:
            raise ValueError("write to closed log")
        if not isinstance(text, str):
            raise TypeError("log writes must be text")
        with self._lock:
            self._pending += text
            lines = self._pending.splitlines(keepends=True)
            self._pending = ""
            for line in lines:
                if line.endswith(("\n", "\r")):
                    self._emit(line)
                else:
                    self._pending = line
        return len(text)

    def _emit(self, text):
        # Always retain the fallback even if the application redactor changes.
        safe = redact_environment_secrets(self.redactor(text))
        limit = min(16 * 1024, self._handler.maxBytes // 2)
        if len(safe.encode("utf-8")) > limit:
            safe = "[oversized log line omitted]\n"
        self._handler.handle(logging.makeLogRecord({"msg": safe, "levelno": logging.INFO,
                                                    "levelname": "INFO"}))

    def flush(self):
        if self.closed:
            return
        with self._lock:
            if self._pending:
                self._emit(self._pending)
                self._pending = ""
            self._handler.flush()

    def close(self):
        if not self.closed:
            self.flush()
            super().close()
            self._handler.close()
