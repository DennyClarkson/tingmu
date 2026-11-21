"""Local Markdown language books with protected manual writing and source provenance."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import tempfile
import threading
import uuid

from .learning_notes import (
    ORGANIZE_PROMPT, ORGANIZE_SCHEMA, decode_topics,
    language_name, literal as _literal, render_topic_body,
)

BEGIN = "<!-- tingmu:collected:start -->"
END = "<!-- tingmu:collected:end -->"
_LOCK = threading.RLock()


class NotebookConflict(ValueError):
    """The editor or a source changed after the current operation started."""


def _language(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value):
        raise ValueError("学习语言标识无效。")
    return value


def _revision(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _title(language):
    name = language_name(language)
    return f"{name}学习笔记" if name != language else f"{language} 学习笔记"


def _video(value):
    return PureWindowsPath(str(value)).name


def _atomic_text(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class NotebookManager:
    """Collections are an inbox; only an explicit note operation changes Markdown."""

    def __init__(self, store):
        self.store = store
        self.root = Path(store.data_dir) / "notebooks"
        with store._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS notebook_state (
                    language TEXT PRIMARY KEY, topics TEXT NOT NULL, generated TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notebook_reviews (
                    entry_id INTEGER PRIMARY KEY, rating TEXT NOT NULL, repetitions INTEGER NOT NULL,
                    interval_days REAL NOT NULL, due_at TEXT NOT NULL, reviewed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notebook_collections (
                    entry_id INTEGER PRIMARY KEY, organized_at TEXT NOT NULL DEFAULT '',
                    entry_note TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS notebook_notes (
                    id TEXT PRIMARY KEY, language TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL,
                    entry_ids TEXT NOT NULL, sources_markdown TEXT NOT NULL, source_language TEXT NOT NULL,
                    origin TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notebook_v5_migrations (language TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS notebook_sources (
                    entry_id INTEGER PRIMARY KEY, target_language TEXT NOT NULL, entry_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notebook_entry_note_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, entry_id INTEGER NOT NULL,
                    entry_note TEXT NOT NULL, revision TEXT NOT NULL, saved_at TEXT NOT NULL
                );
            """)
        self._migrate_legacy()

    def _path(self, language):
        code = _language(language)
        # Some ISO language codes (e.g. con) are device names on Windows.
        folder = "~" + code if PureWindowsPath(code).is_reserved() else code
        return self.root / folder / "notebook.md"

    def _entries(self, language):
        with self.store._connection() as db:
            states = {r["entry_id"]: dict(r) for r in db.execute("SELECT * FROM notebook_collections")}
        result = []
        for entry in sorted(self.store.entries(), key=lambda e: e["id"]):
            if entry["target_language"] != language:
                continue
            state = states.get(entry["id"], {})
            note = state.get("entry_note", "")
            result.append({**entry, "organized": bool(state.get("organized_at")),
                           "organized_at": state.get("organized_at", ""), "entry_note": note,
                           "note_revision": _revision(note), "note_updated_at": state.get("updated_at", "")})
        return result

    def _migrate_legacy(self):
        # Migration indexes existing content without rewriting a single Markdown byte.
        with _LOCK, self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            done = {r[0] for r in db.execute("SELECT language FROM notebook_v5_migrations")}
            old = {r["language"]: dict(r) for r in db.execute("SELECT * FROM notebook_state")}
            paths = {p.parent.name.removeprefix("~"): p for p in self.root.glob("*/notebook.md")}
            for language in sorted(set(old) | set(paths)):
                if language in done:
                    continue
                _language(language)
                path = paths.get(language)
                text = path.read_text(encoding="utf-8") if path else ""
                available = {r[0] for r in db.execute("SELECT id FROM entries WHERE target_language=?", (language,))}
                linked = {int(i) for i in re.findall(r"tingmu://entry/([1-9][0-9]*)", text)}
                if language in old and text:
                    try:
                        linked.update(i for t in json.loads(old[language]["topics"]) for i in t["entry_ids"]
                                      if type(i) is int)
                    except (KeyError, TypeError, ValueError):
                        pass  # The real Markdown links remain authoritative if old metadata is damaged.
                known = sorted(available & linked)
                now = datetime.now(timezone.utc).isoformat()
                for entry_id in known:
                    db.execute("INSERT INTO notebook_collections(entry_id,organized_at) VALUES (?,?) "
                               "ON CONFLICT(entry_id) DO UPDATE SET organized_at=CASE WHEN organized_at='' "
                               "THEN excluded.organized_at ELSE organized_at END", (entry_id, now))
                    self._archive_source(db, dict(db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()))
                if text and (linked or language in old):
                    managed = text.count(BEGIN) == text.count(END) == 1 and text.index(BEGIN) < text.index(END)
                    body = text[text.index(BEGIN) + len(BEGIN):text.index(END)].strip() if managed else text
                    db.execute("INSERT OR IGNORE INTO notebook_notes VALUES (?,?,?,?,?,?,?,?,?,?)",
                               ("legacy-" + language, language, "既有学习笔记", body, json.dumps(known),
                                "### 迁移保留的原始材料\n\n" + body if known else "", "",
                                "legacy_managed" if managed else "legacy_plain", now, now))
                db.execute("INSERT INTO notebook_v5_migrations VALUES (?)", (language,))
            # Backfill only missing archives if an earlier v5 build created notes.
            archived = {r[0] for r in db.execute("SELECT entry_id FROM notebook_sources")}
            note_ids = {entry_id for row in db.execute("SELECT entry_ids FROM notebook_notes")
                        for entry_id in json.loads(row[0])}
            for entry_id in note_ids - archived:
                entry = db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
                if entry:
                    self._archive_source(db, dict(entry))

    @staticmethod
    def _archive_source(db, entry):
        fields = ("id", "kind", "media_path", "cue_id", "start", "text", "reading", "meaning", "note", "sentence",
                  "source_language", "target_language", "created_at")
        value = {field: entry[field] for field in fields}
        db.execute("INSERT INTO notebook_sources VALUES (?,?,?) ON CONFLICT(entry_id) DO UPDATE SET "
                   "target_language=excluded.target_language,entry_json=excluded.entry_json",
                   (entry["id"], entry["target_language"], json.dumps(value, ensure_ascii=False)))

    def source_entry(self, entry_id, *, target_language=None):
        """Resolve a note's source even after its collection was explicitly deleted."""
        if type(entry_id) is not int or entry_id < 1:
            raise ValueError("来源 ID 无效。")
        if target_language is not None:
            _language(target_language)
        with self.store._connection() as db:
            current = db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
            archived = db.execute("SELECT entry_json FROM notebook_sources WHERE entry_id=?", (entry_id,)).fetchone()
        value = dict(current) if current else json.loads(archived[0]) if archived else None
        if value is None or (target_language is not None and value["target_language"] != target_language):
            return None
        return {**value, "archived": current is None}

    def _write(self, language, text, expected_revision):
        path = self._path(language)
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        if _revision(old) != expected_revision:
            raise NotebookConflict("笔记已被其他窗口或编辑器修改，请重新加载后保存。")
        if old == text:
            return
        if old:
            backup = path.parent / "backups" / (
                datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex[:8] + ".md")
            _atomic_text(backup, path.read_bytes().decode("utf-8"))
        _atomic_text(path, text)

    @staticmethod
    def _markers(note_id):
        return (f"<!-- tingmu:note:{note_id}:start -->", f"<!-- tingmu:note:{note_id}:end -->",
                f"<!-- tingmu:note:{note_id}:sources -->")

    def _note_span(self, markdown, note):
        if note["origin"] == "legacy_managed":
            start, end = BEGIN, END
        elif note["origin"] == "legacy_plain":
            if note["body"] and markdown.count(note["body"]) == 1:
                offset = markdown.index(note["body"])
                return offset, offset + len(note["body"]), note["body"]
            return None
        else:
            start, end, _ = self._markers(note["id"])
        if markdown.count(start) != 1 or markdown.count(end) != 1:
            return None
        left, right = markdown.index(start), markdown.index(end)
        if left >= right:
            return None
        return left, right + len(end), markdown[left + len(start):right].strip()

    def _view_note(self, row, markdown):
        note = dict(row)
        note["entry_ids"] = json.loads(note["entry_ids"]) if isinstance(note["entry_ids"], str) else note["entry_ids"]
        span = self._note_span(markdown, note)
        note["detached"] = span is None
        if span:
            content = span[2]
            if note["origin"].startswith("legacy"):
                note["body"] = content
                note["markdown"] = content
            else:
                source_marker = self._markers(note["id"])[2]
                parts = content.split(source_marker, 1)
                head = parts[0].strip()
                first, _, body = head.partition("\n")
                if first.startswith("## "):
                    note["title"] = first[3:].strip()
                    note["body"] = body.strip()
                else:
                    note["body"] = head
                if len(parts) == 2:
                    note["sources_markdown"] = parts[1].strip()
                note["markdown"] = content.replace(source_marker, "")
        else:
            note["markdown"] = f"## {note['title']}\n\n{note['body']}\n\n{note['sources_markdown']}"
        return note

    def snapshot(self, target_language):
        language = _language(target_language)
        with _LOCK:
            path = self._path(language)
            markdown = path.read_text(encoding="utf-8") if path.exists() else ""
            entries = self._entries(language)
            with self.store._connection() as db:
                notes = [self._view_note(row, markdown) for row in db.execute(
                    "SELECT * FROM notebook_notes WHERE language=? ORDER BY created_at,id", (language,))]
                languages = {r[0] for r in db.execute("SELECT language FROM notebook_v5_migrations")}
            all_entries = self.store.entries()
            languages.update(e["target_language"] for e in all_entries)
            languages.add(language)
            counts = Counter(e["target_language"] for e in all_entries)
            organized = sum(e["organized"] for e in entries)
            due = self.practice(language)
            topics = [{"id": n["id"], "title": n["title"], "entry_ids": n["entry_ids"]} for n in notes]
            return {"language": language, "title": _title(language), "markdown": markdown,
                    "revision": _revision(markdown), "path": str(path), "topics": topics,
                    "collections": entries, "entries": entries, "notes": notes,
                    "books": [{"language": lang, "title": _title(lang), "count": counts[lang]}
                              for lang in sorted(languages)],
                    "stats": {"total": len(entries), "words": sum(e["kind"] == "word" for e in entries),
                              "notes": sum(e["kind"] == "note" for e in entries), "due": len(due),
                              "collections": len(entries), "organized": organized,
                              "unorganized": len(entries) - organized, "note_count": len(notes),
                              "markdown_characters": len(markdown)}}

    def sync(self, target_language):
        language = _language(target_language)
        with _LOCK:
            current = self.snapshot(language)
            if not self._path(language).exists():
                self._write(language, f"# {_title(language)}\n\n## 我的笔记\n\n"
                            "在这里记录自己的理解和例句。收藏保留在收集箱，只有明确整理后才加入笔记。\n",
                            current["revision"])
            with self.store._connection() as db:
                db.execute("INSERT OR IGNORE INTO notebook_v5_migrations VALUES (?)", (language,))
            return self.snapshot(language)

    def save(self, target_language, markdown, expected_revision):
        self._check_text(markdown, 10000000, allow_empty=True)
        with _LOCK:
            language = _language(target_language)
            original = self.snapshot(language)
            old_links = set(re.findall(r"tingmu://entry/([1-9][0-9]*)", original["markdown"]))
            for entry_id in set(re.findall(r"tingmu://entry/([1-9][0-9]*)", markdown)) - old_links:
                if self.source_entry(int(entry_id), target_language=language) is None:
                    raise ValueError("笔记新增的来源不存在或不属于当前学习语言。")
            self._write(language, markdown, expected_revision)
            with self.store._connection() as db:
                db.execute("INSERT OR IGNORE INTO notebook_v5_migrations VALUES (?)", (target_language,))
            return self.snapshot(target_language)

    @staticmethod
    def _check_text(value, maximum, allow_empty=False):
        if (not isinstance(value, str) or len(value) > maximum or "\x00" in value
                or (not allow_empty and not value.strip())):
            raise ValueError("笔记内容无效、为空或过长。")

    def _selected(self, entries, ids):
        if (not isinstance(ids, list) or any(type(i) is not int for i in ids)
                or len(ids) != len(set(ids))):
            raise ValueError("收藏 ID 无效或重复。")
        by_id = {entry["id"]: entry for entry in entries}
        if not set(ids) <= set(by_id):
            raise ValueError("收藏不存在或不属于当前学习语言。")
        return [by_id[i] for i in ids]

    def update_entry_note(self, entry_id, note, expected_revision=None, *, target_language=None):
        if type(entry_id) is not int:
            raise ValueError("收藏 ID 无效。")
        self._check_text(note, 20000, allow_empty=True)
        if target_language is not None:
            _language(target_language)
        with _LOCK, self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            entry = db.execute("SELECT target_language FROM entries WHERE id=?", (entry_id,)).fetchone()
            if not entry or (target_language is not None and entry[0] != target_language):
                raise ValueError("收藏不存在或不属于当前学习语言。")
            row = db.execute("SELECT entry_note FROM notebook_collections WHERE entry_id=?", (entry_id,)).fetchone()
            old = row[0] if row else ""
            if expected_revision is not None and expected_revision != _revision(old):
                raise NotebookConflict("条目备注已修改，请重新加载后保存。")
            if old and old != note:
                db.execute("INSERT INTO notebook_entry_note_history(entry_id,entry_note,revision,saved_at) "
                           "VALUES (?,?,?,?)", (entry_id, old, _revision(old), datetime.now(timezone.utc).isoformat()))
            db.execute("INSERT INTO notebook_collections(entry_id,entry_note,updated_at) VALUES (?,?,?) "
                       "ON CONFLICT(entry_id) DO UPDATE SET entry_note=excluded.entry_note,updated_at=excluded.updated_at",
                       (entry_id, note, datetime.now(timezone.utc).isoformat()))
        return next(e for e in self._entries(entry[0]) if e["id"] == entry_id)

    def _source_markdown(self, entries):
        if not entries:
            return ""
        lines = ["### 原始收藏来源", ""]
        for entry in entries:
            entry_id = entry["id"]
            lines.extend([f"[来源 #{entry_id}](tingmu://entry/{entry_id}) · {_literal(entry['text'])}", "",
                          f"{_literal(_video(entry['media_path']))} · {entry['start']:.2f}s", ""])
            for key, label in (("reading", "读音"), ("meaning", "已有释义"), ("sentence", "原句"),
                               ("note", "原收藏备注"), ("entry_note", "我的条目备注")):
                if entry.get(key):
                    lines.extend([f"**{label}**：{_literal(entry[key])}", ""])
        return "\n".join(lines).rstrip()

    def _make_note(self, language, title, body, entries, source_language="", origin="manual"):
        self._check_text(title, 200)
        self._check_text(body, 200000)
        if any(c in title for c in "\r\n") or "<!-- tingmu:" in body or "<!-- tingmu:" in title:
            raise ValueError("笔记不能包含保留的管理标记。")
        now = datetime.now(timezone.utc).isoformat()
        return {"id": uuid.uuid4().hex, "language": language, "title": title, "body": body,
                "entry_ids": [e["id"] for e in entries], "sources_markdown": self._source_markdown(entries),
                "source_language": source_language, "origin": origin, "created_at": now, "updated_at": now}

    def _render_note(self, note):
        start, end, sources = self._markers(note["id"])
        return f"{start}\n## {note['title']}\n\n{note['body']}\n\n{sources}\n{note['sources_markdown']}\n{end}"

    @staticmethod
    def _append_blocks(base, blocks):
        separator = "" if base.endswith("\n\n") else "\n" if base.endswith("\n") else "\n\n"
        return base + separator + blocks + "\n"

    def _commit(self, original, markdown, notes=(), delete_id=None, check_entries=True, cancelled=None):
        language = original["language"]
        # SQLite prevents a concurrent collection update during the final recheck/write.
        # A failed DB commit restores the previous file only if nobody edited it meanwhile.
        written = False
        try:
            with self.store._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                if cancelled and cancelled():
                    raise ValueError("笔记操作已取消，原笔记未修改。")
                if check_entries and self._entries(language) != original["collections"]:
                    raise NotebookConflict("操作期间收藏或条目备注已变更，请重试。")
                if delete_id is not None:
                    db.execute("DELETE FROM notebook_notes WHERE id=? AND language=?", (delete_id, language))
                for note in notes:
                    db.execute("INSERT INTO notebook_notes VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                               "title=excluded.title,body=excluded.body,sources_markdown=excluded.sources_markdown,"
                               "origin=excluded.origin,updated_at=excluded.updated_at",
                               (note["id"], language, note["title"], note["body"], json.dumps(note["entry_ids"]),
                                note["sources_markdown"], note["source_language"], note["origin"],
                                note["created_at"], note["updated_at"]))
                    for entry_id in note["entry_ids"]:
                        source = next((e for e in original["collections"] if e["id"] == entry_id), None)
                        if source:
                            self._archive_source(db, source)
                        db.execute("INSERT INTO notebook_collections(entry_id,organized_at) VALUES (?,?) "
                                   "ON CONFLICT(entry_id) DO UPDATE SET organized_at=CASE WHEN organized_at='' "
                                   "THEN excluded.organized_at ELSE organized_at END", (entry_id, note["updated_at"]))
                db.execute("INSERT OR IGNORE INTO notebook_v5_migrations VALUES (?)", (language,))
                self._write(language, markdown, original["revision"])
                written = True
        except Exception:
            if written:
                path = self._path(language)
                if path.exists() and _revision(path.read_text(encoding="utf-8")) == _revision(markdown):
                    _atomic_text(path, original["markdown"])
            raise
        return self.snapshot(language)

    def append_note(self, target_language, title, body, entry_ids=None, expected_revision=None):
        language = _language(target_language)
        with _LOCK:
            original = self.snapshot(language)
            if expected_revision is not None and expected_revision != original["revision"]:
                raise NotebookConflict("笔记已修改，请重新加载后追加。")
            selected = self._selected(original["collections"], [] if entry_ids is None else entry_ids)
            self._check_linked_ids(body, {entry["id"] for entry in selected})
            note = self._make_note(language, title, body, selected)
            base = original["markdown"] or f"# {_title(language)}\n"
            return self._commit(original, self._append_blocks(base, self._render_note(note)), [note])

    def update_note(self, target_language, note_id, title, body, expected_revision):
        language = _language(target_language)
        with _LOCK:
            original = self.snapshot(language)
            if expected_revision != original["revision"]:
                raise NotebookConflict("笔记已修改，请重新加载后保存。")
            note = next((n for n in original["notes"] if n["id"] == note_id), None)
            if note is None:
                raise ValueError("学习笔记不存在或不属于当前学习语言。")
            self._check_linked_ids(body, set(note["entry_ids"]))
            span = self._note_span(original["markdown"], note)
            if span is None:
                raise NotebookConflict("此条笔记的管理标记已被编辑，请使用整本 Markdown 编辑保留现有内容。")
            updated = self._make_note(language, title, body, [], note["source_language"], "edited")
            updated.update(id=note_id, entry_ids=note["entry_ids"], sources_markdown=note["sources_markdown"],
                           created_at=note["created_at"])
            text = original["markdown"][:span[0]] + self._render_note(updated) + original["markdown"][span[1]:]
            return self._commit(original, text, [updated])

    @staticmethod
    def _check_linked_ids(body, allowed):
        if isinstance(body, str):
            linked = {int(value) for value in re.findall(r"tingmu://entry/([1-9][0-9]*)", body)}
            if not linked <= allowed:
                raise ValueError("笔记引用的来源必须包含在明确指定的 entry_ids 中。")

    def delete_note(self, target_language, note_id, expected_revision):
        language = _language(target_language)
        with _LOCK:
            original = self.snapshot(language)
            if expected_revision != original["revision"]:
                raise NotebookConflict("笔记已修改，请重新加载后删除。")
            note = next((n for n in original["notes"] if n["id"] == note_id), None)
            if note is None:
                raise ValueError("学习笔记不存在或不属于当前学习语言。")
            span = self._note_span(original["markdown"], note)
            if span is None:
                raise NotebookConflict("此条笔记已在 Markdown 中修改，请先核对原文。")
            text = original["markdown"][:span[0]] + original["markdown"][span[1]:]
            return self._commit(original, text, delete_id=note_id)

    def organize(self, target_language, agent, source_language="zh-CN", cancelled=None, *,
                 instructions="", entry_ids=None):
        language = _language(target_language)
        _language(source_language)
        self._check_text(instructions, 4000, allow_empty=True)
        if cancelled and cancelled():
            raise ValueError("笔记整理已取消，原笔记未修改。")
        original = self.snapshot(language)
        entries = (self._selected(original["collections"], entry_ids) if entry_ids is not None else
                   [e for e in original["collections"] if not e["organized"]])
        if not entries:
            return original
        topics = []
        for offset in range(0, len(entries), 12):
            if cancelled and cancelled():
                raise ValueError("笔记整理已取消，原笔记未修改。")
            batch = entries[offset:offset + 12]
            if getattr(agent, "provider", None) == "local":
                details = []
                for entry in batch:
                    details.append({"entry_id": entry["id"], "comparison": "", "usage": entry.get("meaning", ""),
                                    "grammar": "", "pitfalls": "", "example": "", "example_translation": "",
                                    "goal": "回听来源，遮住释义后回忆意思，再用自己的话描述适用场景。",
                                    "remark": entry.get("entry_note", "") or entry.get("note", "")})
                current = [{"title": "我的学习材料", "entry_ids": [e["id"] for e in batch],
                            "learning_note": "本地整理仅引用已有释义与备注。先回忆词句，再对照原片检查；"
                                             "跨语言差异、语法和新例句尚待进一步分析。", "entry_notes": details}]
            else:
                payload = {"target_language": language, "source_language": source_language,
                           "target_language_name": language_name(language),
                           "source_language_name": language_name(source_language),
                           "instructions": instructions,
                           "entries": [{"id": e["id"], "kind": e["kind"], "text": e["text"][:500],
                                        "reading": e["reading"][:300], "meaning": e["meaning"][:1000],
                                        "sentence": e["sentence"][:1200], "saved_note": e["note"][:1500],
                                        "entry_note": e["entry_note"][:3000],
                                        "video": _video(e["media_path"])[:160]} for e in batch]}
                raw = agent._complete(ORGANIZE_PROMPT, payload, ORGANIZE_SCHEMA)
                try:
                    current = decode_topics(raw, batch)
                except (ValueError, TypeError, KeyError):
                    raise ValueError("模型学习笔记缺少有效学习内容或含重复、缺失、虚构来源；原笔记未修改。") from None
            topics.extend(current)
        if cancelled and cancelled():
            raise ValueError("笔记整理已取消，原笔记未修改。")
        notes = []
        for topic in topics:
            selected = self._selected(entries, topic["entry_ids"])
            body = render_topic_body(topic, selected, source_language, language)
            notes.append(self._make_note(language, topic["title"], body, selected, source_language,
                                         "local" if getattr(agent, "provider", None) == "local" else "agent"))
        with _LOCK:
            base = original["markdown"] or f"# {_title(language)}\n"
            text = self._append_blocks(base, "\n\n".join(self._render_note(n) for n in notes))
            return self._commit(original, text, notes, cancelled=cancelled)

    def review(self, entry_id, rating):
        if type(entry_id) is not int or rating not in ("again", "hard", "good", "easy"):
            raise ValueError("复习评分无效。")
        now = datetime.now(timezone.utc)
        with self.store._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT id FROM entries WHERE id=?", (entry_id,)).fetchone():
                raise ValueError("收藏来源不存在。")
            prior = db.execute("SELECT * FROM notebook_reviews WHERE entry_id=?", (entry_id,)).fetchone()
            interval = prior["interval_days"] if prior else 0
            interval = {"again": 10 / 1440, "hard": max(1, interval * 1.2),
                        "good": max(2, interval * 2.5), "easy": max(4, interval * 3.5)}[rating]
            interval = min(interval, 365)
            repetitions = (prior["repetitions"] if prior else 0) + 1
            result = {"entry_id": entry_id, "rating": rating, "repetitions": repetitions,
                      "interval_days": interval, "due_at": (now + timedelta(days=interval)).isoformat(),
                      "reviewed_at": now.isoformat()}
            db.execute("INSERT INTO notebook_reviews VALUES (?,?,?,?,?,?) ON CONFLICT(entry_id) DO UPDATE SET "
                       "rating=excluded.rating,repetitions=excluded.repetitions,interval_days=excluded.interval_days,"
                       "due_at=excluded.due_at,reviewed_at=excluded.reviewed_at", tuple(result.values()))
        return result

    def practice(self, target_language):
        language = _language(target_language)
        with self.store._connection() as db:
            reviews = {r["entry_id"]: dict(r) for r in db.execute("SELECT * FROM notebook_reviews")}
        now = datetime.now(timezone.utc).isoformat()
        cards = []
        for entry in self._entries(language):
            review = reviews.get(entry["id"], {})
            if review.get("due_at", "") > now:
                continue
            cards.append({**entry, "entry_id": entry["id"], "question": f"回忆这个表达的读音和用法：{entry['text']}",
                          "answer": "\n".join(str(entry[k]) for k in ("reading", "meaning", "sentence", "note", "entry_note")
                                              if entry.get(k)), "source_url": f"tingmu://entry/{entry['id']}",
                          "due_at": review.get("due_at", ""), "repetitions": review.get("repetitions", 0)})
        return sorted(cards, key=lambda card: (card["due_at"], card["id"]))
