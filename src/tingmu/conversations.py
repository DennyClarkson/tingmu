"""Independent durable chat sessions, with non-destructive legacy migration."""

import json
import math
import uuid

from .storage import canonical_path
from .timestamps import utc_timestamp

_PRIVATE_FIELDS = {
    "signature", "thinking_signature", "encrypted_content", "encrypted", "redacted_thinking",
    "private", "private_content", "private_thinking", "thinking", "reasoning", "reasoning_content",
    "reasoning_text", "reasoning_summary",
}
_PRIVATE_BLOCK_TYPES = {"thinking", "redacted_thinking", "reasoning", "encrypted", "encrypted_content"}


def _text(value, name, maximum=1000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum or "\x00" in value:
        raise ValueError(f"无效的{name}。")
    return value


def _metadata(value, depth=0):
    """Persist safe request context/results, never private transport credentials."""
    from .diagnostics import redact_text
    if depth > 10:
        raise ValueError("对话操作记录层级过深。")
    if isinstance(value, dict):
        block_type = value.get("type")
        if isinstance(block_type, str) and block_type.lower() in _PRIVATE_BLOCK_TYPES:
            return {}
        forbidden = {"key", "api_key", "apikey", "authorization", "password", "secret", "access_token"} | _PRIVATE_FIELDS
        return {str(k): _metadata(v, depth + 1) for k, v in value.items()
                if not str(k).startswith("_") and str(k).lower() not in forbidden}
    if isinstance(value, list):
        return [_metadata(v, depth + 1) for v in value[:200]]
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int)) or (isinstance(value, float) and math.isfinite(value)):
        return value
    raise ValueError("对话操作记录格式无效。")


def _public_reasoning(message):
    """Only an adapter-selected public text projection may reach durable history.

    Native signed/encrypted blocks are deliberately not accepted by this API.
    The length limit applies to the complete accumulated text of each message.
    """
    from .diagnostics import redact_text
    result = {}
    if "reasoning_text" in message:
        text = message["reasoning_text"]
        if not isinstance(text, str) or len(text) > 60000 or "\x00" in text:
            raise ValueError("公开思考文本无效或超过 60,000 字符。")
        text = redact_text(text)
        if len(text) > 60000:
            raise ValueError("公开思考文本超过 60,000 字符。")
        result["reasoning_text"] = text
    for field, allowed in (("reasoning_kind", {"reasoning", "summary"}),
                           ("phase", {"connecting", "thinking", "acting", "answering"})):
        if field in message:
            value = message[field]
            if not isinstance(value, str) or value not in allowed:
                raise ValueError("公开思考显示状态无效。")
            result[field] = value
    if "reasoning_elapsed_ms" in message:
        value = message["reasoning_elapsed_ms"]
        try:
            valid = type(value) in (int, float) and value >= 0 and math.isfinite(value)
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError("公开思考耗时无效。")
        result["reasoning_elapsed_ms"] = value
    return result


def _messages(messages):
    if not isinstance(messages, list) or len(messages) > 10000:
        raise ValueError("对话消息格式无效。")
    result, ids = [], set()
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in ("user", "assistant"):
            raise ValueError("对话消息角色无效。")
        content = message.get("content")
        if not isinstance(content, str) or len(content) > 1000000 or "\x00" in content:
            raise ValueError("对话消息内容无效。")
        row = {"id": message.get("id") or uuid.uuid4().hex, "role": message["role"],
               "content": content, "status": message.get("status", "completed"),
               "model": message.get("model", ""), "created_at": message.get("created_at") or utc_timestamp()}
        for key in ("id", "status", "created_at"):
            _text(row[key], key)
        if not isinstance(row["model"], str) or len(row["model"]) > 1000:
            raise ValueError("对话模型信息无效。")
        if row["id"] in ids:
            raise ValueError("对话消息 ID 重复。")
        row.update(_public_reasoning(message))
        for key in ("reasoning_effort", "request_context", "tool_events", "error", "request_model"):
            if key in message:
                extra = _metadata(message[key])
                if len(json.dumps(extra, ensure_ascii=False, allow_nan=False)) > 256000:
                    raise ValueError("对话操作记录过大。")
                row[key] = extra
        ids.add(row["id"])
        result.append(row)
    return result


