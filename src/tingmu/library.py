"""Index external media and explicitly imported workspace copies."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from .media_files import MEDIA_EXTENSIONS, SUBTITLE_EXTENSIONS
from .storage import Store, atomic_json, canonical_path, media_fingerprint


class MediaLibrary:
    def __init__(self, store: Store):
        self.path = store.data_dir / "media-library.json"
        self.media_dir = store.data_dir / "media"
        self.automatic = set()
        self.ignored = {}
        try:
            saved = json.loads(self.path.read_text("utf-8"))
            self.paths = [canonical_path(row["media_path"]) for row in saved["items"]]
            self.automatic = {canonical_path(row["media_path"]) for row in saved["items"]
                              if row.get("origin") == "media-inbox"}
            self.ignored = saved.get("ignored", {})
            if not isinstance(self.ignored, dict):
                self.ignored = {}
            self.ignored = {canonical_path(self.media_dir / p): fp for p, fp in self.ignored.items()
                            if isinstance(p, str) and isinstance(fp, dict)}
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

    def reconcile_inbox(self, ready, present):
        """Apply a background scan without recursively touching the filesystem here."""
        old = (list(self.paths), set(self.automatic), dict(self.ignored))
        removed = self.automatic - set(present)
        self.paths = [p for p in self.paths if p not in removed]
        self.automatic.difference_update(removed)
        self.ignored = {p: fp for p, fp in self.ignored.items() if p in present}
        existing = {os.path.normcase(p) for p in self.paths}
        added = []
        for path, fingerprint in ready.items():
            if self.ignored.get(path) == fingerprint:
                continue
            if os.path.normcase(path) not in existing:
                existing.add(os.path.normcase(path))
                self.paths.append(path)
                added.append(path)
            # A workspace copy may already be indexed by the import dialog.
            self.automatic.add(path)
        if old != (self.paths, self.automatic, self.ignored):
            self.save()
        return added, removed

    def copy_files(self, paths, progress, cancelled, *, agent_workflow=False):
        """Publish complete copies atomically. Keep original names and matching subtitle files."""
        if self.media_dir.resolve() != self.media_dir:
            raise ValueError("媒体目录必须位于当前工作区")
        self.media_dir.mkdir(exist_ok=True)
        result = []
        for index, raw in enumerate(paths, 1):
            path = Path(raw).resolve(strict=True)
            if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
                raise ValueError("请选择支持的音视频文件")
            if path.is_relative_to(self.media_dir):
                result.append(path)
                continue
            if agent_workflow:
                from .subtitle_sources import discover_sidecars
                companions = discover_sidecars(path)
            else:
                companions = sorted(p for p in path.parent.iterdir() if p.is_file()
                                    and p.suffix.lower() in SUBTITLE_EXTENSIONS
                                    and (p.stem == path.stem or p.stem.startswith(path.stem + ".")))
            files = [path, *companions]
            fingerprints = [(str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files]
            identity = hashlib.sha256(json.dumps(fingerprints).encode()).hexdigest()[:16]
            target = self.media_dir / identity
            if target.resolve() != target:
                raise ValueError("媒体导入目录必须位于当前工作区")
            if not target.is_dir():
                with tempfile.TemporaryDirectory(dir=self.path.parent, prefix=".media-import-") as tmp:
                    staging = Path(tmp) / identity
                    staging.mkdir()
                    for source, (_, size, timestamp) in zip(files, fingerprints):
                        with source.open("rb") as src, (staging / source.name).open("xb") as dst:
                            previous = -1
                            while chunk := src.read(4 * 1024 * 1024):
                                if cancelled():
                                    raise InterruptedError("媒体导入已取消")
                                dst.write(chunk)
                                percent = int(100 * dst.tell() / max(size, 1))
                                if percent != previous:
                                    progress(f"正在复制 {index}/{len(paths)} · {source.name} · {percent}%")
                                    previous = percent
                        if source.stat().st_mtime_ns != timestamp or (staging / source.name).stat().st_size != size:
                            raise ValueError("复制期间源文件发生变化，请重新导入")
                        shutil.copystat(source, staging / source.name)
                    try:
                        staging.rename(target)
                    except OSError:
                        if not target.is_dir():
                            raise
            for source, (_, size, _) in zip(files, fingerprints):
                if not (target / source.name).is_file() or (target / source.name).stat().st_size != size:
                    raise ValueError("已导入副本有改动，请检查工作区媒体文件夹")
            result.append(target / path.name)
        return result

    def add(self, paths) -> list[str]:
        from .media import scan_media
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
            self.ignored.pop(path, None)
            if os.path.normcase(path) not in existing:
                existing.add(os.path.normcase(path))
                self.paths.append(path)
                added.append(path)
        if added:
            self.save()
        return added

    def remove(self, paths):
        selected = {os.path.normcase(canonical_path(p)) for p in paths}
        for path in self.paths:
            if os.path.normcase(path) in selected and Path(path).is_relative_to(self.media_dir):
                self.ignored[path] = media_fingerprint(path)
                self.automatic.discard(path)
        self.paths = [p for p in self.paths if os.path.normcase(p) not in selected]
        self.save()

    def save(self):
        atomic_json(self.path, {"version": 1, "items": [
            {"media_path": p, **({"origin": "media-inbox"} if p in self.automatic else {})}
            for p in self.paths], "ignored": {
                str(Path(p).relative_to(self.media_dir)): fp for p, fp in self.ignored.items()
                if Path(p).is_relative_to(self.media_dir)}})
