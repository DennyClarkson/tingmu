"""Application services for model profiles, persistent chats and language notebooks.

All QML properties/signals live on Bridge. Worker closures bind their original
session, language and cue; navigation never redirects asynchronous writes.
"""
from copy import deepcopy
from datetime import datetime, timezone
from dataclasses import asdict
import hashlib
import json
import threading
from pathlib import Path
import re
import time
import uuid

from PySide6.QtCore import QUrl, QThread, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from tingmu.models import LanguagePair, Token


def message(role, content, **metadata):
    return {"id": uuid.uuid4().hex, "role": role, "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(), "status": "complete", **metadata}


def learning_tools():
    from tingmu.app_actions import action_tools
    return action_tools()


class FeatureMixin:
    def _feature_init(self):
        from tingmu.providers import ModelRegistry
        from tingmu.notebooks import NotebookManager
        from tingmu.conversations import ConversationStore
        self.model_registry = ModelRegistry(self.store.data_dir)
        self.notebook_manager = NotebookManager(self.store)
        self.conversations = ConversationStore(self.store)
        self._model_profiles = []
        self._model_presets = self.model_registry.presets()
        self._discovered_models = []
        self._model_status = ""
        self._notebook_language = self._settings.target_language
        self._notebooks = {}
        self._chat_sessions = []
        self._active_session_id = ""
        self._chat_worker_session_id = ""
        self._agent_selected_media = []
        self._appActionRequested.connect(self._dispatch_app_action)
        self._chatActionProgress.connect(self._chat_action_progress)
        self._chatDelta.connect(self._chat_delta)
        self._chatPresentation.connect(self._chat_presentation)
        self._learningUpdated.connect(self._refresh_notebooks)
        self._refresh_models()
        self._activate_context_session()
        self.notebook_manager.sync(self._notebook_language)
        self._refresh_notebooks()

    def _display_messages(self):
        from tingmu.richtext import markdown_blocks
        return [{**m, "blocks": markdown_blocks(m.get("content", ""))}
                for m in self._messages]

    def _refresh_models(self):
        self._model_profiles = self.model_registry.public_profiles()
        self.modelsChanged.emit()

    def _profile(self, role):
        profile_id = getattr(self._settings, role + "_profile_id", "")
        return self.model_registry.resolve(profile_id) if profile_id else None

    def _tutor_transport(self, *, profile=None, provider=None, model=None, reasoning_effort=None):
        from tingmu.agent import LearningAgent
        if provider is None:
            profile = self._profile("companion")
        agent = LearningAgent(provider or self._settings.companion_provider,
                              model or self._settings.companion_model,
                              **({"profile": deepcopy(profile)} if profile else {}))
        effort = reasoning_effort if reasoning_effort is not None else self._settings.companion_reasoning_effort
        if effort != "default":
            agent.reasoning_effort = effort
        agent.teaching_preferences = self._settings.teaching_preferences
        return agent

    def _chat_model_snapshot(self, previous=None):
        if previous is not None:
            profile_id = previous.get("id", "")
            if profile_id:
                from tingmu.providers import ProviderError
                if profile_id not in {p["id"] for p in self.model_registry.public_profiles()}:
                    raise ValueError("原消息使用的模型连接已删除，无法原样重试；请发起新消息选择连接。")
                try:
                    profile = self.model_registry.resolve(profile_id)
                except ProviderError:
                    raise ValueError("原消息使用的模型连接当前不可用，请检查连接后重试。") from None
                fingerprint = self._profile_fingerprint(profile)
                if previous.get("fingerprint") != fingerprint:
                    raise ValueError("原消息的模型连接配置已改变，无法原样重试；请使用新消息。")
            else:
                profile = None
            return profile, deepcopy(previous)
        profile = self._profile("companion")
        public = {"provider": self._settings.companion_provider, "model": self._settings.companion_model, "id": ""}
        if profile:
            public = {k: deepcopy(profile[k]) for k in ("id", "provider", "model", "protocol", "reasoning_effort", "max_tokens") if k in profile}
            public["fingerprint"] = self._profile_fingerprint(profile)
        return profile, public

    @staticmethod
    def _profile_fingerprint(profile):
        # Detect endpoint/config changes without storing URLs, credentials or private profiles.
        public = {k: profile.get(k) for k in ("id", "provider", "model", "protocol", "base_url", "reasoning_effort", "max_tokens")}
        return hashlib.sha256(json.dumps(public, sort_keys=True).encode()).hexdigest()

    def _refresh_notebooks(self):
        if self._closed:
            return
        try:
            from tingmu.richtext import markdown_blocks
            self._notebooks = self.notebook_manager.snapshot(self._notebook_language)
            preview = re.sub(r"(?m)^<!-- tingmu:(?:collected:(?:start|end)|note:[^:>]+:(?:start|end|sources)) -->[ \t]*$", "",
                             self._notebooks.get("markdown", ""))
            self._notebooks["blocks"] = markdown_blocks(preview)
            for note in self._notebooks.get("notes", []):
                note["blocks"] = markdown_blocks(note.get("markdown", note.get("body", "")))
            self._notebooks["practice"] = self.notebook_manager.practice(self._notebook_language)
            self.notebooksChanged.emit()
            self._entries = self.store.entries(self.media_path) if self.media_path else self.store.entries()
            self.entriesChanged.emit()
        except (OSError, ValueError) as exc:
            self._error(exc)

    def _refresh_sessions(self):
        self._chat_sessions = self.conversations.list_sessions(self._settings.target_language)
        self.chatSessionsChanged.emit()

    def _activate_context_session(self):
        language = self._settings.language
        session = self.conversations.migrate_legacy(self.media_path, language)
        sessions = self.conversations.list_sessions(language.target)
        existing = next((s for s in sessions if s.get("media_path", "") == self.media_path
                         and s.get("source_language") == language.source), None)
        session = existing or session or self.conversations.create(language, self.media_path)
        self._select_chat(session["id"])

    def _select_chat(self, session_id):
        session = self.conversations.load(session_id)
        if not session:
            raise ValueError("对话已不存在")
        self._active_session_id = session["id"]
        self._messages = deepcopy(session.get("messages", []))
        if "chat" not in self._workers and any(m.get("status") in ("pending", "streaming") for m in self._messages):
            for item in self._messages:
                if item.get("status") in ("pending", "streaming"):
                    item["status"] = "stopped"
            self.conversations.save(session["id"], self._messages)
        status, phase = "", "idle"
        pending = next((m for m in reversed(self._messages)
                        if m.get("role") == "assistant" and m.get("status") in ("pending", "streaming")), None)
        if pending and pending.get("tool_events"):
            event = pending["tool_events"][-1]
            status = event.get("label", "应用操作")
            phase = {"running": "running", "completed": "success", "failed": "error",
                     "success": "success", "error": "error"}.get(event.get("status"), "idle")
        self._set_state(chatToolStatus=status, chatToolPhase=phase)
        self._sync_chat_stopping()
        self.messagesChanged.emit()
        self._refresh_sessions()

    def _sync_chat_stopping(self):
        worker = self._workers.get("chat")
        stopping = bool(worker and self._chat_worker_session_id == self._active_session_id
                        and worker[1].is_set())
        self._set_state(chatStopping=stopping)

    def _stop_model_jobs(self):
        for key in ("chat", "notebook", "summary", "models", "queue"):
            worker = self._workers.get(key)
            if worker:
                worker[1].set()
                if key == "queue":
                    self.queue.pause()
        self._sync_chat_stopping()

    def _persist_chat(self, session, messages):
        self.conversations.save(session["id"], messages)
        # Keep the prior public Store API usable for exports and old integrations.
        self.store.save_conversation(session.get("media_path", ""),
                                     LanguagePair(session["source_language"], session["target_language"]), messages)
        with self.store._connection() as db:
            db.execute("INSERT OR IGNORE INTO chat_legacy_migrations VALUES (?,?,?,?)",
                       (session.get("media_path", ""), session["source_language"],
                        session["target_language"], session["id"]))

    def _chat_delta(self, session_id, message_id, delta):
        if self._closed or session_id != self._active_session_id:
            return
        for item in self._messages:
            if item.get("id") == message_id:
                item["content"] = delta
                item["status"] = "streaming"
                self.messagesChanged.emit()
                break

    @Slot(str, str, object)
    def _chat_presentation(self, session_id, message_id, fields):
        if self._closed or session_id != self._active_session_id:
            return
        for item in self._messages:
            if item.get("id") == message_id:
                for key in ("reasoning_text", "reasoning_kind", "reasoning_elapsed_ms", "phase", "status"):
                    if key in fields:
                        item[key] = fields[key]
                self.messagesChanged.emit()
                break

    def _collect_selection(self, media, language, cue, text):
        if not cue or not media or not text or text not in cue.text or len(text) > 200:
            raise ValueError("请选择当前字幕中不超过 200 字的原文")
        token = next((t for t in cue.tokens if t.text == text), Token(text))
        self.store.save_word(media, cue, token, language)

    def _tool_executor(self, media, language, cue, tutor, cancelled=None, *, session_id="", selected_media=(),
                       correction=None, guidance="", model=""):
        from tingmu.app_actions import AppActionExecutor, ActionContext
        return AppActionExecutor(self.store, self.notebook_manager,
                                 ActionContext(session_id, media, deepcopy(language), deepcopy(cue), tuple(selected_media),
                                               correction, guidance, model, getattr(getattr(self, "workspaces", None), "workspace_id", "")),
                                 tutor, self._request_app_action, cancelled=cancelled,
                                 updated=self._learningUpdated.emit)

    def _request_app_action(self, name, args, context, cancelled):
        from tingmu.app_actions import ActionError
        from tingmu.providers import RequestCancelled
        if name == "recommend_workspace_model":
            from tingmu.app_actions import action_tools, validate_arguments
            schema = next(t["function"]["parameters"] for t in action_tools() if t["function"]["name"] == name)
            validate_arguments(args, schema)
            if self._closed or (cancelled and cancelled()):
                raise RequestCancelled("应用操作已停止。")
            controller = getattr(self, "workspaces", None)
            if controller is None:
                raise ActionError("学习工作区服务当前不可用。")
            if context.workspace_id and context.workspace_id != controller.workspace_id:
                raise ActionError("请求属于另一个学习工作区。", "workspace_changed")
            return controller.recommend(args, context)
        envelope = {"name": name, "args": args, "context": context, "cancelled": cancelled,
                    "done": threading.Event(), "abandoned": threading.Event()}
        if QThread.currentThread() == self.thread():
            self._dispatch_app_action(envelope)
        else:
            self._appActionRequested.emit(envelope)
        deadline = time.monotonic() + 30
        while not envelope["done"].wait(.05):
            if self._closed or (cancelled and cancelled()):
                envelope["abandoned"].set()
                raise RequestCancelled("应用操作已停止。")
            if time.monotonic() > deadline:
                envelope["abandoned"].set()
                raise ActionError("界面未及时处理操作，请稍后重试。", "dispatch_timeout")
        if "error" in envelope:
            raise envelope["error"]
        return envelope["result"]

    @Slot(object)
    def _dispatch_app_action(self, envelope):
        from tingmu.app_actions import ActionError, ACTION_NAMES, action_tools, validate_arguments
        from tingmu.providers import RequestCancelled
        try:
            original_cancelled = envelope["cancelled"]
            def cancelled():
                return (self._closed or envelope["abandoned"].is_set()
                        or bool(original_cancelled and original_cancelled()))
            if cancelled():
                raise RequestCancelled("应用操作已停止。")
            name, args = envelope["name"], envelope["args"]
            if name not in ACTION_NAMES:
                raise ActionError("未授权的应用操作。")
            schema = next(t["function"]["parameters"] for t in action_tools() if t["function"]["name"] == name)
            validate_arguments(args, schema)
            envelope["result"] = self._perform_app_action(name, args, envelope["context"], cancelled)
        except (ActionError, RequestCancelled) as exc:
            envelope["error"] = exc
        except Exception:
            envelope["error"] = ActionError("应用操作未完成，请检查当前状态后重试。")
        finally:
            envelope["done"].set()

    def _perform_app_action(self, name, args, context, cancelled):
        from tingmu.app_actions import ActionError
        from tingmu.providers import RequestCancelled
        from tingmu.storage import canonical_path
        def check():
            if cancelled and cancelled():
                raise RequestCancelled("应用操作已停止。")
        def media_bound():
            if not context.media_path or canonical_path(self.media_path) != canonical_path(context.media_path):
                raise ActionError("当前视频已改变，操作未转移到新视频；请重新发起请求。", "media_changed")
        def jobs_public(rows):
            keys = ("id", "name", "status", "completed_cues", "total_cues")
            return [{k: j.get(k) for k in keys} for j in rows]
        def save_setting(key, value):
            check()
            self.setting(key, value)
            if getattr(self._settings, key) != value:
                raise ActionError("设置未保存，请检查允许范围。")
        check()
        from tingmu.app_actions import WORKSPACE_ACTIONS
        controller = getattr(self, "workspaces", None)
        if context.workspace_id and (controller is None or context.workspace_id != controller.workspace_id):
            raise ActionError("请求属于另一个学习工作区，请在原工作区继续。", "workspace_changed")
        if name in WORKSPACE_ACTIONS:
            if controller is None:
                raise ActionError("学习工作区服务当前不可用。")
            if name in ("configure_speech_api", "assign_local_text_model", "workspace_media"):
                from tingmu.workspace_api_actions import apply_workspace_action
                return apply_workspace_action(controller, name, args)
            return controller.handle_agent_action(name, args, context)
        if name in ("read_current_subtitle", "revise_current_subtitle"):
            from tingmu.corrections import read_target, revise_target
            media_bound()
            if context.language != self._settings.language:
                raise ActionError("当前学习语言已变化，请重新发起修订。", "scope_mismatch")
            if name == "read_current_subtitle":
                return read_target(self.store.load_document(context.media_path, context.language), context.correction)
            if "import" in self._workers or any(
                    row["media_path"] == context.media_path and row["status"] == "running"
                    for row in self.queue.snapshot()):
                raise ActionError("当前视频正在处理，请在完成后重新发起修订。", "queue_busy")
            doc = revise_target(self.store, context, args, self._settings.target_script, cancelled)
            self._set_document(doc)
            self._set_state(status="本句字幕、译文和讲解已保存")
            return {"saved": True, "backup_created": True, "updated": ["workspace_srt", "study_document"]}
        if name == "get_app_context":
            profiles = [{k: p.get(k) for k in ("id", "label", "provider", "model")} for p in self.model_registry.public_profiles()]
            return {"media_name": Path(context.media_path).name, "current_media_matches": self.media_path == context.media_path,
                    "selected_media_names": [Path(p).name for p in context.selected_media],
                    "source_language": context.language.source, "target_language": context.language.target,
                    "position_seconds": self._player.position()/1000, "duration_seconds": self._player.duration()/1000,
                    "playing": bool(self.playback.get("playing")), "queue_running": "queue" in self._workers,
                    "jobs": jobs_public(self.queue.snapshot()), "model_profiles": profiles,
                    "study_workspace_id": controller.workspace_id if controller else "",
                    "settings": {k: getattr(self._settings, k) for k in ("theme", "teaching_preferences", "companion_reasoning_effort", "annotation_profile_id", "review_profile_id", "companion_profile_id")}}
        if name == "control_playback":
            media_bound()
            action = args["action"]
            value = args.get("value")
            if action in ("seek", "speed", "volume") and value is None:
                raise ActionError("此播放操作需要数值参数。", "invalid_arguments")
            if action == "play":
                self._player.play()
            elif action == "pause":
                self._player.pause()
                self.save_position()
            elif action == "seek":
                self._seek(round(value*1000))
            elif action == "speed":
                if not .25 <= value <= 3:
                    raise ActionError("播放速度须在0.25–3之间。")
                self._command("setRate", {"value": value})
            elif action == "volume":
                if not 0 <= value <= 1:
                    raise ActionError("音量须在0–1之间。")
                self._command("setVolume", {"value": value})
            elif action == "replay":
                if not context.cue:
                    raise ActionError("本次请求没有可重听的字幕。")
                self._seek(round(context.cue.start*1000) + self._settings.subtitle_offset_ms)
                self._player.play()
            else:
                self._command({"previous_sentence": "previousCue", "next_sentence": "nextCue", "clear_loop": "clearLoop"}[action], {})
            self._update_playback()
            return {"executed": True, "action": action, "position_seconds": self._player.position()/1000}
        if name == "process_videos":
            scope, mode = args["scope"], args["mode"]
            if scope == "current":
                media_bound()
                paths = [context.media_path]
            elif scope == "selected":
                paths = list(context.selected_media)
            else:
                paths = [j["media_path"] for j in self.queue.snapshot() if j.get("settings", {}).get("target_language") == context.language.target]
            paths = list(dict.fromkeys(canonical_path(p) for p in paths))
            if not paths:
                raise ActionError("本次请求没有符合范围的视频，请先选择视频。", "missing_context")
            if mode in ("teaching", "all") and ({"queue", "import"} & self._workers.keys()):
                raise ActionError("请先暂停处理并等待当前步骤结束，再重做材料。", "queue_busy")
            settings = deepcopy(self._settings)
            settings.source_language, settings.target_language, settings.level = context.language.source, context.language.target, context.language.level
            existing = self.queue.snapshot()
            selected = []
            for path in paths:
                matching = [j for j in existing if j["media_path"] == path and j["settings"].get("source_language") == context.language.source and j["settings"].get("target_language") == context.language.target]
                check()
                if matching:
                    selected.append(matching[-1]["id"])
                else:
                    selected.extend(j["id"] for j in self.queue.enqueue([Path(path)], settings))
            check()
            if mode in ("teaching", "all"):
                self.queue.reset(selected, mode, True, settings)
            else:
                for ident in selected:
                    check()
                    self.queue.retry(ident)
            self._refresh_jobs()
            rows = [j for j in self.queue.snapshot() if j["id"] in selected]
            runnable = [j["id"] for j in rows if j["status"] in ("queued", "paused")]
            if args.get("start", True) and runnable and "queue" not in self._workers:
                check()
                self._start_action_jobs(runnable)
            rows = [j for j in self.queue.snapshot() if j["id"] in selected]
            return {"accepted": True, "mode": mode, "jobs": jobs_public(rows), "job_ids": selected,
                    "queue_running": "queue" in self._workers, "processing_completed": all(j["status"] == "completed" for j in rows)}
        if name == "manage_queue":
            action = args["action"]
            jobs = self.queue.snapshot()
            requested = set(args.get("job_ids", []))
            if requested - {j["id"] for j in jobs}:
                raise ActionError("队列任务已经变化，请先查询最新状态。")
            if action == "start" and "queue" not in self._workers:
                self._start_action_jobs([j["id"] for j in jobs if j["status"] in ("queued", "paused")
                                         and j["settings"].get("target_language") == context.language.target
                                         and (not requested or j["id"] in requested)])
                if not requested:
                    self._resume_inbox()
            elif action == "pause":
                self._command("pauseQueue", {})
            elif action == "retry":
                targets = [j for j in jobs if j["id"] in requested] if requested else [j for j in jobs if j["status"] == "failed" and j["settings"].get("target_language") == context.language.target]
                for job in targets:
                    check()
                    self.queue.retry(job["id"])
                self._refresh_jobs()
            return {"executed": action != "status", "action": action, "queue_running": "queue" in self._workers, "jobs": jobs_public(self.queue.snapshot())}
        if name == "set_teaching_preferences":
            save_setting("teaching_preferences", args["preferences"].strip())
            return {"saved": True, "applies_to": "future_materials", "existing_materials_regenerated": False}
        if name == "set_reasoning_effort":
            save_setting("companion_reasoning_effort", args["effort"])
            return {"saved": True, "effort": args["effort"], "applies_to": "future_requests"}
        if name == "select_model":
            self._feature_command("selectModel", {"role": args["role"], "id": args["profile_id"]})
            return {"saved": True, "role": args["role"], "profile_id": args["profile_id"], "applies_to": "future_requests"}
        if name == "set_appearance":
            save_setting("theme", args["theme"])
            return {"saved": True, "theme": args["theme"]}
        if name == "set_player_preferences":
            if not args:
                raise ActionError("请指定要调整的显示设置。")
            for key, value in args.items():
                save_setting(key, value)
            return {"saved": True, "settings": args}
        if name == "open_workspace":
            if not args:
                raise ActionError("请指定工作区或模块。")
            self.appAction.emit("workspace", args)
            return {"dispatched": True, **args}
        raise ActionError("此操作不属于界面动作。")

    def _start_action_jobs(self, job_ids):
        """Start exactly the requested pending jobs, without retrying unrelated failures."""
        selected = tuple(job_ids)
        if not selected or "queue" in self._workers:
            return
        from tingmu.media_inbox import preparation_block_reason
        from tingmu.models import AppSettings
        from tingmu.app_actions import ActionError
        for job in self.queue.snapshot():
            if job["id"] in selected:
                reason = preparation_block_reason(AppSettings(**job["settings"]), [job["media_path"]])
                if reason:
                    raise ActionError(reason, "model_setup_required")
        self._start_job("queue", lambda progress, cancelled: self.queue.run(
            progress, cancelled, on_error=lambda exc: self.log.exception(exc, "queue_error"),
            job_ids=selected, allow_model_downloads=False), lambda result: self._set_state(status="指定视频处理已结束；可在队列查看状态"))

    @Slot(str, str, object)
    def _chat_action_progress(self, session_id, message_id, event):
        if self._closed or session_id != self._active_session_id:
            return
        phase = {"running": "running", "completed": "success", "failed": "error", "success": "success", "error": "error"}.get(event.get("status"), "idle")
        self._set_state(chatToolStatus=event.get("label", "应用操作"), chatToolPhase=phase)
        for item in self._messages:
            if item.get("id") == message_id:
                item.setdefault("tool_events", []).append(deepcopy(event))
                self.messagesChanged.emit()
                break

    def _v4_send_chat(self, text, *, history=None, reasoning_effort=None, request_context=None, request_model=None):
        from tingmu.companion import CompanionAgent
        from tingmu.setup_chat import setup_intent, SetupAssistant
        local_intent = (setup_intent(text, self._settings.source_language)
                        if not (request_context or {}).get("subtitle_editor") else None)
        if request_model and request_model.get("provider") != "local_setup":
            local_intent = None
        if request_model and request_model.get("provider") == "local_setup" and not local_intent:
            raise ValueError("本地向导仅能重试明确的工作区准备请求；其他问题请发送新消息。")
        if not local_intent and not self._settings.text_models_enabled:
            raise ValueError("此工作区已停用文本模型，可在模型管理中选择仅本机 API。")
        if {"chat", "summary", "notebook"} & self._workers.keys():
            raise ValueError("请等待当前回复或笔记整理完成")
        text = text.strip()
        if not text:
            return
        if len(text) > 6000:
            raise ValueError("单条消息请控制在 6,000 字符内")
        session = self.conversations.load(self._active_session_id)
        if history is None and request_context is None and not self._messages and session.get("media_path", "") != self.media_path:
            self._activate_context_session()
            session = self.conversations.load(self._active_session_id)
        from tingmu.models import Cue
        if request_context is None:
            language = LanguagePair(session["source_language"], session["target_language"], self._settings.level)
            media = self.media_path
            cue = deepcopy(self._active_cue) if language.target == self._settings.target_language else None
            selected_media = tuple(self._agent_selected_media)
            request_context = {"workspace_id": getattr(getattr(self, "workspaces", None), "workspace_id", ""),
                               "media_path": media, "language": asdict(language),
                               "cue": asdict(cue) if cue else None, "selected_media": list(selected_media)}
            if cue and language == self._settings.language:
                request_context["correction"] = self._frozen_subtitle(cue.id)
        else:
            request_context = deepcopy(request_context)
            origin_workspace = request_context.get("workspace_id", "")
            if origin_workspace and origin_workspace != getattr(getattr(self, "workspaces", None), "workspace_id", ""):
                raise ValueError("这条消息属于原学习工作区，请回到原工作区重试；或在此处发起新消息。")
            language = LanguagePair(**request_context["language"])
            media = request_context.get("media_path", "")
            selected_media = tuple(request_context.get("selected_media", []))
            raw_cue = deepcopy(request_context.get("cue"))
            if raw_cue:
                raw_cue["tokens"] = [Token(**item) for item in raw_cue.get("tokens", [])]
                cue = Cue(**raw_cue)
            else:
                cue = None
        if len(selected_media) > 100:
            raise ValueError("单次对话最多绑定 100 个所选视频，请分批选择后再发送；本次消息尚未提交。")
        if local_intent:
            profile, request_model = None, {"provider": "local_setup", "model": "听幕本地向导", "id": ""}
        else:
            profile, request_model = self._chat_model_snapshot(request_model)
        from tingmu.providers import text_allowed
        if not local_intent and not text_allowed(self._settings, profile):
            raise ValueError("云端文本已关闭，请为学习对话分配本机 API 连接。")
        provider, model = request_model["provider"], request_model["model"]
        effort = reasoning_effort if reasoning_effort is not None else self._settings.companion_reasoning_effort
        tutor = None if local_intent else self._tutor_transport(profile=profile, provider=provider, model=model, reasoning_effort=effort)
        prior = deepcopy(self._messages if history is None else history)
        prior = [m for m in prior if m.get("status") != "pending" and m.get("content")]
        frozen = {"reasoning_effort": effort, "request_context": request_context, "request_model": request_model}
        current = prior + [message("user", text, **deepcopy(frozen))]
        assistant = message("assistant", "", status="pending", model=model, tool_events=[],
                            phase="connecting", reasoning_text="", reasoning_kind="reasoning",
                            reasoning_elapsed_ms=0, **deepcopy(frozen))
        current.append(assistant)
        self._persist_chat(session, current)
        if len(prior) == 0 and session.get("title") in ("新对话", ""):
            self.conversations.rename(session["id"], text[:28])
        self._messages = deepcopy(current)
        self.messagesChanged.emit()
        self._refresh_sessions()
        context = {"media_name": Path(media).name, "position_seconds": self._player.position()/1000,
                   "cloud_text_enabled": self._settings.cloud_text_enabled, "has_media": bool(media), "has_subtitles": bool(cue),
                   "is_playing": self.playback["playing"], "review_enabled": self._settings.review_enabled}
        def work(progress, cancelled):
            from tingmu.diagnostics import redact_stream_text
            agent = None
            last_emit = last_save = 0.0
            last_presentation = 0.0
            reasoning_started = None
            reasoning_ms = 0.0
            visible_reasoning = ""

            def presentation(phase, *, force=False, finished=False):
                nonlocal last_presentation, reasoning_started, reasoning_ms
                now = time.monotonic()
                changed = assistant["phase"] != phase
                if reasoning_started is not None and (phase != "thinking" or finished):
                    reasoning_ms += max(0, now - reasoning_started) * 1000
                    reasoning_started = None
                if phase == "thinking" and reasoning_started is None and not finished:
                    reasoning_started = now
                assistant["phase"] = phase
                assistant["reasoning_elapsed_ms"] = round(reasoning_ms + (
                    max(0, now - reasoning_started) * 1000 if reasoning_started is not None else 0))
                if force or changed or now - last_presentation >= 0.1:
                    fields = {key: assistant[key] for key in (
                        "reasoning_text", "reasoning_kind", "reasoning_elapsed_ms", "phase", "status")}
                    self._chatPresentation.emit(session["id"], assistant["id"], fields)
                    last_presentation = now

            def reasoning(event):
                nonlocal visible_reasoning, last_save
                if not isinstance(event, dict) or event.get("kind") not in ("reasoning", "summary"):
                    return
                fragment = event.get("text")
                if not isinstance(fragment, str) or not fragment or "\x00" in fragment or cancelled():
                    return
                # Do not truncate a secret before its final fragment can be redacted.
                # Once the safe visible projection fills the display limit, ignore
                # further public text while continuing normal transport processing.
                if len(assistant["reasoning_text"]) < 60000:
                    visible_reasoning += fragment
                    assistant["reasoning_text"] = redact_stream_text(visible_reasoning)[:60000]
                assistant["reasoning_kind"] = event["kind"]
                assistant["status"] = "streaming"
                presentation("thinking")
                now = time.monotonic()
                if now - last_save >= 1.0:
                    self._persist_chat(session, current)
                    last_save = now

            def delta(fragment):
                nonlocal last_emit, last_save
                assistant["content"] += fragment
                assistant["status"] = "streaming"
                if assistant["phase"] != "answering":
                    presentation("answering", force=True)
                now = time.monotonic()
                if now-last_emit >= 0.06:
                    self._chatDelta.emit(session["id"], assistant["id"], assistant["content"])
                    last_emit = now
                if now-last_save >= 1.0:
                    self._persist_chat(session, current)
                    last_save = now
            try:
                agent = (SetupAssistant(local_intent) if local_intent else
                         CompanionAgent(provider, model, reasoning_effort=effort, **({"profile": profile} if profile else {})))
                def tool_event(event):
                    presentation("acting" if event.get("status") == "running" else "connecting", force=True)
                    assistant["tool_events"].append(deepcopy(event))
                    self._chatActionProgress.emit(session["id"], assistant["id"], event)
                    self._persist_chat(session, current)
                tools = learning_tools()
                if request_context.get("subtitle_editor"):
                    from tingmu.app_actions import SUBTITLE_ACTIONS
                    tools = [tool for tool in tools if tool["function"]["name"] in SUBTITLE_ACTIONS]
                reply = agent.reply(text, prior, language, cue, context, on_delta=delta,
                                    cancelled=cancelled, tools=tools,
                                    tool_executor=self._tool_executor(media, language, cue, tutor, cancelled,
                                        session_id=session["id"], selected_media=selected_media,
                                        correction=request_context.get("correction"), guidance=text, model=model),
                                    on_tool_event=tool_event, on_reasoning=reasoning)
                assistant.update(content=reply, status="stopped" if cancelled() else "complete")
            except Exception as exc:
                from tingmu.agent import AgentError
                assistant["status"] = "stopped" if cancelled() else "error"
                assistant["error"] = "" if cancelled() else (str(exc) if isinstance(exc, AgentError)
                                                             else f"回复失败：{type(exc).__name__}")
                self.log.exception(exc, "chat_error")
            assistant["reasoning_text"] = redact_stream_text(visible_reasoning, final=True)[:60000]
            final_phase = "answering" if assistant["content"] and assistant["status"] == "complete" else assistant["phase"]
            presentation(final_phase, force=True, finished=True)
            self._persist_chat(session, current)
            metadata = agent.last_response_metadata if agent else {}
            self.log.event("model_response", "伴侣回复结束", requested_model=model,
                           returned_model=metadata.get("returned_model"), usage=metadata.get("usage", {}))
            return current

        def completed(messages):
            if self._active_session_id == session["id"]:
                self._messages = messages
                self.messagesChanged.emit()
            self._refresh_sessions()
            self._refresh_notebooks()
            self._set_state(streaming=False, status="已停止" if messages[-1].get("status") == "stopped" else "",
                            error=messages[-1].get("error", ""))
        self._chat_worker_session_id = session["id"]
        self._set_state(chatStopping=False)
        try:
            self._start_job("chat", work, completed)
        except Exception:
            self._chat_worker_session_id = ""
            self._set_state(chatStopping=False)
            raise
        self._set_state(streaming=True, chatToolStatus="", chatToolPhase="idle")

    def _organize_notebook(self, *, entry_ids=None, instructions=""):
        enabled, profile = self.model_registry.text_role(self._settings, "companion")
        if not enabled:
            raise ValueError("当前文本模式不允许此模型。请分配本机 API，或在模型管理中启用云端文本。")
        if {"chat", "summary", "notebook"} & self._workers.keys():
            raise ValueError("请等待当前回复或笔记整理完成")
        target, source = self._notebook_language, self._settings.source_language
        agent = self._tutor_transport(profile=profile, provider=self._settings.companion_provider)
        def work(progress, cancelled):
            return self.notebook_manager.organize(target, agent, source_language=source, cancelled=cancelled, entry_ids=entry_ids, instructions=instructions)
        def completed(result):
            self._refresh_notebooks()
            self._set_state(status="学习笔记已按主题整理", learningBusy=False)
        self._start_job("notebook", work, completed)

    def _feature_command(self, name, p):
        if name == "textModelPolicy":
            mode = p.get("mode")
            if mode not in ("off", "local", "remote"):
                raise ValueError("文本模型使用范围无效。")
            restricted = ((self._settings.text_models_enabled and mode == "off") or
                          (self._settings.cloud_text_enabled and mode != "remote"))
            self._settings.text_models_enabled = mode != "off"
            self._settings.cloud_text_enabled = mode == "remote"
            self.store.save_settings(self._settings)
            self.settingsChanged.emit()
            self._set_state(cloudEnabled=self._settings.cloud_text_enabled)
            if restricted:
                self._stop_model_jobs()
        elif name == "setSelectedMedia":
            from tingmu.storage import canonical_path
            known = {canonical_path(row.get("path", "")) for row in self._library}
            paths = p.get("paths", [])
            if not isinstance(paths, list) or any(not isinstance(x, str) or canonical_path(x) not in known for x in paths):
                raise ValueError("请选择媒体库中的视频")
            self._agent_selected_media = list(dict.fromkeys(canonical_path(x) for x in paths))
        elif name == "saveModel":
            payload = dict(p.get("profile", p))
            key = payload.pop("apiKey", p.get("apiKey", None))
            if payload.get("key_source") != "manual":
                key = None
            self.model_registry.save_profile(payload, api_key=key or None)
            self._model_status = "模型配置已保存"
            self._refresh_models()
        elif name == "deleteModel":
            profile_id = p["id"]
            if "queue" in self._workers:
                raise ValueError("请先暂停队列再删除模型配置")
            self.model_registry.delete_profile(profile_id)
            for role in ("annotation", "review", "companion"):
                if getattr(self._settings, role + "_profile_id") == profile_id:
                    self._feature_command("selectModel", {"role": role, "id": ""})
            self.store.save_settings(self._settings)
            self.settingsChanged.emit()
            self._refresh_models()
        elif name == "discoverModels":
            profile_id = p["id"]
            from tingmu.providers import loopback_url
            profile = next((item for item in self._model_profiles if item["id"] == profile_id), {})
            if not self._settings.cloud_text_enabled and not loopback_url(profile.get("base_url")):
                raise ValueError("当前仅允许查询本机 API。可直接填写远程模型 ID，或启用远程文本 API。")
            self._model_status = "正在读取提供商模型列表…"
            self.modelsChanged.emit()
            def completed(models):
                self._discovered_models = models
                self._model_status = f"已读取 {len(models)} 个模型；可选择或手动输入模型 ID"
                self._refresh_models()
            self._start_job("models", lambda progress, cancelled: self.model_registry.discover_models(
                profile_id, cancelled=cancelled), completed)
        elif name == "refreshModels":
            self._refresh_models()
        elif name == "selectModel":
            role, profile_id = p["role"], p.get("id", "")
            if role not in ("annotation", "review", "companion"):
                raise ValueError("无效的模型用途")
            if profile_id:
                profile = next((x for x in self._model_profiles if x["id"] == profile_id), None)
                if profile is None:
                    raise ValueError("请先保存模型配置")
                if not profile.get("model", "").strip():
                    raise ValueError("请先为连接选择或填写模型 ID 并保存，再分配用途。")
                if role == "annotation":
                    self._settings.provider, self._settings.model = profile["provider"], profile["model"]
                elif role == "companion":
                    self._settings.companion_provider = profile["provider"]
                    self._settings.companion_model = profile["model"]
                else:
                    self._settings.review_provider = profile["provider"]
                    self._settings.review_model = profile["model"]
            else:
                from tingmu.models import AppSettings
                defaults = AppSettings()
                if role == "annotation":
                    self._settings.provider, self._settings.model = defaults.provider, defaults.model
                else:
                    setattr(self._settings, role + "_provider", getattr(defaults, role + "_provider"))
                    setattr(self._settings, role + "_model", getattr(defaults, role + "_model"))
            setattr(self._settings, role + "_profile_id", profile_id)
            self.store.save_settings(self._settings)
            self.settingsChanged.emit()
        elif name == "newChat":
            session = self.conversations.create(self._settings.language, self.media_path)
            self._select_chat(session["id"])
        elif name == "selectChat":
            self._select_chat(p["id"])
        elif name in ("renameChat", "deleteChat"):
            if "chat" in self._workers:
                raise ValueError("请停止回复并等待结束后管理对话")
            if name == "renameChat":
                self.conversations.rename(p["id"], p.get("title", "新对话"))
            else:
                self.conversations.delete(p["id"])
                if p["id"] == self._active_session_id:
                    self._active_session_id = ""
                    self._activate_context_session()
            self._refresh_sessions()
        elif name == "stopChat":
            if "chat" in self._workers:
                self._workers["chat"][1].set()
                self._sync_chat_stopping()
                self._set_state(status="正在停止，已生成的内容会保留")
        elif name in ("retryChat", "editChat"):
            if "chat" in self._workers:
                raise ValueError("请先停止当前回复")
            if name == "editChat":
                index = next((i for i, m in enumerate(self._messages)
                              if m.get("id") == p.get("id") and m["role"] == "user"), -1)
                text = str(p.get("text", ""))
            else:
                index = next((i for i in range(len(self._messages)-1, -1, -1)
                              if self._messages[i]["role"] == "user"), -1)
                text = self._messages[index]["content"] if index >= 0 else ""
            if index < 0:
                raise ValueError("当前没有可重新发送的消息")
            origin = deepcopy(self._messages[index])
            if origin.get("request_model"):
                self._chat_model_snapshot(origin["request_model"])
            # Every regeneration retains the original answer in its original session.
            previous = self.conversations.load(self._active_session_id)
            branch = self.conversations.create(
                LanguagePair(previous["source_language"], previous["target_language"]),
                previous.get("media_path", ""), previous.get("title", "对话")[:180]
                + (" · 编辑" if name == "editChat" else " · 重试"))
            history = deepcopy(self._messages[:index])
            self.conversations.save(branch["id"], history)
            self._select_chat(branch["id"])
            self._v4_send_chat(text, history=history, reasoning_effort=origin.get("reasoning_effort"),
                               request_context=origin.get("request_context"), request_model=origin.get("request_model"))
        elif name == "copyText":
            QGuiApplication.clipboard().setText(str(p.get("text", "")))
        elif name == "openLink":
            from tingmu.richtext import safe_link
            url = str(p.get("url", ""))
            if not safe_link(url):
                raise ValueError("不支持此链接")
            match = re.fullmatch(r"tingmu://entry/(\d+)", url)
            if match:
                self._command("openEntry", {"id": int(match[1])})
            elif QUrl(url).scheme().lower() in ("https", "http") and QUrl(url).host():
                QDesktopServices.openUrl(QUrl(url))
            else:
                raise ValueError("不支持此链接")
        elif name == "selectNotebook":
            self._notebook_language = str(p.get("language", self._settings.target_language))
            self.notebook_manager.sync(self._notebook_language)
            self._refresh_notebooks()
        elif name == "syncNotebook":
            self.notebook_manager.sync(self._notebook_language)
            self._refresh_notebooks()
            self._set_state(status="收藏箱与笔记已刷新；可选择 AI 整理写入笔记")
        elif name == "saveNotebook":
            language = str(p.get("language", self._notebook_language))
            self.notebook_manager.save(language, str(p["markdown"]), str(p["revision"]))
            self._refresh_notebooks()
            self._set_state(status="学习笔记已保存")
        elif name in ("organizeNotebook", "summarizeNotes"):
            self._organize_notebook(entry_ids=p.get("ids"), instructions=str(p.get("instructions", "")))
        elif name == "openNotebook":
            path = self.notebook_manager.sync(self._notebook_language)["path"]
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        elif name == "exportNotebook":
            snapshot = self.notebook_manager.snapshot(self._notebook_language)
            path = self._export_destination(p.get("path"), f"{self._notebook_language}-学习笔记.md", (".md",))
            if path:
                path.write_text(snapshot["markdown"], encoding="utf-8")
                self._set_state(status=f"Markdown 笔记已导出：{path}")
        elif name == "updateEntryNote":
            self.notebook_manager.update_entry_note(int(p["id"]), str(p.get("note", "")),
                expected_revision=str(p["revision"]), target_language=self._notebook_language)
            self._refresh_notebooks()
            self._set_state(status="个性化备注已保存")
        elif name == "reviewEntry":
            self.notebook_manager.review(int(p["id"]), str(p["rating"]))
            self._refresh_notebooks()
        elif name == "practiceNotebook":
            self._notebooks["practice"] = self.notebook_manager.practice(self._notebook_language)
            self.notebooksChanged.emit()
        elif name == "askNotebook":
            if self._notebook_language != self._settings.target_language:
                self.setting("target_language", self._notebook_language)
            self._v4_send_chat(str(p.get("text", "请根据我的学习笔记帮我练习今天需要复习的词语。")))
        elif name == "saveSelection":
            self._collect_selection(self.media_path, self._settings.language,
                                    self._find_cue(p.get("cueId", "")), str(p.get("text", "")))
            self._entries = self.store.entries(self.media_path)
            self.entriesChanged.emit()
            self.notebook_manager.sync(self._settings.target_language)
            self._refresh_notebooks()
            self._set_state(status="所选词句已加入收藏箱")
        elif name in ("resetJobs", "resetMedia"):
            if {"queue", "import"} & self._workers.keys():
                raise ValueError("请先暂停处理队列，等待后台任务结束后清除材料")
            ids = list(p.get("ids", []))
            if name == "resetMedia":
                from tingmu.storage import canonical_path
                paths = {canonical_path(x) for x in p.get("paths", [])}
                ids = [j["id"] for j in self.queue.snapshot() if j["media_path"] in paths]
            count = self.queue.reset(ids, str(p.get("mode", "all")), bool(p.get("requeue", False)), self._settings)
            self._load_document()
            self._refresh_jobs()
            self._refresh_library()
            self._set_state(status=f"已清除 {count} 个视频的材料；视频、收藏与笔记已保留")
        else:
            return False
        return True
