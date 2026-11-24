"""Versioned workspace schema and bounded, thread-owned SQLite leases.

No handle survives an outer connection operation unless an explicit session is
active. This keeps idle portable folders movable on Windows. Nested operations
share the thread's handle but isolate rollback using savepoints.
"""

from contextlib import closing, contextmanager
from pathlib import Path
import sqlite3
import threading
import time
import unicodedata

from filelock import FileLock

SCHEMA_VERSION = 2

_V2_INDEXES = (
    "CREATE INDEX IF NOT EXISTS entries_target_id ON entries(target_language,id)",
    "CREATE INDEX IF NOT EXISTS entries_media_pair ON entries(media_path,source_language,target_language)",
    "CREATE INDEX IF NOT EXISTS notebook_notes_created ON notebook_notes(language,created_at,id)",
    "CREATE INDEX IF NOT EXISTS notebook_notes_updated ON notebook_notes(language,updated_at,id)",
    "CREATE INDEX IF NOT EXISTS chat_sessions_updated ON chat_sessions(target_language,updated_at DESC,id)",
    "CREATE INDEX IF NOT EXISTS notebook_sources_target ON notebook_sources(target_language,entry_id)",
)


def word_key(text: str, target_language: str) -> str:
    """Match sentence-initial capitalization while keeping accents, acronyms and mixed case."""
    word = unicodedata.normalize("NFC", text)
    if not word or not word[0].isupper() or any(c.isupper() for c in word[1:]):
        return word
    first = word[0]
    if target_language.lower().replace("_", "-").split("-")[0] in {"tr", "az"}:
        first = {"I": "ı", "İ": "i"}.get(first, first)
    return unicodedata.normalize("NFC", first.lower() + word[1:])


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('word','note')),
    media_path TEXT NOT NULL, cue_id TEXT NOT NULL,
    start REAL NOT NULL, text TEXT NOT NULL, reading TEXT NOT NULL DEFAULT '',
    meaning TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
    sentence TEXT NOT NULL, source_language TEXT NOT NULL,
    target_language TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(kind, media_path, cue_id, text, source_language, target_language)
);
CREATE TABLE IF NOT EXISTS positions (
    media_path TEXT PRIMARY KEY, milliseconds INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    media_path TEXT NOT NULL, source_language TEXT NOT NULL,
    target_language TEXT NOT NULL, messages TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(media_path, source_language, target_language)
);


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
CREATE INDEX IF NOT EXISTS entries_word_lookup ON entries(source_language,target_language,text)
    WHERE kind='word';
CREATE INDEX IF NOT EXISTS notebook_archived_word_lookup ON notebook_sources(
    target_language, json_extract(entry_json,'$.source_language'), json_extract(entry_json,'$.text'))
    WHERE json_extract(entry_json,'$.kind')='word';


