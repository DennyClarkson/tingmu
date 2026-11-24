"""Fixed, offline persistence workloads. Call measure() with a fresh scratch directory."""

from contextlib import closing, nullcontext
import os
from pathlib import Path
import sqlite3
from statistics import median
from time import perf_counter_ns
from types import SimpleNamespace
from unittest.mock import patch

from tingmu import batch
from tingmu.conversations import ConversationStore
from tingmu.models import AppSettings, Cue, LanguagePair, StudyDocument, Token
from tingmu.notebooks import NotebookManager
from tingmu.storage import Store
from tingmu.task_progress import Progress


def _ms(action):
    samples = []
    action()
    for _ in range(5):
        start = perf_counter_ns()
        action()
        samples.append((perf_counter_ns() - start) / 1e6)
    return median(samples)


def _material_metrics(store, document, stage):
    document.metadata["stage"] = stage
    result = {f"document_{stage}_ms": _ms(lambda: store.save_document(document))}
    written = []
    fsync = os.fsync
    replace = os.replace

    def observe_replace(source, dest, *args, **kwargs):
        size = Path(source).stat().st_size
        replace(source, dest, *args, **kwargs)
        written.append(size)

    with patch("os.replace", observe_replace), patch("os.fsync", wraps=fsync) as sync:
        store.save_document(document)
    result[f"document_{stage}_published_bytes"] = sum(written)
    result[f"document_{stage}_fsync_count"] = sync.call_count
    return result


def _queue_metrics(store, media, document):
    queue = batch.PreparationQueue(store)
    queue.enqueue([media], AppSettings())
    # Simulate one slow teaching stage deterministically, without sleeping or inference.
    clock = SimpleNamespace(time=batch.time.time, monotonic=lambda: next(ticks))
    ticks = iter(i * 0.75 for i in range(1000))

    def prepare(*args, progress, **kwargs):
        progress(Progress("teaching", stage_code="teaching"))
        for i in range(1, 51):
            progress(Progress(str(i), total=50, done=i, unit="句"))
        return document

    with (patch.object(batch, "prepare_media", prepare), patch.object(batch, "time", clock),
          patch.object(batch, "atomic_json", wraps=batch.atomic_json) as writes):
        result = queue.run()
    assert result[0]["status"] == "completed"
    return {"queue_50_progress_and_transitions_writes": writes.call_count}


def measure(folder: Path) -> dict:
    root = folder / "persistence"
    root.mkdir()
    media = root / "sample.mkv"
    media.write_bytes(b"synthetic")
    language = LanguagePair()
    cues = [Cue(str(i), i * 2., i * 2. + 1.5, f"練習第{i}句", translation=f"练习第{i}句",
                explanation="合成例句", tokens=[Token("練習", "lin6 zaap6", "练习", True)]) for i in range(600)]
    document = StudyDocument(str(media), language, cues, metadata={"stage": "ready"})
    with closing(Store(root / "data")) as store:
        manager = NotebookManager(store)
        with store._connection() as db:
            for cue in cues[:150]:
                store._save_entry(db, "word", str(media), cue, language, cue.text)
        manager.sync(language.target)
        # Compare 50 real source resolutions, both with and without one bounded session.
        def lookup(batched):
            with store.database.session() if batched else nullcontext():
                for i in range(1, 51):
                    assert manager.source_entry(i)["id"] == i

        history = ConversationStore(store)
        sid = history.create(language, str(media))["id"]
        messages = [{"id": str(i), "role": "assistant" if i % 2 else "user", "content": f"合成消息 {i}",
                     "created_at": "2026-01-01T00:00:00Z"} for i in range(50)]
        results = {
            "sqlite_50_reads_unbatched_ms": _ms(lambda: lookup(False)),
            "sqlite_50_reads_session_ms": _ms(lambda: lookup(True)),
            "notebook_150_snapshot_ms": _ms(lambda: manager.snapshot(language.target)),
            "notebook_150_sync_ms": _ms(lambda: manager.sync(language.target)),
            "chat_50_save_with_legacy_ms": _ms(lambda: history.save_with_legacy(sid, messages)),
        }
        with patch("sqlite3.connect", wraps=sqlite3.connect) as connections:
            manager.snapshot(language.target)
        results["notebook_snapshot_connections"] = connections.call_count
        # Start from an already published file, as when resuming/re-teaching a video.
        store.save_document(document)
        results.update(_material_metrics(store, document, "teaching"))
        results.update(_material_metrics(store, document, "ready"))
        results.update(_queue_metrics(store, media, document))
    return results
