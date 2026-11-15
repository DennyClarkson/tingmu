"""A persistent index of external videos; no sidecars or source-media copies."""

import json
import os
from pathlib import Path

from .media import MEDIA_EXTENSIONS, scan_media
from .storage import Store, atomic_json, canonical_path


class MediaLibrary:
    def __init__(self, store: Store):
        self.path = store.data_dir / "media-library.json"
        try:
            saved = json.loads(self.path.read_text("utf-8"))
            self.paths = [canonical_path(row["media_path"]) for row in saved["items"]]
        except (OSError, ValueError, TypeError, KeyError):
            self.paths = []
            # Discover already prepared documents without moving or rewriting them.
            for path in (store.data_dir / "documents").glob("*.json"):
                try:
                    media = json.loads(path.read_text("utf-8"))["media_path"]
                    if Path(media).is_file():
                        self.paths.append(canonical_path(media))
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        self.paths = list(dict.fromkeys(self.paths))

    def add(self, paths) -> list[str]:
        incoming = []
        for raw in paths:
            path = Path(raw).expanduser().resolve()
            candidates = scan_media(path) if path.is_dir() else [path]
            for item in candidates:
                if item.is_file() and item.suffix.lower() in MEDIA_EXTENSIONS:
                    incoming.append(canonical_path(item))
        existing = {os.path.normcase(p) for p in self.paths}
        added = []
        for path in incoming:
            if os.path.normcase(path) not in existing:
                existing.add(os.path.normcase(path))
                self.paths.append(path)
                added.append(path)
        self.save()
        return added

    def remove(self, paths):
        selected = {os.path.normcase(canonical_path(p)) for p in paths}
        self.paths = [p for p in self.paths if os.path.normcase(p) not in selected]
        self.save()

    def save(self):
        atomic_json(self.path, {"version": 1, "items": [{"media_path": p} for p in self.paths]})