class ConversationStore:
    def __init__(self, store):
        self.store = store
        self.database = store.database

    def list_sessions(self, target_language):
        with self.store._connection() as db:
            rows = db.execute("SELECT id,title,media_path,source_language,target_language,created_at,updated_at "
                              "FROM chat_sessions WHERE target_language=? ORDER BY updated_at DESC,rowid DESC,id DESC",
                              (target_language,)).fetchall()
        return [dict(row) for row in rows]

    def create(self, language, media_path="", title="新对话"):
        _text(title, "标题", 200)
        _text(language.source, "讲解语言/方言", 80)
        _text(language.target, "学习语言/方言", 80)
        if not isinstance(media_path, str) or "\x00" in media_path:
            raise ValueError("视频路径无效。")
        session_id = uuid.uuid4().hex
        with self.store._connection(immediate=True) as db:
            now = utc_timestamp()
            db.execute("INSERT INTO chat_sessions VALUES (?,?,?,?,?,?,?,?)",
                       (session_id, title, canonical_path(media_path) if media_path else "",
                        language.source, language.target, "[]", now, now))
        return self.load(session_id)

    def load(self, session_id):
        with self.store._connection() as db:
            row = db.execute("SELECT * FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            raise ValueError("对话不存在。")
        result = dict(row)
        result["messages"] = json.loads(result["messages"])
        return result

    def save(self, session_id, messages, title=None, *, record_legacy_migration=False):
        clean = _messages(messages)
        if title is not None:
            _text(title, "标题", 200)
        with self.store._connection(immediate=True) as db:
            row = db.execute("UPDATE chat_sessions SET messages=?,title=COALESCE(?,title),updated_at=? "
                             "WHERE id=? RETURNING id,title,media_path,source_language,target_language,"
                             "created_at,updated_at",
                             (json.dumps(clean, ensure_ascii=False), title, utc_timestamp(), session_id)).fetchone()
            if row is None:
                raise ValueError("对话不存在。")
            session = {**dict(row), "messages": clean}
            if record_legacy_migration:
                self._record_legacy(db, session)
        return session

    @staticmethod
    def _record_legacy(db, session):
        path, source, target = (session[key] for key in ("media_path", "source_language", "target_language"))
        # Project the validated canonical JSON in place. json_each traverses the
        # message array in order, including an empty array when clearing chat.
        db.execute("""
            INSERT INTO conversations(media_path,source_language,target_language,messages,updated_at)
            SELECT media_path,source_language,target_language,
                (SELECT json_group_array(json_object('role',m.value->>'$.role','content',m.value->>'$.content'))
                 FROM json_each(chat_sessions.messages) m), updated_at
            FROM chat_sessions WHERE id=?
            ON CONFLICT(media_path,source_language,target_language) DO UPDATE SET
                messages=excluded.messages,updated_at=excluded.updated_at
        """, (session["id"],))
        db.execute("INSERT OR IGNORE INTO chat_legacy_migrations VALUES (?,?,?,?)",
                   (path, source, target, session["id"]))

    def save_with_legacy(self, session_id, messages):
        """Atomically save both views while retaining the positional save hook."""
        with self.store._connection(immediate=True) as db:
            result = self.save(session_id, messages)
            self._record_legacy(db, result)
            return result

    def rename(self, session_id, title):
        _text(title, "标题", 200)
        with self.store._connection(immediate=True) as db:
            cursor = db.execute("UPDATE chat_sessions SET title=?,updated_at=? WHERE id=?",
                                (title, utc_timestamp(), session_id))
            if not cursor.rowcount:
                raise ValueError("对话不存在。")
        return self.load(session_id)

    def delete(self, session_id):
        with self.store._connection() as db:
            db.execute("DELETE FROM chat_sessions WHERE id=?", (session_id,))
        # Keep the migration tombstone: deleting a migrated session must not resurrect it.

    def migrate_legacy(self, media_path, language):
        path = canonical_path(media_path) if media_path else ""
        identity = (path, language.source, language.target)
        with self.store._connection(immediate=True) as db:
            prior = db.execute("SELECT session_id FROM chat_legacy_migrations WHERE media_path=? "
                               "AND source_language=? AND target_language=?", identity).fetchone()
            if prior:
                row = db.execute("SELECT * FROM chat_sessions WHERE id=?", (prior[0],)).fetchone()
                if not row:
                    return None
                result = dict(row)
                result["messages"] = json.loads(result["messages"])
                return result
            old = db.execute("SELECT messages FROM conversations WHERE media_path=? AND source_language=? "
                             "AND target_language=?", identity).fetchone()
            if not old:
                return None
            clean = _messages(json.loads(old[0]))
            session_id, now = uuid.uuid4().hex, utc_timestamp()
            db.execute("INSERT INTO chat_sessions VALUES (?,?,?,?,?,?,?,?)",
                       (session_id, "导入的对话", *identity, json.dumps(clean, ensure_ascii=False), now, now))
            db.execute("INSERT INTO chat_legacy_migrations VALUES (?,?,?,?)", (*identity, session_id))
        return self.load(session_id)
