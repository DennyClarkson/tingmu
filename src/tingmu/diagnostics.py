"""Bounded, local structured diagnostics. Never log learning or request content."""

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import traceback
import uuid
import threading

_SESSION_SECRETS = set()
_SECRET_LOCK = threading.RLock()


def register_secret(value):
    """Keep manually entered/custom-env credentials out of runtime diagnostics."""
    if isinstance(value, str) and value:
        with _SECRET_LOCK:
            _SESSION_SECRETS.add(value)


@lru_cache(maxsize=1)
def _known_secrets():
    values = []
    names = ("OPENAI_API_KEY", "OPENAI", "DEEPSEEK_API_KEY", "DEEPSEEK", "AZURE_OPENAI_API_KEY")
    for name, value in os.environ.items():
        if name.upper() in names or any(part in name.upper() for part in ("API_KEY", "ACCESS_TOKEN", "PASSWORD")):
            if len(value) >= 8:
                values.append(value)
    if os.name == "nt":
        import winreg
        for hive, location in ((winreg.HKEY_CURRENT_USER, "Environment"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment")):
            try:
                with winreg.OpenKey(hive, location) as key:
                    for name in names:
                        try:
                            value = winreg.QueryValueEx(key, name)[0]
                            if isinstance(value, str) and len(value) >= 8:
                                values.append(value)
                        except OSError:
                            pass
            except OSError:
                pass
    return tuple(set(values))


def redact_text(value: str) -> str:
    result = str(value)
    with _SECRET_LOCK:
        secrets = _known_secrets() + tuple(_SESSION_SECRETS)
    for secret in sorted(secrets, key=len, reverse=True):
        result = result.replace(secret, "[REDACTED]")
    result = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[REDACTED]", result)
    result = re.sub(r"(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+", r"\1[REDACTED]", result)
    result = re.sub(r"(?i)((?:api[_-]?key|access[_-]?token|password)\s*[=:]\s*)[^\s,;]+", r"\1[REDACTED]", result)
    return result


def redact_stream_text(value: str, *, final=False) -> str:
    """Redact an accumulated stream without exposing a split credential suffix.

    Callers retain the raw accumulator privately and publish this safe snapshot.
    A suffix which could still become a registered credential is withheld until
    the next fragment; a long incomplete credential is redacted at stream end.
    """
    result = redact_text(value)
    with _SECRET_LOCK:
        secrets = _known_secrets() + tuple(_SESSION_SECRETS)
    held = 0
    for secret in secrets:
        if not secret:
            continue
        start = max(0, len(result) - len(secret) + 1)
        position = result.find(secret[0], start)
        while position >= 0:
            suffix = result[position:]
            if len(suffix) < len(secret) and secret.startswith(suffix):
                held = max(held, len(suffix))
            position = result.find(secret[0], position + 1)
    if held:
        if not final:
            return result[:-held]
        if held >= 8:
            return result[:-held] + "[REDACTED]"
    return result


CONTENT_KEYS = {"text", "content", "prompt", "messages", "transcript", "original_text", "note", "notes",
                "teaching_preferences", "learner_teaching_preferences", "request_context",
                "reasoning_text", "reasoning_content", "reasoning_summary", "thinking", "summary",
                "signature", "encrypted_content", "redacted_thinking", "_native_content", "_native_response",
                "_responses_output",
                "request", "response", "body", "authorization", "api_key", "apikey", "password", "secret", "token",
                "_protected_key", "key"}


def safe_fields(value):
    if isinstance(value, dict):
        return {str(key): ("[OMITTED]" if str(key).lower() in CONTENT_KEYS else safe_fields(item))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_fields(item) for item in value[:100]]
    if isinstance(value, str):
        return redact_text(value)[:2000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return type(value).__name__


def media_id(path):
    return hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12] if path else ""


class AppLog:
    def __init__(self, data_dir: Path, developer=False, max_bytes=2 * 1024 * 1024):
        self.directory = Path(data_dir) / "logs"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "events.jsonl"
        self.session = uuid.uuid4().hex[:12]
        self._closed = False
        self._read_cache = (None, ())
        self.logger = logging.getLogger(f"tingmu.session.{self.session}")
        self.logger.propagate = False
        self.handler = RotatingFileHandler(self.path, maxBytes=max_bytes, backupCount=3, encoding="utf-8")
        self.handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(self.handler)
        self.set_developer(developer)

    def set_developer(self, enabled):
        self.logger.setLevel(logging.DEBUG if enabled else logging.INFO)

    def event(self, name, message, level="INFO", **details):
        if self._closed:
            return
        priority = getattr(logging, level)
        if not self.logger.isEnabledFor(priority):
            return
        record = {"time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                  "level": level, "session": self.session, "event": name,
                  "message": redact_text(message), "fields": safe_fields(details)}
        self.logger.log(priority, json.dumps(record, ensure_ascii=False, allow_nan=False))

    def exception(self, error, event="unhandled_exception"):
        frames = [{"file": Path(frame.filename).name, "line": frame.lineno, "function": frame.name}
                  for frame in traceback.extract_tb(error.__traceback__)[-12:]]
        # Exception messages can contain provider payloads; store type/locations only.
        self.event(event, "运行发生异常", "ERROR", error_type=type(error).__name__, stack=frames)

    def read(self, limit=200):
        if not self._closed:
            self.handler.flush()
        try:
            stat = self.path.stat()
            signature = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
            cached_signature, cached = self._read_cache
            if cached_signature == signature:
                return [row.copy() for row in cached[-limit:]]
            with self.path.open("rb") as stream:
                stat = os.fstat(stream.fileno())
                signature = (stat.st_size, stat.st_mtime_ns, stat.st_ino)
                stream.seek(max(0, stat.st_size - 256000))
                lines = stream.read().decode("utf-8", errors="replace").splitlines()
            result = []
            for line in lines:
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        continue
                    row["details"] = json.dumps(row.pop("fields", {}), ensure_ascii=False)
                    result.append(row)
                except (ValueError, TypeError):
                    continue
            # Standard rows are flat after encoding details. Foreign structured
            # rows can still be read without sharing nested state with callers.
            if all(not isinstance(v, (dict, list)) for row in result for v in row.values()):
                self._read_cache = (signature, result)
                return [row.copy() for row in result[-limit:]]
            return result[-limit:]
        except OSError:
            return []

    def close(self):
        if not self._closed:
            self._closed = True
            self.handler.flush()
            self.handler.close()
            self.logger.removeHandler(self.handler)
