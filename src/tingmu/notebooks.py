"""Local Markdown language books with protected manual writing and source provenance."""

from contextlib import contextmanager
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
from .storage import canonical_path, word_key
from .timestamps import utc_timestamp

BEGIN = "<!-- tingmu:collected:start -->"
END = "<!-- tingmu:collected:end -->"
_LOCK = threading.RLock()
_SOURCE_FIELDS = ("id", "kind", "media_path", "cue_id", "start", "text", "reading", "meaning", "note", "sentence",
                  "source_language", "target_language", "created_at")


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
        self.database = store.database
        self.root = Path(store.data_dir) / "notebooks"

        self._migrate_legacy()

    def _path(self, language):
        code = _language(language)
        # Some ISO language codes (e.g. con) are device names on Windows.
        folder = "~" + code if PureWindowsPath(code).is_reserved() else code
        return self.root / folder / "notebook.md"

    def _entries(self, language):
        with self.store._connection() as db:
            states = {r["entry_id"]: dict(r) for r in db.execute(
                "SELECT c.* FROM entries e JOIN notebook_collections c ON c.entry_id=e.id "
                "WHERE e.target_language=?", (language,))}
            entries = [dict(r) for r in db.execute("SELECT * FROM entries WHERE target_language=? ORDER BY id",
                                                  (language,))]
            archived = [json.loads(r[0]) for r in db.execute("""
                SELECT s.entry_json FROM notebook_sources s WHERE s.target_language=?
                AND NOT EXISTS(SELECT 1 FROM entries e WHERE e.id=s.entry_id)
                AND s.entry_id IN (SELECT ids.value FROM notebook_notes n, json_each(n.entry_ids) ids
                                   WHERE n.language=?)
            """, (language, language))]
        word_sources = {}
        for e in entries + archived:
            if e["kind"] == "word":
                group = word_sources.setdefault((e["source_language"], word_key(e["text"], language)), {})
                group.setdefault((e["media_path"], e["cue_id"]),
                                 {k: e[k] for k in ("id", "media_path", "cue_id", "start", "sentence", "text")})
        word_sources = {key: sorted(group.values(), key=lambda s: s["id"]) for key, group in word_sources.items()}
        result = []
        for entry in entries:
            state = states.get(entry["id"], {})
            note = state.get("entry_note", "")
            result.append({**entry, "organized": bool(state.get("organized_at")),
                           "organized_at": state.get("organized_at", ""), "entry_note": note,
                           "word_sources": word_sources.get((entry["source_language"], word_key(entry["text"], language)), [])
                           if entry["kind"] == "word" else [],
                           "note_revision": _revision(note), "note_updated_at": state.get("updated_at", "")})
        return result

    @staticmethod
    def _word_rows(db, language, keys=None):
        """Include deleted collections only while a saved note still cites them."""
        forms = live_filter = archived_filter = ""
        params = []
        if keys is not None:
            forms = """WITH forms AS (SELECT text FROM word_forms WHERE target_language=?
                                      AND word_key IN (SELECT value FROM json_each(?)))"""
            params = [language.target, json.dumps(list(keys), ensure_ascii=False)]
            live_filter = "AND e.text IN forms"
            archived_filter = "AND json_extract(entry_json,'$.text') IN forms"
        return db.execute(f"""
            {forms}
            SELECT e.id, e.text, e.media_path, e.cue_id, COALESCE(c.organized_at,'') AS organized_at,
                   0 AS archived, EXISTS(SELECT 1 FROM notebook_sources s WHERE s.entry_id=e.id) AS cited
            FROM entries e LEFT JOIN notebook_collections c ON c.entry_id=e.id
            WHERE e.kind='word' AND e.target_language=? AND e.source_language=?
                  {live_filter}
            UNION ALL
            SELECT s.entry_id, json_extract(entry_json,'$.text'), json_extract(entry_json,'$.media_path'),
                   json_extract(entry_json,'$.cue_id'), '', 1, 1
            FROM notebook_sources s
            WHERE json_extract(entry_json,'$.kind')='word'
                  AND s.target_language=? AND json_extract(entry_json,'$.source_language')=?
                  {archived_filter}
                  AND NOT EXISTS(SELECT 1 FROM entries e WHERE e.id=s.entry_id)
                  AND s.entry_id IN (SELECT ids.value FROM notebook_notes n, json_each(n.entry_ids) ids
                                     WHERE n.language=?)
            ORDER BY id
        """, [*params, language.target, language.source, language.target, language.source, language.target])

    @classmethod
    def _word_occurrences(cls, db, texts, language):
        requested = {text: word_key(text, language.target) for text in texts}
        result = {key: [] for key in requested.values()}
        for row in cls._word_rows(db, language, result):
            result[word_key(row["text"], language.target)].append(row)
        return {text: result[key] for text, key in requested.items()}

    def playback_word_states(self, media_path, language):
        """Index saved words of this pair and pending occurrences in this video."""
        states = {}
        if not media_path:
            return states
        media = canonical_path(media_path)
        with self.store._connection() as db:
            for row in self._word_rows(db, language):
                key = word_key(row["text"], language.target)
                if row["organized_at"] or row["cited"]:
                    states[None, key] = "organized"
                else:
                    states.setdefault((None, key), "pending")
                if row["media_path"] == media and not row["archived"] and not row["organized_at"]:
                    states[row["cue_id"], key] = "pending"
        return states

    @staticmethod
    def _word_state(occurrences, media, cue_id):
        current = min((r for r in occurrences if r["media_path"] == media and r["cue_id"] == cue_id),
                      key=lambda r: (r["archived"], bool(r["organized_at"]), r["id"]), default=None)
        if current is not None and not current["organized_at"] and not current["archived"]:
            return "pending", current
        if any(r["organized_at"] or r["cited"] for r in occurrences):
            return "organized", current
        return ("pending" if occurrences else "unsaved"), current

    def word_collection_states(self, media_path, cue_id, texts, language):
        media = canonical_path(media_path)
        texts = tuple(dict.fromkeys(texts))
        if not texts:
            return {}
        with self.store._connection() as db:
            occurrences = self._word_occurrences(db, texts, language)
        return {text: self._word_state(rows, media, cue_id)[0] for text, rows in occurrences.items()}

    def collect_word(self, media_path, cue, token, language, *, confirm=False):
        """Collect one real occurrence or explicitly requeue a previously organized word."""
        if not media_path or not token.text.strip() or token.text not in cue.text:
            raise ValueError("收藏的词必须来自当前字幕。")
        media = canonical_path(media_path)
        with _LOCK, self._book_transaction(language.target) as (db, write):
            occurrences = self._word_occurrences(db, [token.text], language)[token.text]
            state, current = self._word_state(occurrences, media, cue.id)
            if state == "pending" and current is not None:
                return "pending"
            if state == "organized" and not confirm:
                return "confirm"
            if current is None:
                entry_id = self.store._save_entry(db, "word", media, cue, language, token.text,
                                                  token.reading, token.meaning, note=None)
            else:
                entry_id = current["id"]
                if current["archived"]:
                    # Restore the same ID and original wording so existing links and annotations survive.
                    archived = self._source_row(db, entry_id)
                    db.execute("""INSERT INTO entries(id,kind,media_path,cue_id,start,text,reading,meaning,
                                  note,sentence,source_language,target_language,created_at)
                                  VALUES (:id,:kind,:media_path,:cue_id,:start,:text,:reading,:meaning,
                                  :note,:sentence,:source_language,:target_language,:created_at)""", archived)
            if state != "organized":
                return "pending_source" if occurrences else "collected"
            db.execute("UPDATE notebook_collections SET organized_at='' WHERE entry_id=?", (entry_id,))
            self._add_word_reference(db, language, occurrences, entry_id, write)
            return "referenced"

    @staticmethod
    def _source_row(db, entry_id):
        row = db.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
        if row:
            return dict(row)
        return json.loads(db.execute("SELECT entry_json FROM notebook_sources WHERE entry_id=?", (entry_id,)).fetchone()[0])

    def _migrate_legacy(self):
        # Migration indexes existing content without rewriting a single Markdown byte.
        with _LOCK, self.store._connection(immediate=True) as db:
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
                now = utc_timestamp()
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
        value = {field: entry[field] for field in _SOURCE_FIELDS}
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
        with _LOCK, self.database.session():
            path = self._path(language)
            markdown = path.read_text(encoding="utf-8") if path.exists() else ""
            entries = self._entries(language)
            with self.store._connection() as db:
                notes = [self._view_note(row, markdown) for row in db.execute(
                    "SELECT * FROM notebook_notes WHERE language=? ORDER BY created_at, rowid, id", (language,))]
                languages = {r[0] for r in db.execute("SELECT language FROM notebook_v5_migrations")}
                counts = dict(db.execute("SELECT target_language,count(*) FROM entries GROUP BY target_language"))
                linked = {i for n in notes for i in n["entry_ids"]}
                sources = {e["id"]: {**{k: e[k] for k in _SOURCE_FIELDS}, "archived": False}
                           for e in entries if e["id"] in linked}
                missing = linked.difference(sources)
                if missing:
                    for row in db.execute("""SELECT entry_id,entry_json FROM notebook_sources
                                          WHERE target_language=? AND entry_id IN (SELECT value FROM json_each(?))""",
                                          (language, json.dumps(sorted(missing)))):
                        sources[row[0]] = {**json.loads(row[1]), "archived": True}
            languages.update(counts)
            languages.add(language)
            organized = sum(e["organized"] for e in entries)
            due = self._practice(language, entries)
            topics = [{"id": n["id"], "title": n["title"], "entry_ids": n["entry_ids"]} for n in notes]
            return {"language": language, "title": _title(language), "markdown": markdown,
                    "revision": _revision(markdown), "path": str(path), "topics": topics,
                    "collections": entries, "entries": entries, "notes": notes,
                    "note_sources": [sources[i] for i in sorted(sources)],
                    "practice": due,
                    "books": [{"language": lang, "title": _title(lang), "count": counts.get(lang, 0)}
                              for lang in sorted(languages)],
                    "stats": {"total": len(entries), "words": sum(e["kind"] == "word" for e in entries),
                              "notes": sum(e["kind"] == "note" for e in entries), "due": len(due),
                              "collections": len(entries), "organized": organized,
                              "unorganized": len(entries) - organized, "note_count": len(notes),
                              "markdown_characters": len(markdown)}}

    def sync(self, target_language):
        language = _language(target_language)
        with _LOCK, self.database.session():
            if not self._path(language).exists():
                self._write(language, f"# {_title(language)}\n\n## 我的笔记\n\n"
                            "在这里记录自己的理解和例句。收藏保留在收集箱，只有明确整理后才加入笔记。\n",
                            _revision(""))
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
        with _LOCK, self.store._connection(immediate=True) as db:
            entry = db.execute("SELECT target_language FROM entries WHERE id=?", (entry_id,)).fetchone()
            if not entry or (target_language is not None and entry[0] != target_language):
                raise ValueError("收藏不存在或不属于当前学习语言。")
            row = db.execute("SELECT entry_note FROM notebook_collections WHERE entry_id=?", (entry_id,)).fetchone()
            old = row[0] if row else ""
            if expected_revision is not None and expected_revision != _revision(old):
                raise NotebookConflict("条目备注已修改，请重新加载后保存。")
            if old and old != note:
                db.execute("INSERT INTO notebook_entry_note_history(entry_id,entry_note,revision,saved_at) "
                           "VALUES (?,?,?,?)", (entry_id, old, _revision(old), utc_timestamp()))
            db.execute("INSERT INTO notebook_collections(entry_id,entry_note,updated_at) VALUES (?,?,?) "
                       "ON CONFLICT(entry_id) DO UPDATE SET entry_note=excluded.entry_note,updated_at=excluded.updated_at",
                       (entry_id, note, utc_timestamp()))
        return next(e for e in self._entries(entry[0]) if e["id"] == entry_id)

    def _source_markdown(self, entries, *, heading=True):
        if not entries:
            return ""
        lines = ["### 原始收藏来源", ""] if heading else []
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
        return {"id": uuid.uuid4().hex, "language": language, "title": title, "body": body,
                "entry_ids": [e["id"] for e in entries], "sources_markdown": self._source_markdown(entries),
                "source_language": source_language, "origin": origin}

    def _render_note(self, note):
        start, end, sources = self._markers(note["id"])
        return f"{start}\n## {note['title']}\n\n{note['body']}\n\n{sources}\n{note['sources_markdown']}\n{end}"

    @staticmethod
    def _append_blocks(base, blocks):
        separator = "" if base.endswith("\n\n") else "\n" if base.endswith("\n") else "\n\n"
        return base + separator + blocks + "\n"

    def _add_word_reference(self, db, language, occurrences, entry_id, write):
        path = self._path(language.target)
        markdown = path.read_text(encoding="utf-8") if path.exists() else ""
        prior_ids = {row["id"] for row in occurrences}
        source = self._source_row(db, entry_id)
        same_source = {row["id"] for row in occurrences if row["media_path"] == source["media_path"]
                       and row["cue_id"] == source["cue_id"]}
        self._archive_source(db, source)
        # A topic may have been reorganized several times. Extend its most recent intact note.
        for row in db.execute("SELECT * FROM notebook_notes WHERE language=? "
                              "ORDER BY updated_at DESC, rowid DESC, id DESC", (language.target,)):
            note = self._view_note(row, markdown)
            if not prior_ids.intersection(note["entry_ids"]) or note["detached"]:
                continue
            if entry_id in note["entry_ids"] or same_source.intersection(note["entry_ids"]):
                return
            if note["origin"].startswith("legacy"):
                continue  # Keep unstructured original writing intact, using the supplement below.
            span = self._note_span(markdown, note)
            source_marker = self._markers(note["id"])[2]
            if span[2].count(source_marker) != 1:
                continue
            citation = self._source_markdown([source], heading=not note["sources_markdown"])
            end_marker = self._markers(note["id"])[1]
            insert = span[1] - len(end_marker)
            revised = self._append_blocks(markdown[:insert], citation) + markdown[insert:]
            sources = self._append_blocks(note["sources_markdown"], citation).strip()
            db.execute("UPDATE notebook_notes SET entry_ids=?,sources_markdown=?,updated_at=? WHERE id=?",
                       (json.dumps([*note["entry_ids"], entry_id]), sources, utc_timestamp(), note["id"]))
            write(revised, _revision(markdown))
            return
        # Notes may have been deleted, detached by the Markdown editor, or migrated without markers.
        # Keep those bytes untouched and give the learner a linked supplement containing every real source.
        entries = [self._source_row(db, i) for i in sorted(prior_ids | {entry_id})]
        note = self._make_note(language.target, source["text"], "再次收藏的原句出处。", entries, language.source)
        base = markdown or f"# {_title(language.target)}\n"
        self._store_note(db, note)
        for entry in entries:
            self._archive_source(db, entry)
        db.execute("INSERT OR IGNORE INTO notebook_v5_migrations VALUES (?)", (language.target,))
        write(self._append_blocks(base, self._render_note(note)), _revision(markdown))

    @staticmethod
    def _store_note(db, note):
        now = utc_timestamp()
        db.execute("INSERT INTO notebook_notes VALUES (?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                   "title=excluded.title,body=excluded.body,entry_ids=excluded.entry_ids,"
                   "sources_markdown=excluded.sources_markdown,origin=excluded.origin,updated_at=excluded.updated_at",
                   (note["id"], note["language"], note["title"], note["body"], json.dumps(note["entry_ids"]),
                    note["sources_markdown"], note["source_language"], note["origin"],
                    note.get("created_at", now), now))
        return now

    @contextmanager
    def _book_transaction(self, language):
        """Serialize collection checks with writes and restore Markdown if the SQLite commit fails."""
        written = None

        def write(text, expected_revision):
            nonlocal written
            path = self._path(language)
            old = path.read_text(encoding="utf-8") if path.exists() else None
            self._write(language, text, expected_revision)
            written = (path, old, _revision(text))

        try:
            with self.store._connection(immediate=True) as db:
                yield db, write
        except Exception:
            if written:
                path, old, revision = written
                if path.exists() and _revision(path.read_text(encoding="utf-8")) == revision:
                    if old is None:
                        path.unlink()
                    else:
                        _atomic_text(path, old)
            raise

    def _commit(self, original, markdown, notes=(), delete_id=None, check_entries=True, cancelled=None,
                organize_ids=None):
        language = original["language"]
        with self._book_transaction(language) as (db, write):
            if cancelled and cancelled():
                raise ValueError("笔记操作已取消，原笔记未修改。")
            if check_entries and self._entries(language) != original["collections"]:
                raise NotebookConflict("操作期间收藏或条目备注已变更，请重试。")
            if delete_id is not None:
                db.execute("DELETE FROM notebook_notes WHERE id=? AND language=?", (delete_id, language))
            for note in notes:
                updated_at = self._store_note(db, note)
                for entry_id in note["entry_ids"]:
                    source = next((e for e in original["collections"] if e["id"] == entry_id), None)
                    if source:
                        self._archive_source(db, source)
                    if organize_ids is None or entry_id in organize_ids:
                        db.execute("INSERT INTO notebook_collections(entry_id,organized_at) VALUES (?,?) "
                                   "ON CONFLICT(entry_id) DO UPDATE SET organized_at=CASE WHEN organized_at='' "
                                   "THEN excluded.organized_at ELSE organized_at END", (entry_id, updated_at))
            db.execute("INSERT OR IGNORE INTO notebook_v5_migrations VALUES (?)", (language,))
            write(markdown, original["revision"])
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
            return self._commit(original, text, [updated], organize_ids=set())

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
                from .llm_client import completion_port
                raw = completion_port(agent).complete(ORGANIZE_PROMPT, payload, ORGANIZE_SCHEMA)
                try:
                    current = decode_topics(raw, batch)
                except (ValueError, TypeError, KeyError):
                    raise ValueError("模型学习笔记缺少有效学习内容或含重复、缺失、虚构来源；原笔记未修改。") from None
            topics.extend(current)
        if cancelled and cancelled():
            raise ValueError("笔记整理已取消，原笔记未修改。")
        notes = []
        by_id = {e["id"]: e for e in original["collections"]}
        for topic in topics:
            selected = self._selected(entries, topic["entry_ids"])
            body = render_topic_body(topic, selected, source_language, language)
            sources = {e["id"]: e for e in selected}
            for entry in selected:
                for ref in entry["word_sources"]:
                    source_id = ref["id"]
                    if source_id not in sources:
                        sources[source_id] = by_id.get(source_id) or self.source_entry(source_id, target_language=language)
            notes.append(self._make_note(language, topic["title"], body, list(sources.values()), source_language,
                                         "local" if getattr(agent, "provider", None) == "local" else "agent"))
        with _LOCK:
            base = original["markdown"] or f"# {_title(language)}\n"
            text = self._append_blocks(base, "\n\n".join(self._render_note(n) for n in notes))
            return self._commit(original, text, notes, cancelled=cancelled, organize_ids={e["id"] for e in entries})

    def review(self, entry_id, rating):
        if type(entry_id) is not int or rating not in ("again", "hard", "good", "easy"):
            raise ValueError("复习评分无效。")
        now = datetime.now(timezone.utc)
        with self.store._connection(immediate=True) as db:
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
        with self.database.session():
            return self._practice(language, self._entries(language))

    def _practice(self, language, entries):
        with self.store._connection() as db:
            reviews = {r["entry_id"]: dict(r) for r in db.execute(
                "SELECT r.* FROM entries e JOIN notebook_reviews r ON r.entry_id=e.id "
                "WHERE e.target_language=?", (language,))}
        now = datetime.now(timezone.utc).isoformat()
        cards = []
        for entry in entries:
            review = reviews.get(entry["id"], {})
            if review.get("due_at", "") > now:
                continue
            cards.append({**entry, "entry_id": entry["id"], "question": f"回忆这个表达的读音和用法：{entry['text']}",
                          "answer": "\n".join(str(entry[k]) for k in ("reading", "meaning", "sentence", "note", "entry_note")
                                              if entry.get(k)), "source_url": f"tingmu://entry/{entry['id']}",
                          "due_at": review.get("due_at", ""), "repetitions": review.get("repetitions", 0)})
        return sorted(cards, key=lambda card: (card["due_at"], card["id"]))
