"""Worker-compatible summaries of library materials, cached by file identity."""

from dataclasses import asdict
from pathlib import Path
import threading

from tingmu.lesson_material import lesson_counts
from tingmu.storage import canonical_path, media_fingerprint


class LibrarySummaryCache:
    """Only cold or changed documents perform parsing and verification hashing.

    `get` may read/recover material and belongs on a worker for cold libraries.
    `peek` returns the most recently checked value without filesystem access.
    Callers publishing asynchronous results must still check their request's
    workspace, language, and catalog generation before updating the UI.
    """

    def __init__(self, store):
        self.store = store
        self._lock = threading.RLock()
        self._loads = threading.RLock()
        self._generation = 0
        self._entries = {}
        self._paths = {}

    @staticmethod
    def _key(media, language):
        # Catalog paths are canonical. Avoid resolve()/stat in the UI peek path.
        return str(media), tuple(sorted(asdict(language).items()))

    @staticmethod
    def _stat(path):
        try:
            stat = path.stat()
            return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino
        except OSError:
            return None

    def peek(self, media, language):
        with self._lock:
            cached = self._entries.get(self._key(media, language))
            return dict(cached[1]) if cached else None

    def invalidate(self, media=None):
        canonical = canonical_path(media) if media is not None else None
        with self._lock:
            self._generation += 1
            if media is None:
                self._entries.clear()
                self._paths.clear()
            else:
                for key in list(self._entries):
                    if key[0] == canonical:
                        self._entries.pop(key)
                for key in list(self._paths):
                    if key[0] == canonical:
                        self._paths.pop(key)

    def get(self, media, language):
        media = canonical_path(media)
        key = self._key(media, language)
        # A slow disk read never holds the lock used by the UI's peek().
        with self._loads:
            with self._lock:
                generation = self._generation
                path = self._paths.get(key)
            if path is None:
                path = self.store._document_path(media, language)
                with self._lock:
                    self._paths[key] = path
            pending = path.with_suffix(".pending")
            fingerprint = media_fingerprint(media)
            signature = self._stat(path), tuple(sorted(fingerprint.items())), Path(media).is_file()
            with self._lock:
                cached = self._entries.get(key)
            if cached and cached[0] == signature and not pending.exists():
                return dict(cached[1])
            document = self.store.load_document(media, language)
            stage = document.metadata.get("stage", "transcribed") if document else "unprepared"
            status = "ready" if stage == "ready" else "partial" if document else "unprepared"
            if document and status == "ready":
                counts = lesson_counts(document)
                if counts["verified"] + counts.get("provided", 0) < counts["total"]:
                    status = "lessonDraft" if counts["taught"] else "subtitleOnly"
            if not signature[2]:
                status = "missing"
            result = {"status": status, "cueCount": len(document.cues) if document else 0}
            # Never bless a mixed read if a worker replaced media/document during
            # parsing. A surviving journal also forces recovery on the next read.
            after = self._stat(path), tuple(sorted(media_fingerprint(media).items())), Path(media).is_file()
            stable = signature == after and not pending.exists()
            with self._lock:
                if generation == self._generation:
                    if stable:
                        self._entries[key] = (after, result)
                    else:
                        self._entries.pop(key, None)
            return dict(result)
