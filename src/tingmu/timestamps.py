"""UTC timestamps used to order persisted changes made by this process."""

from datetime import UTC, datetime, timedelta
from threading import Lock


_lock = Lock()
_last: datetime | None = None
_one_microsecond = timedelta(microseconds=1)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def utc_timestamp() -> str:
    """Issue a strictly increasing, fixed-width UTC timestamp, even if the clock stalls or goes back."""
    global _last
    with _lock:
        now = _utc_now()
        if _last is not None and now <= _last:
            now = _last + _one_microsecond
        _last = now
    return now.isoformat(timespec="microseconds")
