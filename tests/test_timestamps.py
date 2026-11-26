from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

from tingmu import timestamps


def test_utc_timestamp_is_fixed_width_across_stalls_rollbacks_and_midnight(monkeypatch):
    midnight = datetime(2026, 1, 1, tzinfo=UTC)
    tick = timedelta(microseconds=1)
    before = midnight - tick
    readings = iter([before, before, before - timedelta(days=1), midnight + timedelta(seconds=2)])
    monkeypatch.setattr(timestamps, "_last", None)
    monkeypatch.setattr(timestamps, "_utc_now", lambda: next(readings))

    values = [timestamps.utc_timestamp() for _ in range(4)]
    assert values == [
        "2025-12-31T23:59:59.999999+00:00",
        "2026-01-01T00:00:00.000000+00:00",
        "2026-01-01T00:00:00.000001+00:00",
        "2026-01-01T00:00:02.000000+00:00",
    ]
    assert sorted(values) == values


def test_threads_share_one_sequence_during_a_frozen_clock_tick(monkeypatch):
    frozen = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(timestamps, "_last", None)
    monkeypatch.setattr(timestamps, "_utc_now", lambda: frozen)
    workers, per_worker = 8, 32
    start = Barrier(workers)

    def collect():
        start.wait(timeout=10)
        return [timestamps.utc_timestamp() for _ in range(per_worker)]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(collect) for _ in range(workers)]
        series = [future.result() for future in futures]
    assert all(values == sorted(values) for values in series)
    assert sorted(value for values in series for value in values) == [
        (frozen + timedelta(microseconds=i)).isoformat(timespec="microseconds")
        for i in range(workers * per_worker)
    ]
