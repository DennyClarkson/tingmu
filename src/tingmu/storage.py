"""Thread-safe SQLite study journal and atomic, human-readable document cache."""

from contextlib import contextmanager
from dataclasses import asdict, fields
from datetime import datetime, timezone
import csv
from functools import cached_property
import hashlib
import json
import os
from pathlib import Path
import tempfile
import uuid

from .models import AppSettings, Cue, LanguagePair, StudyDocument, Token
from .database import Database, word_key as word_key


def canonical_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def media_fingerprint(path: str | Path) -> dict:
    try:
        stat = Path(path).stat()
        return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    except OSError:
        return {}


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, data_dir: Path | None = None):
        from .runtime import data_root
        self.data_dir = Path(data_dir or data_root()).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "study.db"
        self.database = Database(self.db_path)
        from .portable_paths import migrate_portable_paths
        migrate_portable_paths(self)

    def _connection(self, immediate=False):
        # Compatibility seam for existing readers and SQL instrumentation.
        return self.database.connection(immediate=immediate)

    def close(self):
        self.database.close()

    @cached_property
    def model_registry(self):
        from .providers import ModelRegistry
        return ModelRegistry(self.data_dir)

    def load_settings(self) -> AppSettings:
        from .runtime import is_clean_install

        settings = AppSettings()
        path = self.data_dir / "settings.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                allowed = {f.name for f in fields(AppSettings) if not f.name.startswith("_")}
                settings = AppSettings(**{k: v for k, v in data.items() if k in allowed})
        except (OSError, ValueError, TypeError):
            if is_clean_install():
                settings.text_models_enabled = settings.cloud_text_enabled = False
        return self.model_registry.load_roles(settings)

    def save_settings(self, settings: AppSettings) -> None:
        from .global_models import workspace_preferences
        self.model_registry.save_role_changes(settings)
        atomic_json(self.data_dir / "settings.json", workspace_preferences(settings))

    def _document_path(self, media_path: str, language: LanguagePair) -> Path:
        identity = json.dumps([canonical_path(media_path), asdict(language)], sort_keys=True)
        key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        return self.data_dir / "documents" / f"{key}.json"

    @contextmanager
    def _material_write(self):
        # Coordinate independent Store instances, including a second app process.
        with self._connection(immediate=True):
            yield

    def material_paths(self, media_path: str, language: LanguagePair) -> tuple[Path, Path]:
        document = self._document_path(media_path, language)
        return document, document.with_suffix(".srt")

    def ensure_subtitle_files(self, document):
        """Restore missing published subtitles from the current authoritative document."""
        path, srt = self.material_paths(document.media_path, document.language)
        if srt.exists() and path.with_suffix(".translation.srt").exists():
            return
        with self._material_write():
            self._recover_material(path)
            current = self.load_document(document.media_path, document.language)
            if current:
                self._render_srt(current, srt)

    def _backup_material(self, path):
        backup = self.data_dir / "material-backups" / (
            datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:10])
        backup.mkdir(parents=True)
        for source in (path, path.with_suffix(".srt"), path.with_suffix(".translation.srt")):
            if source.exists():
                with (backup / source.name).open("xb") as stream:
                    stream.write(source.read_bytes())
                    stream.flush()
                    os.fsync(stream.fileno())
        return backup

    @staticmethod
    def _render_srt(document, path):
        from .lesson_material import translated_subtitles
        from .media import save_srt
        from .timeline import SubtitleTimeline
        # Zero-length source markers have no playable caption interval.
        save_srt([cue for cue in SubtitleTimeline(document.cues).cues if cue.end > cue.start], path)
        translated = path.with_suffix(".translation.srt")
        cues = translated_subtitles(document)
        if cues:
            save_srt(cues, translated)
        else:
            translated.unlink(missing_ok=True)

    def _recover_material(self, path):
        marker = path.with_suffix(".pending")
        if not marker.exists():
            return
        if path.exists():
            current = StudyDocument.from_dict(json.loads(path.read_bytes()))
            self._render_srt(current, path.with_suffix(".srt"))
        else:
            path.with_suffix(".srt").unlink(missing_ok=True)
            path.with_suffix(".translation.srt").unlink(missing_ok=True)
        marker.unlink()

    def save_document(self, document: StudyDocument, *, replace_existing=False) -> Path:
        with self._material_write():
            path = self._document_path(document.media_path, document.language)
            self._recover_material(path)
            if path.exists():
                try:
                    metadata = json.loads(path.read_bytes()).get("metadata", {})
                    previous = metadata.get("cue_corrections", {})
                except (ValueError, AttributeError):
                    # Replacement of an unreadable cache must retain its original bytes.
                    metadata, previous = None, {}
                replaced_corrections = previous and previous != document.metadata.get("cue_corrections", {})
                if replaced_corrections and not replace_existing:
                    raise ValueError("当前字幕已有新的逐句修订，请重新读取材料。")
                if (metadata is None or replaced_corrections or
                        bool(metadata.get("subtitle_plan")) != bool(document.metadata.get("subtitle_plan"))):
                    self._backup_material(path)
            return self._save_document(document)

    def _save_document(self, document: StudyDocument, *, payload=None) -> Path:
        document.media_path = canonical_path(document.media_path)
        document.metadata.setdefault("media_fingerprint", media_fingerprint(document.media_path))
        document.metadata["schema_version"] = 1
        path = self._document_path(document.media_path, document.language)
        if payload is None:
            payload = document.to_dict()
        else:
            payload["metadata"] = document.metadata
        srt = path.with_suffix(".srt")
        managed = (srt.exists() or document.metadata.get("stage") == "ready"
                   or document.metadata.get("subtitle_path") or document.metadata.get("cue_corrections"))
        if managed:
            # JSON is authoritative. The journal repairs an interrupted two-file
            # publication on the next read, including after a process crash.
            marker = path.with_suffix(".pending")
            atomic_json(marker, {})
            try:
                self._render_srt(document, srt)
                atomic_json(path, payload)
            except Exception:
                self._recover_material(path)
                raise
            marker.unlink()
        else:
            atomic_json(path, payload)
        return path

    def load_document(self, media_path: str, language: LanguagePair) -> StudyDocument | None:
        path = self._document_path(media_path, language)
        if path.with_suffix(".pending").exists():
            with self._material_write():
                self._recover_material(path)
        if not path.exists():
            return None
        try:
            doc = StudyDocument.from_dict(json.loads(path.read_text(encoding="utf-8")))
            expected = doc.metadata.get("media_fingerprint")
            if expected and expected != media_fingerprint(media_path):
                return None
            doc.cues.sort(key=lambda c: (c.start, c.end))
            return doc
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def reset_material(self, media_path: str, language: LanguagePair, mode: str = "all") -> bool:
        """Remove current generated material, retaining an app-local recovery copy.

        Never touches media, raw ASR audit files, journals, conversations or progress.
        Callers serialize this operation against preparation/import workers.
        """
        if mode not in ("all", "teaching"):
            raise ValueError("请选择清除全部识别与讲解，或仅清除讲解")
        with self._material_write():
            path = self._document_path(media_path, language)
            self._recover_material(path)
            return self._reset_material(path, mode)

    def _reset_material(self, path, mode):
        if not path.exists():
            return False
        raw = path.read_bytes()
        document = StudyDocument.from_dict(json.loads(raw)) if mode == "teaching" else None
        self._backup_material(path)
        if mode == "all":
            atomic_json(path.with_suffix(".pending"), {})
            path.unlink()
            self._recover_material(path)
        else:
            from .material_reset import reset_teaching_document
            document = reset_teaching_document(document)
            self._save_document(document)
        return True

    def _save_entry(self, db, kind: str, media_path: str, cue: Cue, language: LanguagePair,
                    text: str, reading: str = "", meaning: str = "", note: str | None = "") -> int:
        if kind == "word":
            db.execute("INSERT OR IGNORE INTO word_forms VALUES (?,?,?)",
                       (language.target, text, word_key(text, language.target)))
        return db.execute("""
                INSERT INTO entries(kind,media_path,cue_id,start,text,reading,meaning,note,sentence,
                                    source_language,target_language,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(kind,media_path,cue_id,text,source_language,target_language)
                DO UPDATE SET reading=excluded.reading, meaning=excluded.meaning,
                              note=COALESCE(?, entries.note), sentence=excluded.sentence
                RETURNING id
            """, (kind, canonical_path(media_path), cue.id, cue.start, text, reading, meaning, note or "",
                  cue.text, language.source, language.target, datetime.now(timezone.utc).isoformat(), note)).fetchone()[0]

    def save_word(self, media_path: str, cue: Cue, token: Token, language: LanguagePair) -> None:
        with self._connection() as db:
            self._save_entry(db, "word", media_path, cue, language, token.text, token.reading, token.meaning, note=None)

    def save_note(self, media_path: str, cue: Cue, note: str | None, language: LanguagePair) -> None:
        with self._connection() as db:
            self._save_entry(db, "note", media_path, cue, language, cue.text,
                             meaning=cue.translation, note=note)

    def entries(self, media_path: str | None = None) -> list[dict]:
        with self._connection() as db:
            if media_path is not None:
                rows = db.execute("SELECT * FROM entries WHERE media_path=? ORDER BY start,id",
                                  (canonical_path(media_path),)).fetchall()
            else:
                rows = db.execute("SELECT * FROM entries ORDER BY created_at DESC,id DESC").fetchall()
        return [dict(row) for row in rows]

    def delete_entry(self, entry_id: int) -> None:
        with self._connection() as db:
            db.execute("DELETE FROM entries WHERE id=?", (entry_id,))

    def export_markdown(self, path: Path, media_path: str | None = None) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        output = ["# 听幕学习笔记", ""]
        for entry in self.entries(media_path):
            output.extend([f"## {entry['text']}", "",
                           f"{Path(entry['media_path']).name} · {entry['start']:.2f}s · "
                           f"{entry['source_language']} → {entry['target_language']}", ""])
            for label, key in [("读音", "reading"), ("释义", "meaning"), ("原句", "sentence"), ("笔记", "note")]:
                if entry[key]:
                    output.extend([f"{label}：{entry[key]}", ""])
        path.write_text("\n".join(output), encoding="utf-8")
        return path

    def export_csv(self, path: Path, media_path: str | None = None) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        columns = ["kind", "text", "reading", "meaning", "sentence", "note", "media_path", "start",
                   "source_language", "target_language", "created_at"]
        with path.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for entry in self.entries(media_path):
                # Prevent spreadsheet software treating user/model text as formulas.
                safe = {k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                            else v) for k, v in entry.items()}
                writer.writerow(safe)
        return path

    def save_review(self, media_path: str, text: str) -> Path:
        key = hashlib.sha256(canonical_path(media_path).encode()).hexdigest()[:16]
        path = self.data_dir / "reviews" / f"{key}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def save_position(self, media_path: str, milliseconds: int) -> None:
        with self._connection() as db:
            db.execute("INSERT INTO positions VALUES (?,?) ON CONFLICT(media_path) "
                       "DO UPDATE SET milliseconds=excluded.milliseconds",
                       (canonical_path(media_path), max(0, int(milliseconds))))

    def load_position(self, media_path: str) -> int:
        with self._connection() as db:
            row = db.execute("SELECT milliseconds FROM positions WHERE media_path=?",
                             (canonical_path(media_path),)).fetchone()
        return row[0] if row else 0

    def load_positions(self, media_paths) -> dict[str, int]:
        """Read one library's positions with a single query, including zero defaults."""
        positions = dict.fromkeys((canonical_path(path) for path in media_paths), 0)
        if not positions:
            return positions
        with self._connection() as db:
            rows = db.execute("SELECT media_path,milliseconds FROM positions "
                              "WHERE media_path IN (SELECT value FROM json_each(?))",
                              (json.dumps(list(positions)),)).fetchall()
        positions.update(rows)
        return positions

    def save_conversation(self, media_path: str, language: LanguagePair, messages: list[dict]) -> None:
        """Persist text chat separately from lesson documents; no API credentials."""
        clean = [{"role": str(m["role"]), "content": str(m["content"])} for m in messages
                 if m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)]
        with self._connection() as db:
            db.execute("INSERT INTO conversations VALUES (?,?,?,?,?) "
                       "ON CONFLICT(media_path,source_language,target_language) DO UPDATE SET "
                       "messages=excluded.messages,updated_at=excluded.updated_at",
                       (canonical_path(media_path) if media_path else "", language.source, language.target,
                        json.dumps(clean, ensure_ascii=False), datetime.now(timezone.utc).isoformat()))

    def load_conversation(self, media_path: str, language: LanguagePair) -> list[dict]:
        with self._connection() as db:
            row = db.execute("SELECT messages FROM conversations WHERE media_path=? AND source_language=? "
                             "AND target_language=?", (canonical_path(media_path) if media_path else "",
                                                       language.source, language.target)).fetchone()
        if row is None:
            return []
        try:
            parsed = json.loads(row[0])
            if not isinstance(parsed, list):
                return []
            return [m for m in parsed if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                    and isinstance(m.get("content"), str)]
        except (ValueError, TypeError):
            return []
