"""A delivered Qt completion does not imply its emitting Python thread exited."""

import threading

import pytest
from PySide6.QtCore import Qt

from tingmu.quick.bridge import Bridge
from tingmu.storage import Store


@pytest.mark.parametrize("key", ["chat", "models"])
def test_shutdown_joins_thread_still_returning_from_completion_emit(qapp, qtbot, tmp_path, monkeypatch, key):
    bridge = Bridge(Store(tmp_path / "data"))
    emitting, release, joining = threading.Event(), threading.Event(), threading.Event()
    callbacks, failures = [], []

    def hold_emit(name, value, error):
        if name == key:
            emitting.set()
            if not release.wait(5):
                failures.append("completion emission gate timed out")

    # Bridge's queued receiver is connected first. This direct observer holds the
    # sender inside emit while the UI can finish handling the queued completion.
    bridge._completed.connect(hold_emit, Qt.ConnectionType.DirectConnection)
    bridge._start_job(key, lambda *args: "result", lambda result: callbacks.append(result))
    worker = bridge._workers[key][0]
    join = worker.join

    def observe_join(timeout=None):
        joining.set()
        join(timeout)

    monkeypatch.setattr(worker, "join", observe_join)

    def release_on_join():
        if joining.wait(5):
            release.set()

    releaser = threading.Thread(target=release_on_join)
    try:
        assert emitting.wait(5)
        qtbot.waitUntil(lambda: callbacks == ["result"])
        assert key not in bridge._workers
        assert worker.is_alive()
        releaser.start()
        bridge.shutdown()
        assert joining.is_set(), "shutdown lost the live completion sender after removing its business task"
        assert not worker.is_alive()
        assert not failures
    finally:
        release.set()
        # Also unblock the helper if shutdown failed before reaching a join.
        joining.set()
        join(5)
        if releaser.ident is not None:
            releaser.join(5)
        bridge.shutdown()