CREATE TABLE IF NOT EXISTS chat_sessions (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, media_path TEXT NOT NULL,
    source_language TEXT NOT NULL, target_language TEXT NOT NULL,
    messages TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_legacy_migrations (
    media_path TEXT NOT NULL, source_language TEXT NOT NULL,
    target_language TEXT NOT NULL, session_id TEXT NOT NULL,
    PRIMARY KEY(media_path,source_language,target_language)
);


CREATE TABLE IF NOT EXISTS ui_drafts (
    kind TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
    PRIMARY KEY(kind, key)
);
CREATE TABLE IF NOT EXISTS ui_draft_migrations (
    name TEXT PRIMARY KEY, original TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS word_forms(target_language TEXT NOT NULL, text TEXT NOT NULL,
 word_key TEXT NOT NULL, PRIMARY KEY(target_language,text));
CREATE INDEX IF NOT EXISTS word_forms_key ON word_forms(target_language,word_key,text);"""


def _migrate_v1(db):
    # These owned DDL statements contain no semicolons inside SQL literals.
    # executescript is deliberately avoided: it would commit before migration.
    for statement in _SCHEMA_SQL.split(";"):
        if statement.strip():
            db.execute(statement)
    forms = db.execute("SELECT DISTINCT target_language,text FROM entries WHERE kind='word'").fetchall()
    forms += db.execute("""SELECT DISTINCT target_language,json_extract(entry_json,'$.text')
                           FROM notebook_sources WHERE json_extract(entry_json,'$.kind')='word'""").fetchall()
    db.executemany(
        "INSERT OR IGNORE INTO word_forms VALUES (?,?,?)",
        ((language, text, word_key(text, language)) for language, text in forms),
    )


class Database:
    def __init__(self, path, *, max_connections=8):
        if type(max_connections) is not int or max_connections < 1:
            raise ValueError("max_connections must be a positive integer")
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_connections = max_connections
        self._condition = threading.Condition()
        self._local = threading.local()
        self._active = 0
        self._closed = False
        self._initialize()

    def _initialize(self):
        # SQLite's WAL-mode transition can fail immediately under simultaneous
        # first opens. Serialize schema setup across threads and app processes.
        with (
            FileLock(str(self.path) + ".schema.lock", timeout=25),
            closing(sqlite3.connect(self.path, timeout=25)) as db,
        ):
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError("Workspace database was created by a newer application")
            if version == SCHEMA_VERSION:
                return
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            try:
                # Another Store/process may have upgraded while this one waited.
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise RuntimeError("Workspace database was created by a newer application")
                if version == 0:
                    _migrate_v1(db)
                if version < 2:
                    for statement in _V2_INDEXES:
                        db.execute(statement)
                    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @property
    def closed(self):
        with self._condition:
            return self._closed

    def _acquire(self):
        db = getattr(self._local, "db", None)
        if db is not None:
            return db
        deadline = time.monotonic() + 25
        with self._condition:
            while self._active >= self.max_connections and not self._closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for a workspace database connection")
                self._condition.wait(remaining)
            if self._closed:
                raise RuntimeError("Workspace database is closed")
            self._active += 1
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=25)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA synchronous=NORMAL")
            db.execute("PRAGMA temp_store=MEMORY")
            db.execute("PRAGMA cache_size=-16000")
        except BaseException:
            if db is not None:
                db.close()
            with self._condition:
                self._active -= 1
                self._condition.notify_all()
            raise
        self._local.db = db
        return db

    def _release(self):
        db = getattr(self._local, "db", None)
        if db is None:
            return
        try:
            db.close()
        finally:
            self._local.db = None
            with self._condition:
                self._active -= 1
                self._condition.notify_all()

    @contextmanager
    def connection(self, *, immediate=False):
        db = self._acquire()
        depth = getattr(self._local, "depth", 0)
        savepoint = f"tingmu_nested_{depth}"
        self._local.depth = depth + 1
        try:
            db.execute(f"SAVEPOINT {savepoint}" if depth else "BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield db
            except BaseException:
                if depth:
                    db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    db.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    db.rollback()
                raise
            else:
                if depth:
                    db.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    try:
                        db.commit()
                    except BaseException:
                        db.rollback()
                        raise
        finally:
            self._local.depth = depth
            if not depth:
                db.set_trace_callback(None)
                if not getattr(self._local, "sessions", 0):
                    self._release()

    @contextmanager
    def session(self):
        """Reuse one thread-owned handle across operations, not their transactions."""
        if self.closed:
            raise RuntimeError("Workspace database is closed")
        self._local.sessions = getattr(self._local, "sessions", 0) + 1
        try:
            yield self
        finally:
            self._local.sessions -= 1
            if not self._local.sessions and not getattr(self._local, "depth", 0):
                self._release()

    def close(self, timeout=25):
        """Stop new leases and wait for active owners to release their own handles."""
        if getattr(self._local, "db", None) is not None:
            raise RuntimeError("Cannot close a database from inside its active connection/session")
        deadline = time.monotonic() + timeout
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            while self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Database connections are still active")
                self._condition.wait(remaining)
