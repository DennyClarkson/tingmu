"""Workspace drafts, independent of bounded window-layout preferences."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3

from .database import Database


DRAFT_KINDS = frozenset({"notebookDrafts", "noteDrafts"})
_LAYOUT_MIGRATION = "quick-layout-v1"


class DraftStorage:
    """Persist complete draft values in the workspace database, without text limits.

    The legacy layout snapshot remains in the migration table for recovery. Neither
    draft identities nor content are interpreted as file paths during normal use.
    """

    def __init__(self, source):
        if isinstance(source, Database):
            self.database = source
            self._owns_database = False
        elif isinstance(source, (str, Path)):
            self.database = Database(source)
            self._owns_database = True
        elif isinstance(getattr(source, "database", None), Database):
            self.database = source.database
            self._owns_database = False
        else:
            raise TypeError("DraftStorage requires a database path, Store, or Database")
        self.db_path = self.database.path

    def _connection(self, immediate=False):
        return self.database.connection(immediate=immediate)

    def close(self):
        if self._owns_database:
            self.database.close()

    @staticmethod
    def _validate(kind: str, key: str | None = None):
        if kind not in DRAFT_KINDS:
            raise ValueError("Unknown draft kind")
        if key is not None and (not isinstance(key, str) or not key):
            raise ValueError("Draft identity must be a nonempty string")

    def load(self, kind: str) -> dict:
        self._validate(kind)
        with self._connection() as db:
            return {key: json.loads(value) for key, value in
                    db.execute("SELECT key,value FROM ui_drafts WHERE kind=?", (kind,))}

    def write(self, kind: str, key: str, value) -> None:
        self._validate(kind, key)
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with self._connection() as db:
            db.execute("INSERT INTO ui_drafts(kind,key,value) VALUES(?,?,?) "
                       "ON CONFLICT(kind,key) DO UPDATE SET value=excluded.value",
                       (kind, key, encoded))

    def delete(self, kind: str, key: str) -> None:
        self._validate(kind, key)
        with self._connection() as db:
            db.execute("DELETE FROM ui_drafts WHERE kind=? AND key=?", (kind, key))

    def migrate_layout(self, layout: dict) -> dict:
        """Import legacy maps once, then return a layout with those maps removed.

        The caller replaces its layout only after this succeeds. A failed import
        rolls back all draft changes and never mutates the supplied layout. A
        crash before the caller saves is safe: the marker prevents stale layout
        text from resurrecting deleted drafts or replacing subsequent edits.
        """
        result = deepcopy(layout)
        preferences = result.get("preferences", {})
        if not isinstance(preferences, dict):
            return result
        legacy = {kind: preferences[kind] for kind in DRAFT_KINDS if kind in preferences}
        if not legacy:
            return result
        with self._connection(immediate=True) as db:
            migrated = db.execute("SELECT 1 FROM ui_draft_migrations WHERE name=?",
                                  (_LAYOUT_MIGRATION,)).fetchone()
            if not migrated:
                for kind, values in legacy.items():
                    if not isinstance(values, dict):
                        raise ValueError("旧草稿格式无效；布局和草稿均保留")
                    for key, value in values.items():
                        self._validate(kind, key)
                        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
                        existing = db.execute("SELECT value FROM ui_drafts WHERE kind=? AND key=?",
                                              (kind, key)).fetchone()
                        if existing and json.loads(existing[0]) != value:
                            raise ValueError("旧草稿与现有草稿冲突；布局和草稿均保留")
                        db.execute("INSERT OR IGNORE INTO ui_drafts VALUES(?,?,?)", (kind, key, encoded))
                db.execute("INSERT INTO ui_draft_migrations VALUES(?,?)",
                           (_LAYOUT_MIGRATION, json.dumps(legacy, ensure_ascii=False, allow_nan=False)))
        for kind in legacy:
            preferences.pop(kind)
        return result


def remap_draft_media(db: sqlite3.Connection, remap) -> None:
    """Remap only note identity prefixes inside the caller's migration transaction."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ui_drafts'").fetchone():
        return
    rows = db.execute("SELECT key,value FROM ui_drafts WHERE kind='noteDrafts'").fetchall()
    rewritten = {}
    for key, value in rows:
        media, separator, suffix = key.partition("|")
        target = remap(media) + separator + suffix
        if target in rewritten and json.loads(rewritten[target]) != json.loads(value):
            raise ValueError("便携迁移发现未保存笔记冲突；原草稿保留")
        rewritten[target] = value
    if dict(rows) != rewritten:
        db.execute("DELETE FROM ui_drafts WHERE kind='noteDrafts'")
        db.executemany("INSERT INTO ui_drafts VALUES('noteDrafts',?,?)", rewritten.items())
