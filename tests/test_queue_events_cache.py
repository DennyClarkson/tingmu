"""Queue-driven refreshes and bounded-cost library summaries."""

from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import threading

import pytest

import tingmu.batch as batch
from tingmu.models import AppSettings, Cue, LanguagePair, StudyDocument
from tingmu.quick.library_summary import LibrarySummaryCache
from tingmu.storage import Store, atomic_json, canonical_path


def test_queue_notifies_changes_unsubscribes_and_isolates_observer_failure(tmp_path):
    queue = batch.PreparationQueue(Store(tmp_path / "data"))
    movie = tmp_path / "one.mkv"
    movie.touch()
    events = []

    def failing_observer():
        raise RuntimeError("observer failure")

    def callback():
        events.append(queue.snapshot())

    queue.subscribe(failing_observer)
    release = queue.subscribe(callback)
    queue.subscribe(callback)  # Registering the same callback does not double-deliver.
    job = queue.enqueue([movie], AppSettings())[0]
    assert len(events) == 1 and events[-1][0]["id"] == job["id"]
    queue.enqueue([movie], AppSettings())
    queue.remove("missing")
    queue.retry("missing")
    queue.pause_automatic()
    queue.resume_automatic()
    queue.clear_completed()
    assert len(events) == 1
    queue.remove(job["id"])
    assert events[-1] == [] and len(events) == 2
    release()
    release()
    queue.enqueue([movie], AppSettings())
    assert len(events) == 2
    assert len(batch.PreparationQueue(queue.store).snapshot()) == 1


def test_queue_observer_runs_outside_lock_and_reentrant_mutation_is_persisted(tmp_path):
    queue = batch.PreparationQueue(Store(tmp_path / "data"))
    movie = tmp_path / "one.mkv"
    movie.touch()
    events, readers = [], []

    def observer():
        reader_done = threading.Event()
        reader = threading.Thread(target=lambda: (queue.snapshot(), reader_done.set()))
        readers.append(reader)
        reader.start()
        assert reader_done.wait(1), "observer still holds the queue lock"
        snapshot = queue.snapshot()
        events.append(len(snapshot))
        if snapshot:
            queue.remove(snapshot[0]["id"])

    queue.subscribe(observer)
    queue.enqueue([movie], AppSettings())
    for reader in readers:
        reader.join(timeout=1)
        assert not reader.is_alive()
    assert events == [1, 0]
    assert batch.PreparationQueue(queue.store).snapshot() == []


def test_queue_progress_is_throttled_but_stage_and_terminal_changes_are_immediate(tmp_path, monkeypatch):
    queue = batch.PreparationQueue(Store(tmp_path / "data"))
    movie = tmp_path / "one.mkv"
    movie.touch()
    queue.enqueue([movie], AppSettings())
    events = []
    queue.subscribe(lambda: events.append(queue.snapshot()[0].copy()))

    class Progress(str):
        def __new__(cls, text, details):
            message = super().__new__(cls, text)
            message.details = details
            return message

    clock = [10.0]

    def prepare(path, settings, store, *, progress, **kwargs):
        with monkeypatch.context() as scoped:
            scoped.setattr(batch.time, "monotonic", lambda: clock[0])
            progress(Progress("start", {"stage": "讲解"}))
            baseline = len(events)
            for done in range(1, 100):
                clock[0] += 0.001
                progress(Progress(str(done), {"done": done, "total": 100, "unit": "句"}))
            assert len(events) == baseline
            clock[0] += 0.5
            progress(Progress("100", {"done": 100, "total": 100, "unit": "句"}))
            assert len(events) == baseline + 1
            progress(Progress("verify", {"stage": "核验"}))
            assert len(events) == baseline + 2
        return StudyDocument(str(path), settings.language, [Cue("a", 0, 1, "完成")])

    monkeypatch.setattr(batch, "prepare_media", prepare)
    queue.run()
    assert events[0]["status"] == "running"
    assert events[-1]["status"] == "completed"
    assert events[-1]["completed_cues"] == 1
    assert len(events) == 5


def test_queue_pause_retry_and_automatic_flags_notify(tmp_path, monkeypatch):
    queue = batch.PreparationQueue(Store(tmp_path / "data"))
    movie = tmp_path / "one.mkv"
    movie.touch()
    job = queue.enqueue([movie], AppSettings())[0]
    events = []
    queue.subscribe(lambda: events.append(queue.snapshot()))

    def prepare(*args, **kwargs):
        queue.pause()
        raise InterruptedError()

    monkeypatch.setattr(batch, "prepare_media", prepare)
    queue.run()
    assert [event[0]["status"] for event in events] == ["running", "paused"]
    queue.retry(job["id"])
    assert events[-1][0]["status"] == "queued"
    queue.remove(job["id"])
    queue.enqueue([movie], AppSettings(), automatic=True)
    queue.pause_automatic()
    assert events[-1][0]["auto_paused"]
    count = len(events)
    queue.pause_automatic()
    assert len(events) == count
    queue.resume_automatic()
    assert "auto_paused" not in events[-1][0]


