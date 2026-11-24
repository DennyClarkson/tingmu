"""Typed, application-only agent actions bound to an immutable request context."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import uuid
from types import MappingProxyType

from .models import Token
from .providers import RequestCancelled


class ActionError(ValueError):
    def __init__(self, message, code="action_failed"):
        super().__init__(message)
        self.code = code


def string(maximum=6000, enum=None):
    result = {"type": "string", "maxLength": maximum}
    if enum is not None:
        result["enum"] = enum
    return result


def ids(kind="integer"):
    return {"type": "array", "items": {"type": kind}, "maxItems": 100, "uniqueItems": True}


# Descriptions state outcome semantics so an asynchronous task is never called done.
_DEFINITIONS = [
    ("get_app_context", "读取应用状态", "Read safe current playback, selected media, queue, settings and saved model choices. No credentials or full paths.", {}, []),
    ("process_videos", "处理视频", "Prepare the current, selected or queued videos. Omit workflow to use the user's current choice; select subtitle_agent when asked to inspect supplied/embedded captions and reuse originals/translations. It always requires teaching. mode=prepare resumes. Use teaching or all only for explicitly requested regeneration; they back up and clear teaching or all materials respectively. Report job IDs and actual progress; accepted/running is not completed.",
     {"scope": string(enum=["current", "selected", "queue"]), "mode": string(enum=["prepare", "teaching", "all"]),
      "workflow": string(enum=["classic", "subtitle_agent"]), "start": {"type": "boolean"}}, ["scope", "mode"]),
    ("manage_queue", "管理处理队列", "Read queue status, start pending jobs, pause processing, or retry named failed jobs. An empty job_ids list retries all failed jobs in the bound language.", {"action": string(enum=["status", "start", "pause", "retry"]), "job_ids": ids("string")}, ["action"]),
    ("control_playback", "控制播放", "Control the request-bound video. Seek value is seconds, speed 0.25–3, volume 0–1. Rejects if current video changed since the request.", {"action": string(enum=["play", "pause", "seek", "replay", "previous_sentence", "next_sentence", "speed", "volume", "clear_loop"]), "value": {"type": "number", "minimum": 0}}, ["action"]),
    ("set_teaching_preferences", "保存讲解偏好", "Persist desired explanation detail, examples and known/target language comparisons for FUTURE materials. Never reprocess existing materials unless the user separately requests process_videos mode=teaching.", {"preferences": string(6000)}, ["preferences"]),
    ("set_reasoning_effort", "设置后续对话思考强度", "Change reasoning effort for future requests only. This response retains its frozen effort. default uses the model connection default; provider may reject unsupported choices.", {"effort": string(enum=["default", "none", "minimal", "low", "medium", "high", "xhigh"])}, ["effort"]),
    ("select_model", "选择模型", "Select an existing public model profile for a global role shared by all study workspaces. Empty profile_id restores defaults. Never creates credentials or changes the in-flight reply model.", {"role": string(enum=["annotation", "review", "companion"]), "profile_id": string(100)}, ["role", "profile_id"]),
    ("set_appearance", "调整主题", "Set the application's persistent color theme.", {"theme": string(enum=["forest", "paper", "mist", "graphite"])}, ["theme"]),
    ("set_player_preferences", "调整播放器显示", "Update only specified display settings; no media processing.", {"subtitle_offset_ms": {"type": "integer", "minimum": -60000, "maximum": 60000}, "subtitle_gap_ms": {"type": "integer", "minimum": 0, "maximum": 1500}, "overlay_opacity": {"type": "number", "minimum": .15, "maximum": 1}, "overlay_font_size": {"type": "integer", "minimum": 14, "maximum": 48}}, []),
    ("open_workspace", "切换视图或模块", "Open a supported VIEW or module, not a study data workspace. Use use_study_workspace to change study folders. Does not open arbitrary apps or URLs.", {"view": string(enum=["watch", "prepare", "learn"]), "module": string(enum=["chat", "player", "companion", "subtitles", "library", "queue"])}, []),
    ("list_collections", "读取收藏箱", "Read collected words/sentences for the request-bound studied language, including organized state and personal-note revisions.", {"organized": string(enum=["all", "yes", "no"]), "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}, "query": string(200)}, []),
    ("read_notebook", "读取学习笔记", "Read a page of notebook Markdown and revision plus note IDs. Use offset to read more; read the complete notebook before replacing its Markdown.", {"offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 100, "maximum": 18000}}, []),
    ("collect_current_word", "收藏当前词语", "Collect an exact substring of the current subtitle. Report duplicates. If confirmation_required, ask whether to collect it again with this source. Set confirm_recollection only when the user agrees in the following message.", {"text": string(200), "confirm_recollection": {"type": "boolean"}}, ["text"]),
    ("collect_current_sentence", "收藏当前句", "Add the sentence to the 收藏箱 ONLY when explicitly requested as a bookmark. Saving the corrected subtitle and its explanation is exclusively revise_current_subtitle. It never implies a bookmark.", {"note": string(4000)}, []),
    ("organize_notebook", "整理收藏到笔记", "Explicitly organize unorganized collections (or provided entry IDs) into useful study notes. Preserve collections and mark organized. instructions can request native/target comparisons, detailed grammar or personal goals.", {"instructions": string(6000), "entry_ids": ids()}, []),
    ("append_learning_note", "新增学习笔记", "Append a substantive personal/tutor note, preserving existing notes. Only claim saved after the result succeeds.", {"text": string(20000), "title": string(100), "entry_ids": ids()}, ["text"]),
    ("save_notebook", "保存笔记修改", "Replace notebook Markdown only after reading ALL of it, with the exact observed revision. Preserve source references and manual writing unless the user explicitly requests a specific change.", {"markdown": string(60000), "revision": string(100)}, ["markdown", "revision"]),
    ("update_learning_note", "修改学习笔记", "Edit a named note using the observed notebook revision. Read the COMPLETE notebook first, including this note body. Preserve unrelated notes and source associations.", {"note_id": string(100), "title": string(100), "text": string(20000), "revision": string(100)}, ["note_id", "title", "text", "revision"]),
    ("update_collection", "保存收藏个性化备注", "Update ONLY a collected entry's personal note, preserving original text/source. Supports known/target-language comparisons and personal learning goals. Requires observed note_revision.", {"entry_id": {"type": "integer", "minimum": 1}, "note": string(6000), "revision": string(100)}, ["entry_id", "note", "revision"]),
    ("delete_collection", "删除指定收藏", "Delete one collection ONLY on explicit user request, after listing its ID. A local backup is made. This does not delete video or notebook prose.", {"entry_id": {"type": "integer", "minimum": 1}}, ["entry_id"]),
    ("delete_learning_note", "删除指定笔记", "Delete a note ONLY on explicit user request, after reading the COMPLETE notebook using its observed revision. Keeps original collections and other notes; notebook service retains backup.", {"note_id": string(100), "revision": string(100)}, ["note_id", "revision"]),
    ("practice_words", "读取复习材料", "Read due collected words and source sentences for practice; does not invent saved content.", {}, []),
    ("review_collection", "记录复习结果", "Record the user's explicit review rating for a collected entry in this language.", {"entry_id": {"type": "integer", "minimum": 1}, "rating": string(enum=["again", "hard", "good", "easy"])}, ["entry_id", "rating"]),
    ("read_current_subtitle", "读取待修订字幕", "Read the request-bound display segment, original raw source, surrounding text in that source, speaker, current lesson and revision. No path or cue ID input.", {}, []),
    ("revise_current_subtitle", "保存字幕与讲解", "Only on the user's explicit request to revise the current subtitle or lesson. Read it first. Reconcile their correction with the raw text and their stated meaning. Set selected_text to ONLY the replacement of the selected display segment (HK Traditional for Cantonese). Keep it identical for a lesson-only edit. Provide translation and explanation for the FULL updated source. When text changes, supply tokens covering source_prefix + selected_text + source_suffix exactly, including punctuation, readings (Jyutping for Cantonese), and glosses. Retain unresolved ambiguity; never claim audio verification. Saves workspace SRT and lesson JSON together.", {
        "revision": string(100), "selected_text": string(8000), "translation": string(8000),
        "explanation": string(8000), "tokens": {"type": "array", "maxItems": 1000, "items": {
            "type": "object", "properties": {"text": string(2000), "reading": string(2000),
                                               "meaning": string(2000), "learn": {"type": "boolean"}},
            "required": ["text", "reading", "meaning", "learn"], "additionalProperties": False}}},
     ["revision", "selected_text", "translation", "explanation"]),
]
_WORKSPACE_DEFINITIONS = [
    ("list_study_workspaces", "列出学习工作区", "List real independent study workspaces and their IDs/languages. These are different from watch/prepare/learn views.", {}, []),
    ("configure_speech_api", "配置工作区转写服务", "Set a local or remote speech endpoint and model named by the user. No audio permission or credentials can be granted here. Existing user permission must be obtained in workspace speech settings for a changed remote endpoint.", {"workspace_id": string(100), "backend": string(enum=["local_api", "external"]), "endpoint": string(2000), "model": string(180), "format": string(enum=["verbose_json", "diarized_json"])}, ["workspace_id", "backend", "endpoint", "model"]),
    ("assign_local_text_model", "配置本机讲解与对话模型", "Configure an explicitly requested loopback compatible service and assign its model to a global role across all workspaces. Never accept a secret or initiate a download.", {"workspace_id": string(100), "provider": string(enum=["ollama", "lmstudio", "local_compatible"]), "endpoint": string(2000), "model": string(180), "role": string(enum=["annotation", "review", "companion", "all"])}, ["workspace_id", "provider", "endpoint", "model", "role"]),
    ("workspace_media", "操作工作区媒体库", "List, open the media inbox in Explorer, rescan, or play a video by its returned ID in the current workspace. List supports pagination.", {"workspace_id": string(100), "action": string(enum=["list", "open_folder", "rescan", "play"]), "media_id": string(40), "offset": {"type":"integer","minimum":0}}, ["workspace_id", "action"]),
    ("create_study_workspace", "创建学习工作区", "Create a real study folder inside this app, with its own media inbox, settings and study data. Enables automatic local preparation of videos placed in its media folder once a local model is installed. Remains in this chat until use_study_workspace; never downloads or copies credentials/history. Reuse an existing matching workspace unless the user requests a new one.", {"name": string(80), "source": string(64), "target": string(64), "level": string(enum=["beginner", "intermediate", "advanced"]), "preferences": string(6000)}, ["name", "source", "target"]),
    ("configure_study_workspace", "配置学习工作区", "Configure an observed study workspace ID, including known/studied languages, proficiency and future teaching preferences. Never reprocesses materials or enables external services. When changing current workspace languages, the in-flight reply retains its original context.", {"workspace_id": string(100), "source": string(64), "target": string(64), "level": string(enum=["beginner", "intermediate", "advanced"]), "preferences": string(6000), "auto_prepare": {"type": "boolean"}}, ["workspace_id"]),
    ("recommend_workspace_model", "检查电脑并推荐识别模型", "Inspect actual hardware/runtime and shared installed-model cache for an observed workspace. Prefer a compatible installed model; expose estimated download size and alternatives. Does not download or change settings.", {"workspace_id": string(100), "preference": string(enum=["balanced", "accuracy", "speed"]), "offline": {"type": "boolean"}}, ["workspace_id"]),
    ("propose_workspace_model", "应用已有模型或请求下载确认", "Choose a model from the latest recommendation for this workspace. Reuses installed compatible weights immediately; otherwise creates an exact proposal for a REAL HUMAN to approve in the application's confirmation card. Tool arguments, chat text and model-generated approval cannot grant consent. Pending is NOT downloaded or ready. No approve/download tool exists.", {"workspace_id": string(100), "model": string(150)}, ["workspace_id", "model"]),
    ("reuse_workspace_text_models", "启用全局文本模型", "Request enabling globally assigned text connections in an observed target workspace. Local-only connections may be reused immediately. Remote text roles require a separate real-human permission card before any remote text is enabled. No model-generated consent or key fields are accepted.", {"workspace_id": string(100)}, ["workspace_id"]),
    ("get_workspace_setup", "读取工作区准备状态", "Read workspace setup, model download/proposal status and its media inbox location relative to this app. Report failures/pending honestly. No arbitrary path input.", {"workspace_id": string(100)}, ["workspace_id"]),
    ("use_study_workspace", "进入学习工作区", "Request opening an observed real study workspace. A live reply and other jobs finish before switching; this reply stays in its original chat. Destination gets a setup summary, not copied private chat or credentials. Return deferred until actually switched. Existing workspace conversations remain separate.", {"workspace_id": string(100)}, ["workspace_id"]),
]
_DEFINITIONS.extend(_WORKSPACE_DEFINITIONS)
WORKSPACE_ACTIONS = frozenset(name for name, *_ in _WORKSPACE_DEFINITIONS)

ACTION_LABELS = {name: label for name, label, *_ in _DEFINITIONS}
ACTION_NAMES = frozenset(ACTION_LABELS)
SUBTITLE_ACTIONS = frozenset({"read_current_subtitle", "revise_current_subtitle"})
UI_ACTIONS = frozenset(name for name, *_ in _DEFINITIONS[:10]) | SUBTITLE_ACTIONS | WORKSPACE_ACTIONS


# Only these explicitly registered learning handlers can run after schema validation.
LEARNING_ACTION_HANDLERS = MappingProxyType({
    'list_collections': '_handle_list_collections',
    'read_notebook': '_handle_read_notebook',
    'collect_current_word': '_handle_collect_current',
    'collect_current_sentence': '_handle_collect_current',
    'organize_notebook': '_handle_organize_notebook',
    'append_learning_note': '_handle_append_note',
    'save_notebook': '_handle_save_notebook',
    'update_learning_note': '_handle_modify_note',
    'delete_learning_note': '_handle_modify_note',
    'update_collection': '_handle_collection',
    'delete_collection': '_handle_collection',
    'review_collection': '_handle_collection',
    'practice_words': '_handle_practice',
})


def action_tools():
    return [{"type": "function", "function": {"name": name, "description": description,
             "parameters": {"type": "object", "properties": deepcopy(props), "required": required,
                            "additionalProperties": False}}}
            for name, _, description, props, required in _DEFINITIONS]


def validate_arguments(value, schema):
    kind = schema.get("type")
    valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int,
             "number": type(value) in (int, float) and math.isfinite(value), "boolean": type(value) is bool}
    if not valid.get(kind, False):
        raise ActionError("应用操作参数类型无效。", "invalid_arguments")
    if kind == "object":
        props = schema.get("properties", {})
        if set(value) - set(props) or set(schema.get("required", [])) - set(value):
            raise ActionError("应用操作参数缺失或包含未知字段。", "invalid_arguments")
        for name, item in value.items():
            validate_arguments(item, props[name])
    elif kind == "array":
        if len(value) > schema.get("maxItems", 100):
            raise ActionError("应用操作列表过长。", "invalid_arguments")
        if schema.get("uniqueItems") and len({json.dumps(x, sort_keys=True) for x in value}) != len(value):
            raise ActionError("应用操作列表不能包含重复项。", "invalid_arguments")
        for item in value:
            validate_arguments(item, schema["items"])
    elif kind == "string":
        if len(value) > schema.get("maxLength", 6000) or "\x00" in value:
            raise ActionError("应用操作文本过长或无效。", "invalid_arguments")
    elif kind in ("integer", "number"):
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ActionError("应用操作数值超出范围。", "invalid_arguments")
    if "enum" in schema and value not in schema["enum"]:
        raise ActionError("应用操作参数不在允许范围。", "invalid_arguments")


@dataclass(frozen=True)
class ActionContext:
    session_id: str
    media_path: str
    language: object
    cue: object = None
    selected_media: tuple = ()
    correction: dict | None = None
    guidance: str = ""
    model: str = ""
    workspace_id: str = ""


def previous_word_prompts(history):
    """Only a completed reply from the preceding turn can offer recollection."""
    if not history:
        return ()
    last = history[-1]
    if last.get("role") != "assistant" or last.get("status") not in ("complete", "completed"):
        return ()
    return tuple(event["result"]["word_prompt"] for event in last.get("tool_events", [])
                 if isinstance(event, dict) and event.get("name") == "collect_current_word"
                 and isinstance(event.get("result"), dict) and isinstance(event["result"].get("word_prompt"), str))


class AppActionExecutor:
    def __init__(self, store, notebooks, context, tutor, dispatch, *, cancelled=None, updated=None, word_prompts=()):
        self.store, self.notebooks = store, notebooks
        self.context, self.tutor, self.dispatch = deepcopy(context), tutor, dispatch
        self.cancelled, self.updated = cancelled, updated
        self._read_revision, self._read_ranges = None, []
        self._seen_entries = set()
        self._word_prompts = frozenset(word_prompts)
        self._subtitle_revision = ""
        self._schemas = {t["function"]["name"]: t["function"]["parameters"] for t in action_tools()}
        self._learning_handlers = MappingProxyType({
            name: getattr(self, method) for name, method in LEARNING_ACTION_HANDLERS.items()
        })

    def _check(self):
        if self.cancelled and self.cancelled():
            raise RequestCancelled("应用操作已停止。")

    def _collection(self, ident):
        snapshot = self.notebooks.snapshot(self.context.language.target)
        entry = next((e for e in snapshot.get("collections", snapshot.get("entries", [])) if e["id"] == ident), None)
        if entry is None:
            raise ActionError("收藏不存在或不属于本次对话的学习语言。", "scope_mismatch")
        return entry

    def _require_complete_read(self, target, revision):
        snapshot = self.notebooks.snapshot(target)
        if revision != self._read_revision or revision != snapshot["revision"]:
            raise ActionError("笔记已经变化，请重新读取最新版本再修改。", "revision_conflict")
        covered = 0
        for start, end in sorted(self._read_ranges):
            if start > covered:
                break
            covered = max(covered, end)
        if covered < len(snapshot.get("markdown", "")):
            raise ActionError("请先完整读取当前笔记正文，再修改或删除笔记。", "incomplete_read")

    def __call__(self, name, args):
        self._check()
        if name not in self._schemas:
            raise ActionError("未授权的应用操作。", "unknown_action")
        validate_arguments(args, self._schemas[name])
        if (name in ("collect_current_sentence", "collect_current_word") and self._subtitle_revision
                and re.search(r"字幕|讲解|講解", self.context.guidance)
                and not re.search(r"收藏|收集|書籤|书签|生[词詞]本|bookmark|favou?rite|collection",
                                  self.context.guidance, re.IGNORECASE)):
            raise ActionError("用户要求保存字幕或讲解，没有要求加入收藏箱。", "scope_mismatch")
        if name == "revise_current_subtitle" and (not self._subtitle_revision or args["revision"] != self._subtitle_revision):
            raise ActionError("请先读取本次请求绑定的字幕及修订版本。", "unobserved_target")
        if name in UI_ACTIONS:
            result = self.dispatch(name, deepcopy(args), self.context, self.cancelled)
            if name == "read_current_subtitle":
                if len(json.dumps(result, ensure_ascii=False)) > 32000:
                    raise ActionError("当前源句过长，无法完整读取。", "result_too_large")
                self._subtitle_revision = result["revision"]
            return result
        target = self.context.language.target
        try:
            result = self._learning_action(name, args, target)
        except RequestCancelled:
            raise
        except ActionError:
            raise
        except ValueError as exc:
            from .notebooks import NotebookConflict
            if isinstance(exc, NotebookConflict):
                raise ActionError("笔记或收藏已经变化，请重新读取最新版本再修改。", "revision_conflict") from None
            raise ActionError("学习操作未完成，请检查目标条目、内容和修订版本。") from None
        unchanged_word = name == "collect_current_word" and (
            result.get("confirmation_required") or result.get("collection_status") == "pending")
        if self.updated and not unchanged_word and name not in {"read_notebook", "list_collections", "practice_words"}:
            self.updated()
        return result

    def _learning_action(self, name, args, target):
        handler = self._learning_handlers.get(name)
        if handler is None:
            raise ActionError("未实现的应用操作。", "unknown_action")
        return handler(name, args, target)

    def _handle_list_collections(self, name, args, target):
        rows = self.notebooks.snapshot(target).get("collections", [])
        state = args.get("organized", "all")
        rows = [r for r in rows if state == "all" or bool(r.get("organized")) == (state == "yes")]
        query = args.get("query", "").strip().casefold()
        if query:
            rows = [r for r in rows if query in " ".join(str(r.get(k, "")) for k in ("text", "sentence", "note", "entry_note")).casefold()]
        offset, limit = args.get("offset", 0), args.get("limit", 20)
        page = rows[offset:offset+limit]
        self._seen_entries.update(r["id"] for r in page)
        keys = ("id", "text", "reading", "meaning", "sentence", "note", "entry_note", "note_revision", "organized")
        return {"language": target, "items": [{k: r.get(k) for k in keys} for r in page], "total": len(rows), "offset": offset, "has_more": offset + limit < len(rows), "truncated": offset + limit < len(rows)}

    def _handle_read_notebook(self, name, args, target):
        snap = self.notebooks.snapshot(target)
        text = snap.get("markdown", "")
        offset, limit = args.get("offset", 0), args.get("limit", 18000)
        if self._read_revision != snap["revision"]:
            self._read_revision, self._read_ranges = snap["revision"], []
        result = {"language": target, "markdown": text[offset:offset+limit], "revision": snap["revision"],
                  "offset": offset, "total_characters": len(text), "has_more": offset+limit < len(text),
                  "notes": [{k: n.get(k) for k in ("id", "title", "entry_ids")} for n in snap.get("notes", [])[:100]]}
        # Stay below CompanionAgent's encoded-result cap before recording
        # read coverage; an error substituted there must never count as read.
        while len(json.dumps(result, ensure_ascii=False)) > 30000:
            if result["notes"]:
                result["notes"].pop()
            elif result["markdown"]:
                result["markdown"] = result["markdown"][:len(result["markdown"]) // 2]
            else:
                raise ActionError("笔记读取结果过长，请缩小范围。", "result_too_large")
        end = min(len(text), offset + len(result["markdown"]))
        result["end"] = end
        result["has_more"] = end < len(text)
        result["notes_truncated"] = len(result["notes"]) < len(snap.get("notes", []))
        self._read_ranges.append((offset, end))
        return result

    def _handle_collect_current(self, name, args, target):
        context = self.context
        cue = context.cue
        if cue is None or not context.media_path:
            raise ActionError("本次请求没有可收藏的原句。", "missing_context")
        self._check()
        if name == "collect_current_word":
            text = args["text"]
            if not text.strip() or text not in cue.text:
                raise ActionError("收藏文字必须确实来自本次请求的原句。", "scope_mismatch")
            token = next((t for t in cue.tokens if t.text == text), Token(text))
            identity = (context.workspace_id, context.session_id, target, context.media_path,
                        cue.id, cue.text, text)
            prompt = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode("utf-8")).hexdigest()
            confirmed = args.get("confirm_recollection", False) and prompt in self._word_prompts
            outcome = self.notebooks.collect_word(context.media_path, cue, token, context.language,
                                                  confirm=confirmed)
            if outcome == "confirm":
                return {"saved": False, "confirmation_required": True, "word_prompt": prompt,
                        "message": "这个词已整理。请问用户是否要再次收藏并保留本句出处。"}
            return {"saved": True, "language": target, "organized": False, "collection_status": outcome,
                    "message": {"collected": "已加入收藏夹。", "pending": "这个词在本句中已经收藏。",
                                "pending_source": "这个词已经收藏，已收下本句的新出处。",
                                "referenced": "已再次加入待整理收藏，单词本已关联本句出处。"}[outcome]}
        self.store.save_note(context.media_path, cue, args.get("note", ""), context.language)
        return {"saved": True, "language": target, "organized": False}

    def _handle_organize_notebook(self, name, args, target):
        context = self.context
        result = self.notebooks.organize(target, self.tutor, source_language=context.language.source,
                                         cancelled=self.cancelled, instructions=args.get("instructions", ""), entry_ids=args.get("entry_ids"))
        return {"saved": True, "language": target, "stats": result.get("stats", {}), "revision": result["revision"]}

    def _handle_append_note(self, name, args, target):
        self._check()
        result = self.notebooks.append_note(target, args.get("title", "伴侣补充"), args["text"], entry_ids=args.get("entry_ids"))
        return {"saved": True, "language": target, "revision": result["revision"]}

    def _handle_save_notebook(self, name, args, target):
        self._require_complete_read(target, args["revision"])
        self._check()
        result = self.notebooks.save(target, args["markdown"], args["revision"])
        return {"saved": True, "revision": result["revision"], "language": target}

    def _handle_modify_note(self, name, args, target):
        self._require_complete_read(target, args["revision"])
        self._check()
        if name == "update_learning_note":
            result = self.notebooks.update_note(target, args["note_id"], args["title"], args["text"], args["revision"])
        else:
            result = self.notebooks.delete_note(target, args["note_id"], args["revision"])
        return {"saved": True, "deleted": name == "delete_learning_note", "revision": result["revision"], "language": target}

    def _handle_collection(self, name, args, target):
        entry = self._collection(args["entry_id"])
        self._check()
        if name == "update_collection":
            result = self.notebooks.update_entry_note(entry["id"], args["note"], args["revision"], target_language=target)
            return {"saved": True, "id": entry["id"], "note_revision": result["note_revision"], "language": target}
        if name == "review_collection":
            result = self.notebooks.review(entry["id"], args["rating"])
            return {"saved": True, "id": entry["id"], "rating": args["rating"], "language": target}
        if entry["id"] not in self._seen_entries:
            raise ActionError("请先列出并确认要删除的收藏。", "unobserved_target")
        backup = Path(self.store.data_dir) / "backups" / "agent-actions" / (uuid.uuid4().hex + ".json")
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_text(json.dumps({"saved_at": datetime.now(timezone.utc).isoformat(), "entry": entry}, ensure_ascii=False), encoding="utf-8")
        self._check()
        self.store.delete_entry(entry["id"])
        return {"deleted": True, "id": entry["id"], "backup_created": True, "language": target}

    def _handle_practice(self, name, args, target):
        keys = ("id", "entry_id", "text", "reading", "meaning", "sentence", "note", "question", "answer")
        return {"language": target, "items": [{k: row.get(k) for k in keys} for row in self.notebooks.practice(target)[:20]]}
