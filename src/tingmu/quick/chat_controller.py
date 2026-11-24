"""Chat domain operations with owned state and explicit callback ports."""

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
import time

from PySide6.QtCore import QThread, Slot
from PySide6.QtGui import QGuiApplication

from tingmu.models import LanguagePair, Token


from dataclasses import dataclass
from typing import Any, Callable
from .chat_messages import message, learning_tools


@dataclass(frozen=True)
class ChatPorts:
    send_chat: Callable
    activate_context_session: Callable
    select_chat: Callable
    active_cue: Callable
    chatActionProgress: Any
    chatDelta: Any
    chatPresentation: Any
    chatSessionsChanged: Any
    chat_model_snapshot: Callable
    conversations: Callable
    frozen_subtitle: Callable
    is_closed: Callable
    learningUpdated: Any
    library_rows: Callable
    log: Callable
    media_path: Callable
    messagesChanged: Any
    notebook_manager: Callable
    persist_chat: Callable
    playback: Callable
    player: Callable
    refresh_notebooks: Callable
    request_app_action: Callable
    set_state: Callable
    settings: Callable
    start_job: Callable
    store: Callable
    task_workers: Callable
    thread: Callable
    tutor_transport: Callable
    workspaces: Callable


class ChatController:
    def __init__(self, state, ports):
        self.state, self.ports = state, ports

    @property
    def active_session_id(self):
        return self.state.active_session_id

    def activate_context_session(self):
        return self.ports.activate_context_session()

    def select_session(self, session_id):
        return self.ports.select_chat(session_id)

    def create_setup_summary(self, text):
        session = self.ports.conversations().create(self.ports.settings().language, title="工作区准备")
        self.ports.conversations().save(session["id"], [message("assistant", text, model="听幕本地向导")])
        self._select_chat(session["id"])

    def _display_messages(self):
        return self.state.markdown.display(self.state.messages)

    def _refresh_sessions(self):
        self.state.sessions = self.ports.conversations().list_sessions(self.ports.settings().target_language)
        self.ports.chatSessionsChanged.emit()

    def _activate_context_session(self):
        language = self.ports.settings().language
        session = self.ports.conversations().migrate_legacy(self.ports.media_path(), language)
        sessions = self.ports.conversations().list_sessions(language.target)
        existing = next(
            (
                s
                for s in sessions
                if s.get("media_path", "") == self.ports.media_path()
                and s.get("source_language") == language.source
            ),
            None,
        )
        session = existing or session or self.ports.conversations().create(language, self.ports.media_path())
        self._select_chat(session["id"])

    def _select_chat(self, session_id):
        session = self.ports.conversations().load(session_id)
        if not session:
            raise ValueError("对话已不存在")
        self.state.active_session_id = session["id"]
        self.state.messages = deepcopy(session.get("messages", []))
        if "chat" not in self.ports.task_workers() and any(
            m.get("status") in ("pending", "streaming") for m in self.state.messages
        ):
            for item in self.state.messages:
                if item.get("status") in ("pending", "streaming"):
                    item["status"] = "stopped"
            self.ports.conversations().save(session["id"], self.state.messages)
        status, phase = "", "idle"
        pending = next(
            (
                m
                for m in reversed(self.state.messages)
                if m.get("role") == "assistant" and m.get("status") in ("pending", "streaming")
            ),
            None,
        )
        if pending and pending.get("tool_events"):
            event = pending["tool_events"][-1]
            status = event.get("label", "应用操作")
            phase = {
                "running": "running",
                "completed": "success",
                "failed": "error",
                "success": "success",
                "error": "error",
            }.get(event.get("status"), "idle")
        self.ports.set_state(domain="chat", chatToolStatus=status, chatToolPhase=phase)
        self._sync_chat_stopping()
        self.ports.messagesChanged.emit()
        self._refresh_sessions()

    def _sync_chat_stopping(self):
        worker = self.ports.task_workers().get("chat")
        stopping = bool(
            worker and self.state.worker_session_id == self.state.active_session_id and worker[1].is_set()
        )
        self.ports.set_state(domain="chat", chatStopping=stopping)

    def _persist_chat(self, session, messages):
        # Persistence must never retain a worker's live streaming dictionaries.
        messages = deepcopy(messages)
        self.ports.conversations().save_with_legacy(session["id"], deepcopy(messages))

    def _chat_delta(self, session_id, message_id, delta):
        if self.ports.is_closed() or session_id != self.state.active_session_id:
            return
        for item in self.state.messages:
            if item.get("id") == message_id:
                item["content"] = delta
                item["status"] = "streaming"
                self.ports.messagesChanged.emit()
                break

    @Slot(str, str, object)
    def _chat_presentation(self, session_id, message_id, fields):
        if self.ports.is_closed() or session_id != self.state.active_session_id:
            return
        for item in self.state.messages:
            if item.get("id") == message_id:
                for key in ("reasoning_text", "reasoning_kind", "reasoning_elapsed_ms", "phase", "status"):
                    if key in fields:
                        item[key] = fields[key]
                self.ports.messagesChanged.emit()
                break

    def _tool_executor(
        self,
        media,
        language,
        cue,
        tutor,
        cancelled=None,
        *,
        session_id="",
        selected_media=(),
        correction=None,
        guidance="",
        model="",
        word_prompts=(),
        workspace_id=None,
    ):
        from tingmu.app_actions import AppActionExecutor, ActionContext

        if workspace_id is None:
            if QThread.currentThread() != self.ports.thread():
                raise RuntimeError("Worker tools require a frozen workspace ID")
            workspace_id = getattr(self.ports.workspaces(), "workspace_id", "")
        return AppActionExecutor(
            self.ports.store(),
            self.ports.notebook_manager(),
            ActionContext(
                session_id,
                media,
                deepcopy(language),
                deepcopy(cue),
                tuple(selected_media),
                deepcopy(correction),
                guidance,
                model,
                workspace_id,
            ),
            tutor,
            self.ports.request_app_action,
            cancelled=cancelled,
            updated=self.ports.learningUpdated.emit,
            word_prompts=word_prompts,
        )

    @Slot(str, str, object)
    def _chat_action_progress(self, session_id, message_id, event):
        if self.ports.is_closed() or session_id != self.state.active_session_id:
            return
        phase = {
            "running": "running",
            "completed": "success",
            "failed": "error",
            "success": "success",
            "error": "error",
        }.get(event.get("status"), "idle")
        self.ports.set_state(
            domain="chat", chatToolStatus=event.get("label", "应用操作"), chatToolPhase=phase
        )
        for item in self.state.messages:
            if item.get("id") == message_id:
                item.setdefault("tool_events", []).append(deepcopy(event))
                self.ports.messagesChanged.emit()
                break

    def _v4_send_chat(
        self, text, *, history=None, reasoning_effort=None, request_context=None, request_model=None
    ):
        from tingmu.app_actions import previous_word_prompts
        from tingmu.companion import CompanionAgent
        from tingmu.setup_chat import setup_intent, SetupAssistant

        local_intent = (
            setup_intent(text, self.ports.settings().source_language)
            if not (request_context or {}).get("subtitle_editor")
            else None
        )
        if request_model and request_model.get("provider") != "local_setup":
            local_intent = None
        if request_model and request_model.get("provider") == "local_setup" and not local_intent:
            raise ValueError("本地向导仅能重试明确的工作区准备请求；其他问题请发送新消息。")
        if not local_intent and not self.ports.settings().text_models_enabled:
            raise ValueError("此工作区已停用文本模型，可在「当前工作区的使用权限」中选择「仅本机 API」。")
        if {"chat", "summary", "notebook"} & self.ports.task_workers().keys():
            raise ValueError("请等待当前回复或笔记整理完成")
        text = text.strip()
        if not text:
            return
        if len(text) > 6000:
            raise ValueError("单条消息请控制在 6,000 字符内")
        session = self.ports.conversations().load(self.state.active_session_id)
        if (
            history is None
            and request_context is None
            and not self.state.messages
            and session.get("media_path", "") != self.ports.media_path()
        ):
            self._activate_context_session()
            session = self.ports.conversations().load(self.state.active_session_id)
        from tingmu.models import Cue

        if request_context is None:
            language = LanguagePair(
                session["source_language"], session["target_language"], self.ports.settings().level
            )
            media = self.ports.media_path()
            cue = (
                deepcopy(self.ports.active_cue())
                if language.target == self.ports.settings().target_language
                else None
            )
            selected_media = tuple(self.state.selected_media)
            request_context = {
                "workspace_id": getattr(self.ports.workspaces(), "workspace_id", ""),
                "media_path": media,
                "language": asdict(language),
                "cue": asdict(cue) if cue else None,
                "selected_media": list(selected_media),
            }
            if cue and language == self.ports.settings().language:
                request_context["correction"] = self.ports.frozen_subtitle(cue.id)
        else:
            request_context = deepcopy(request_context)
            origin_workspace = request_context.get("workspace_id", "")
            if origin_workspace and origin_workspace != getattr(self.ports.workspaces(), "workspace_id", ""):
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
            profile, request_model = self.ports.chat_model_snapshot(request_model)
        from tingmu.providers import text_allowed

        if not local_intent and not text_allowed(self.ports.settings(), profile):
            raise ValueError("云端文本已关闭，请为学习对话分配本机 API 连接。")
        provider, model = request_model["provider"], request_model["model"]
        effort = (
            reasoning_effort
            if reasoning_effort is not None
            else self.ports.settings().companion_reasoning_effort
        )
        tutor = (
            None
            if local_intent
            else self.ports.tutor_transport(
                profile=profile, provider=provider, model=model, reasoning_effort=effort
            )
        )
        prior = deepcopy(self.state.messages if history is None else history)
        prior = [m for m in prior if m.get("status") != "pending" and m.get("content")]
        frozen = {
            "reasoning_effort": effort,
            "request_context": request_context,
            "request_model": request_model,
        }
        current = prior + [message("user", text, **deepcopy(frozen))]
        assistant = message(
            "assistant",
            "",
            status="pending",
            model=model,
            tool_events=[],
            phase="connecting",
            reasoning_text="",
            reasoning_kind="reasoning",
            reasoning_elapsed_ms=0,
            **deepcopy(frozen),
        )
        current.append(assistant)
        self.ports.persist_chat(session, current)
        if len(prior) == 0 and session.get("title") in ("新对话", ""):
            self.ports.conversations().rename(session["id"], text[:28])
        self.state.messages = deepcopy(current)
        self.ports.messagesChanged.emit()
        self._refresh_sessions()
        context = {
            "media_name": Path(media).name,
            "position_seconds": self.ports.player().position() / 1000,
            "cloud_text_enabled": self.ports.settings().cloud_text_enabled,
            "has_media": bool(media),
            "has_subtitles": bool(cue),
            "is_playing": self.ports.playback()["playing"],
            "review_enabled": self.ports.settings().review_enabled,
        }
        # Capture every mutable request input on the UI thread. Worker history is
        # separate from persisted history, and neither aliases the displayed rows.
        snapshot = deepcopy(
            {
                "session": session,
                "messages": current,
                "history": prior,
                "request_context": request_context,
                "profile": profile,
                "local_intent": local_intent,
                "context": context,
                "language": language,
                "cue": cue,
            }
        )
        workspace_id = request_context.get("workspace_id", "")

        def work(progress, cancelled):
            from tingmu.diagnostics import redact_stream_text

            session = deepcopy(snapshot["session"])
            current = deepcopy(snapshot["messages"])
            prior = deepcopy(snapshot["history"])
            request_context = deepcopy(snapshot["request_context"])
            profile = deepcopy(snapshot["profile"])
            local_intent = deepcopy(snapshot["local_intent"])
            context = deepcopy(snapshot["context"])
            language, cue = deepcopy(snapshot["language"]), deepcopy(snapshot["cue"])
            assistant = current[-1]
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
                assistant["reasoning_elapsed_ms"] = round(
                    reasoning_ms
                    + (max(0, now - reasoning_started) * 1000 if reasoning_started is not None else 0)
                )
                if force or changed or now - last_presentation >= 0.1:
                    fields = {
                        key: assistant[key]
                        for key in (
                            "reasoning_text",
                            "reasoning_kind",
                            "reasoning_elapsed_ms",
                            "phase",
                            "status",
                        )
                    }
                    self.ports.chatPresentation.emit(session["id"], assistant["id"], deepcopy(fields))
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
                    self.ports.persist_chat(session, current)
                    last_save = now

            def delta(fragment):
                nonlocal last_emit, last_save
                assistant["content"] += fragment
                assistant["status"] = "streaming"
                if assistant["phase"] != "answering":
                    presentation("answering", force=True)
                now = time.monotonic()
                if now - last_emit >= 0.06:
                    self.ports.chatDelta.emit(session["id"], assistant["id"], assistant["content"])
                    last_emit = now
                if now - last_save >= 1.0:
                    self.ports.persist_chat(session, current)
                    last_save = now

            try:
                agent = (
                    SetupAssistant(local_intent)
                    if local_intent
                    else CompanionAgent(
                        provider, model, reasoning_effort=effort, **({"profile": profile} if profile else {})
                    )
                )

                def tool_event(event):
                    presentation("acting" if event.get("status") == "running" else "connecting", force=True)
                    assistant["tool_events"].append(deepcopy(event))
                    self.ports.chatActionProgress.emit(session["id"], assistant["id"], deepcopy(event))
                    self.ports.persist_chat(session, current)

                tools = learning_tools()
                if request_context.get("subtitle_editor"):
                    from tingmu.app_actions import SUBTITLE_ACTIONS

                    tools = [tool for tool in tools if tool["function"]["name"] in SUBTITLE_ACTIONS]
                reply = agent.reply(
                    text,
                    prior,
                    language,
                    cue,
                    context,
                    on_delta=delta,
                    cancelled=cancelled,
                    tools=tools,
                    tool_executor=self._tool_executor(
                        media,
                        language,
                        cue,
                        tutor,
                        cancelled,
                        session_id=session["id"],
                        selected_media=selected_media,
                        workspace_id=workspace_id,
                        correction=request_context.get("correction"),
                        guidance=text,
                        model=model,
                        word_prompts=previous_word_prompts(prior),
                    ),
                    on_tool_event=tool_event,
                    on_reasoning=reasoning,
                )
                assistant.update(content=reply, status="stopped" if cancelled() else "complete")
            except Exception as exc:
                from tingmu.agent import AgentError

                assistant["status"] = "stopped" if cancelled() else "error"
                assistant["error"] = (
                    ""
                    if cancelled()
                    else (str(exc) if isinstance(exc, AgentError) else f"回复失败：{type(exc).__name__}")
                )
                self.ports.log().exception(exc, "chat_error")
            assistant["reasoning_text"] = redact_stream_text(visible_reasoning, final=True)[:60000]
            final_phase = (
                "answering"
                if assistant["content"] and assistant["status"] == "complete"
                else assistant["phase"]
            )
            presentation(final_phase, force=True, finished=True)
            self.ports.persist_chat(session, current)
            metadata = agent.last_response_metadata if agent else {}
            self.ports.log().event(
                "model_response",
                "伴侣回复结束",
                requested_model=model,
                returned_model=metadata.get("returned_model"),
                usage=metadata.get("usage", {}),
            )
            return deepcopy(current)

        def completed(messages):
            if self.state.active_session_id == session["id"]:
                self.state.messages = deepcopy(messages)
                self.ports.messagesChanged.emit()
            self._refresh_sessions()
            self.ports.refresh_notebooks()
            self.ports.set_state(
                domain="chat",
                streaming=False,
                status="已停止" if messages[-1].get("status") == "stopped" else "",
                error=messages[-1].get("error", ""),
            )

        self.state.worker_session_id = session["id"]
        self.ports.set_state(domain="chat", chatStopping=False)
        try:
            self.ports.start_job("chat", work, completed)
        except Exception:
            self.state.worker_session_id = ""
            self.ports.set_state(domain="chat", chatStopping=False)
            raise
        self.ports.set_state(domain="chat", streaming=True, chatToolStatus="", chatToolPhase="idle")

    def command_set_selected_media(self, name, p):
        from tingmu.storage import canonical_path

        known = {canonical_path(row.get("path", "")) for row in self.ports.library_rows()}
        paths = p.get("paths", [])
        if not isinstance(paths, list) or any(
            not isinstance(x, str) or canonical_path(x) not in known for x in paths
        ):
            raise ValueError("请选择媒体库中的视频")
        self.state.selected_media = list(dict.fromkeys(canonical_path(x) for x in paths))

    def command_new_chat(self, name, p):
        session = self.ports.conversations().create(self.ports.settings().language, self.ports.media_path())
        self._select_chat(session["id"])

    def command_select_chat(self, name, p):
        self._select_chat(p["id"])

    def command_rename_chat(self, name, p):
        if "chat" in self.ports.task_workers():
            raise ValueError("请停止回复并等待结束后管理对话")
        if name == "renameChat":
            self.ports.conversations().rename(p["id"], p.get("title", "新对话"))
        else:
            self.ports.conversations().delete(p["id"])
            if p["id"] == self.state.active_session_id:
                self.state.active_session_id = ""
                self._activate_context_session()
        self._refresh_sessions()

    def command_stop_chat(self, name, p):
        if "chat" in self.ports.task_workers():
            self.ports.task_workers()["chat"][1].set()
            self._sync_chat_stopping()
            self.ports.set_state(domain="chat", status="正在停止，已生成的内容会保留")

    def command_retry_chat(self, name, p):
        if "chat" in self.ports.task_workers():
            raise ValueError("请先停止当前回复")
        if name == "editChat":
            index = next(
                (
                    i
                    for i, m in enumerate(self.state.messages)
                    if m.get("id") == p.get("id") and m["role"] == "user"
                ),
                -1,
            )
            text = str(p.get("text", ""))
        else:
            index = next(
                (
                    i
                    for i in range(len(self.state.messages) - 1, -1, -1)
                    if self.state.messages[i]["role"] == "user"
                ),
                -1,
            )
            text = self.state.messages[index]["content"] if index >= 0 else ""
        if index < 0:
            raise ValueError("当前没有可重新发送的消息")
        origin = deepcopy(self.state.messages[index])
        if origin.get("request_model"):
            self.ports.chat_model_snapshot(origin["request_model"])
        # Every regeneration retains the original answer in its original session.
        previous = self.ports.conversations().load(self.state.active_session_id)
        branch = self.ports.conversations().create(
            LanguagePair(previous["source_language"], previous["target_language"]),
            previous.get("media_path", ""),
            previous.get("title", "对话")[:180] + (" · 编辑" if name == "editChat" else " · 重试"),
        )
        history = deepcopy(self.state.messages[:index])
        self.ports.conversations().save(branch["id"], history)
        self._select_chat(branch["id"])
        self._v4_send_chat(
            text,
            history=history,
            reasoning_effort=origin.get("reasoning_effort"),
            request_context=origin.get("request_context"),
            request_model=origin.get("request_model"),
        )

    def command_copy_text(self, name, p):
        QGuiApplication.clipboard().setText(str(p.get("text", "")))

    def command_send_chat(self, name, p):
        self.ports.send_chat(str(p.get("text", "")))

    def command_clear_chat(self, name, p):
        if {"chat", "summary"} & self.ports.task_workers().keys():
            raise ValueError("请等待当前回复完成")
        self.state.messages = []
        self.ports.persist_chat(self.ports.conversations().load(self.state.active_session_id), [])
        self.ports.messagesChanged.emit()