def _material(tmp_path):
    store = Store(tmp_path / "data")
    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"synthetic")
    document = StudyDocument(str(movie), LanguagePair(), [
        Cue("a", 0, 1, "one", translation="译文", explanation="讲解")], metadata={"stage": "ready"})
    path = store.save_document(document)
    return store, movie, document, path


def test_summary_cache_unchanged_rows_do_not_load_or_hash_documents(tmp_path, monkeypatch):
    import tingmu.quick.library_summary as summaries

    store, movie, document, _ = _material(tmp_path)
    cache = LibrarySummaryCache(store)
    assert cache.peek(str(movie), document.language) is None
    assert cache.get(movie, document.language) == {"status": "lessonDraft", "cueCount": 1}
    with monkeypatch.context() as scoped:
        scoped.setattr(store, "load_document", lambda *_: pytest.fail("unchanged document read"))
        scoped.setattr(store, "_document_path", lambda *_: pytest.fail("unchanged document key hash"))
        scoped.setattr(summaries, "lesson_counts", lambda *_: pytest.fail("unchanged verification hashes"))
        for _ in range(8):
            assert cache.get(movie, document.language) == {"status": "lessonDraft", "cueCount": 1}
        scoped.setattr(Path, "stat", lambda *_args, **_kwargs: pytest.fail("peek performed filesystem IO"))
        value = cache.peek(str(movie), document.language)
        value["cueCount"] = 999
        assert cache.peek(str(movie), document.language)["cueCount"] == 1


def test_summary_cache_detects_document_media_missing_and_language_changes(tmp_path):
    store, movie, document, path = _material(tmp_path)
    cache = LibrarySummaryCache(store)
    assert cache.get(movie, document.language)["cueCount"] == 1
    document.cues.append(Cue("b", 1, 2, "two"))
    atomic_json(path, document.to_dict())
    assert cache.get(movie, document.language) == {"status": "lessonDraft", "cueCount": 2}
    other = replace(document.language, target="en")
    assert cache.get(movie, other) == {"status": "unprepared", "cueCount": 0}
    movie.write_bytes(b"replaced media bytes")
    assert cache.get(movie, document.language) == {"status": "unprepared", "cueCount": 0}
    movie.unlink()
    assert cache.get(movie, document.language) == {"status": "missing", "cueCount": 0}
    path.unlink()
    movie.write_bytes(b"synthetic")
    assert cache.get(movie, document.language) == {"status": "unprepared", "cueCount": 0}


def test_summary_cache_pending_recovery_and_explicit_invalidation(tmp_path, monkeypatch):
    store, movie, document, path = _material(tmp_path)
    cache = LibrarySummaryCache(store)
    expected = cache.get(movie, document.language)
    original = store.load_document
    reads = []

    def load(*args):
        reads.append(args)
        return original(*args)

    monkeypatch.setattr(store, "load_document", load)
    pending = path.with_suffix(".pending")
    pending.touch()
    path.with_suffix(".srt").write_text("interrupted", encoding="utf-8")
    assert cache.get(movie, document.language) == expected
    assert len(reads) == 1 and not pending.exists()
    assert "interrupted" not in path.with_suffix(".srt").read_text("utf-8")
    assert cache.get(movie, document.language) == expected
    assert len(reads) == 1
    cache.invalidate(movie)
    assert cache.peek(str(movie), document.language) is None
    assert cache.get(movie, document.language) == expected
    assert len(reads) == 2
    cache.invalidate()
    assert cache.peek(str(movie), document.language) is None


def test_summary_peek_does_not_wait_for_cold_worker_and_invalidation_wins(tmp_path, monkeypatch):
    store, movie, document, _ = _material(tmp_path)
    cache = LibrarySummaryCache(store)
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original = store.load_document

    def load(*args):
        started.set()
        assert release.wait(3)
        return original(*args)

    monkeypatch.setattr(store, "load_document", load)
    worker = threading.Thread(target=lambda: (cache.get(movie, document.language), finished.set()))
    worker.start()
    try:
        assert started.wait(1)
        # A second reader can inspect UI state while the disk worker is blocked.
        inspected = threading.Event()
        reader = threading.Thread(target=lambda: (cache.peek(str(movie), document.language), inspected.set()))
        reader.start()
        assert inspected.wait(1)
        reader.join(timeout=1)
        cache.invalidate(movie)
    finally:
        release.set()
        worker.join(timeout=3)
    assert finished.is_set()
    assert cache.peek(str(movie), document.language) is None


def test_bulk_positions_one_select_canonicalizes_and_supplies_missing_defaults(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    first = tmp_path / "first.mkv"
    store.save_position(str(first), 1234)
    paths = [first, tmp_path / "folder" / ".." / "first.mkv"]
    paths += [tmp_path / f"missing-{i}.mkv" for i in range(1200)]
    queries = []
    connection = store._connection

    @contextmanager
    def traced():
        with connection() as db:
            db.set_trace_callback(queries.append)
            yield db

    monkeypatch.setattr(store, "_connection", traced)
    positions = store.load_positions(paths)
    assert len(positions) == 1201
    assert positions[canonical_path(first)] == 1234
    assert positions[canonical_path(paths[-1])] == 0
    assert sum(query.lstrip().upper().startswith("SELECT") for query in queries) == 1
    queries.clear()
    assert store.load_positions([]) == {} and not queries
