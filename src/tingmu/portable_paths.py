"""Move internal media identities with a portable folder, keeping external paths intact."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import filecmp
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import threading
from tempfile import mkstemp
import time
import uuid

from .models import LanguagePair, StudyDocument, preparation_identity_settings

_THREAD_LOCK = threading.RLock()
MANIFEST = "portable-location.json"


@contextmanager
def _migration_lock(data, filename=".portable-migration.lock"):
    """An OS-owned lock survives neither crashes nor process exit."""
    with _THREAD_LOCK, (data / filename).open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"\0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            deadline = time.monotonic() + 25
            while True:
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("另一个听幕进程正在迁移便携数据，请稍后重试")
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _remap(value, old, new):
    if not isinstance(value, str) or not value:
        return value
    path = Path(value)
    if not path.is_absolute():
        return value
    # Normalize '..' before the ancestry test; a sibling is never an internal file.
    path = Path(os.path.abspath(path))
    try:
        relative = path.relative_to(old)
    except ValueError:
        return value
    return str((new / relative).resolve())


def _rewrite(value, old, new, key=""):
    if key in {"cues", "raw_display_text", "review_records", "review_responses", "messages"}:
        return value  # Content and provider evidence are immutable, even if they resemble paths.
    if isinstance(value, dict):
        if key == "noteDrafts":
            rewritten = {}
            for identity, text in value.items():
                media, separator, suffix = identity.partition("|")
                target = _remap(media, old, new) + separator + suffix
                if target in rewritten and rewritten[target] != text:
                    raise ValueError("便携迁移发现未保存笔记冲突；原草稿保留")
                rewritten[target] = text
            return rewritten
        return {name: _rewrite(item, old, new, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_rewrite(item, old, new, key) for item in value]
    if key in {"path", "subtitle"} or key.endswith(("_path", "_paths")):
        return _remap(value, old, new)
    return value


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _copy_published(source, target):
    # The new hash can live on a removable volume without hard-link support.
    descriptor, temporary = mkstemp(dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as new, source.open("rb") as old:
            shutil.copyfileobj(old, new)
            new.flush()
            os.fsync(new.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _backup(store, sources, old, new):
    from .storage import atomic_json
    folder = store.data_dir / "migration-backups" / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True)
    for source in sources:
        target = folder / source.relative_to(store.data_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    with closing(sqlite3.connect(store.db_path.as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(folder / "study.db")) as target:
            source.backup(target)
    atomic_json(folder / "migration.json", {"from": str(old), "to": str(new), "state": "backup"})
    return folder


def migrate_portable_paths(store):
    """Remap the portable installation or a registered study folder after it moves."""
    from .runtime import portable_root
    from .storage import atomic_json, media_fingerprint
    from .workspaces import MARKER
    root = portable_root()
    if (store.data_dir / MARKER).is_file():
        root = store.data_dir
    elif root is None or store.data_dir != (root / "data").resolve():
        return
    root = root.resolve()
    manifest = store.data_dir / MANIFEST
    with _migration_lock(store.data_dir):
        if not manifest.exists():
            atomic_json(manifest, {"version": 1, "root": str(root)})
            return
        previous = _read(manifest)
        old = Path(previous["root"])
        if not old.is_absolute() or old.parent == old:
            raise ValueError("便携数据位置记录无效；未修改学习记录")
        old = Path(os.path.abspath(old))
        # Cache keys hash the canonical path string, so case-only Windows renames matter too.
        if str(old) == str(root):
            return
        with store._material_write():
            for pending in (store.data_dir / "documents").glob("*.pending"):
                store._recover_material(pending.with_suffix(".json"))
        sources = [manifest]
        sources += list((store.data_dir / "documents").glob("*.json"))
        sources += [store.data_dir / name for name in
                    ("settings.json", "preparation-queue.json", "workspace.json",
                     "media-library.json", "quick-layout.json")
                    if (store.data_dir / name).exists()]
        subtitles = list((store.data_dir / "documents").glob("*.srt"))
        if any(not p.resolve().is_relative_to(store.data_dir) for p in sources + subtitles):
            raise ValueError("便携数据包含指向目录外的链接；未修改学习记录")
        backup = _backup(store, sources + subtitles, old, root)
        writes = {}
        renders = {}
        published = {}
        retired = set()
        for source in sources:
            if source == manifest:
                continue
            original = _read(source)
            changed = _rewrite(original, old, root)
            if original == changed:
                continue
            if source.name == "preparation-queue.json":
                for job in changed.get("jobs", []):
                    subtitle = job.get("subtitle_path")
                    spec = preparation_identity_settings(job["settings"])
                    parts = (["existing-lessons", job["media_path"], job["fingerprint"], spec]
                             if job.get("reuse_existing_transcript") else
                             [job["media_path"], job["fingerprint"], spec, subtitle,
                              media_fingerprint(subtitle) if subtitle else None])
                    if not job.get("reuse_existing_transcript") and "subtitle_inputs" in job:
                        parts.append(job["subtitle_inputs"])
                    identity = json.dumps(parts, sort_keys=True)
                    job["id"] = hashlib.sha256(identity.encode()).hexdigest()[:24]
            target = source
            if source.parent.name == "documents":
                target = store._document_path(changed["media_path"], LanguagePair(**changed["language"]))
                # Identical targets are allowed so an interrupted migration is retryable.
                if target != source and target.exists() and _read(target) != changed:
                    raise ValueError(f"便携迁移发现学习材料冲突，原文件与备份均保留：{backup}")
                if target != source:
                    retired.add(source)
                    for suffix in (".srt", ".translation.srt"):
                        if source.with_suffix(suffix).exists():
                            retired.add(source.with_suffix(suffix))
                metadata = changed.get("metadata", {})
                if metadata.get("stage") not in (None, "", "ready"):
                    # A checkpoint can be newer than either published track.
                    # Retain the exact last release, including absent translations.
                    for suffix in (".srt", ".translation.srt"):
                        old_track, new_track = source.with_suffix(suffix), target.with_suffix(suffix)
                        if old_track == new_track or not old_track.exists():
                            continue
                        if new_track.exists() and not filecmp.cmp(old_track, new_track, shallow=False):
                            raise ValueError(f"便携迁移发现已发布字幕冲突，原文件与备份均保留：{backup}")
                        published[new_track] = old_track
                elif source.with_suffix(".srt").exists() or metadata.get("cue_corrections"):
                    renders[target.with_suffix(".srt")] = StudyDocument.from_dict(changed)
            if target in writes and writes[target] != changed:
                raise ValueError(f"便携迁移发现重复学习材料，原文件与备份均保留：{backup}")
            writes[target] = changed
        # First validate every SQL uniqueness constraint. No JSON is modified if this fails.
        with store._connection(immediate=True) as db:
            try:
                from .drafts import remap_draft_media
                remap_draft_media(db, lambda path: _remap(path, old, root))
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in ("entries", "positions", "conversations", "chat_sessions", "chat_legacy_migrations"):
                    if table not in tables:
                        continue
                    paths = [row[0] for row in db.execute(f"SELECT DISTINCT media_path FROM {table}")]
                    for path in paths:
                        replacement = _remap(path, old, root)
                        if replacement != path:
                            db.execute(f"UPDATE {table} SET media_path=? WHERE media_path=?", (replacement, path))
                if "notebook_sources" in tables:
                    for entry_id, raw in db.execute("SELECT entry_id,entry_json FROM notebook_sources").fetchall():
                        entry = json.loads(raw)
                        replacement = _remap(entry.get("media_path"), old, root)
                        if replacement != entry.get("media_path"):
                            entry["media_path"] = replacement
                            db.execute("UPDATE notebook_sources SET entry_json=? WHERE entry_id=?",
                                       (json.dumps(entry, ensure_ascii=False), entry_id))
                if "chat_sessions" in tables:
                    for session_id, raw in db.execute("SELECT id,messages FROM chat_sessions").fetchall():
                        messages = json.loads(raw)
                        changed = False
                        for message in messages:
                            context = message.get("request_context")
                            if not isinstance(context, dict):
                                continue
                            media = context.get("media_path", "")
                            selected = context.get("selected_media", [])
                            replacement = _remap(media, old, root)
                            moved = [_remap(path, old, root) for path in selected]
                            if replacement != media or moved != selected:
                                context.update(media_path=replacement, selected_media=moved)
                                changed = True
                        if changed:
                            db.execute("UPDATE chat_sessions SET messages=? WHERE id=?",
                                       (json.dumps(messages, ensure_ascii=False), session_id))
                for target, value in writes.items():
                    atomic_json(target, value)
                for target, source in published.items():
                    if not target.exists():
                        _copy_published(source, target)
                for target, document in renders.items():
                    store._render_srt(document, target)
            except sqlite3.IntegrityError as error:
                raise ValueError(f"便携迁移发现学习记录冲突，数据库与备份均保留：{backup}") from error
            except Exception:
                # Existing source document files are retained until completion. Atomic JSON
                # replacements + the old manifest make a retry idempotent after interruption.
                raise
        # The OS lock spans the SQL commit and manifest update, serializing app startups.
        atomic_json(manifest, {"version": 1, "root": str(root),
                              "last_backup": str(backup.relative_to(store.data_dir))})
        atomic_json(backup / "migration.json", {"from": str(old), "to": str(root), "state": "complete",
                                                "rewritten_json": len(writes)})
        for source in retired - writes.keys() - renders.keys() - published.keys():
            source.unlink()  # A byte-for-byte copy remains in migration-backups.
