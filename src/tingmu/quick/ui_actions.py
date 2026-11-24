"""UiAction domain operations with owned state and explicit callback ports."""

from copy import deepcopy
import threading
from pathlib import Path
import time

from PySide6.QtCore import QThread, Slot


from dataclasses import dataclass
from typing import Any, Callable
from tingmu.app_actions import ActionError, WORKSPACE_ACTIONS
from tingmu.providers import RequestCancelled
from tingmu.storage import canonical_path


@dataclass(frozen=True)
class UiActionPorts:
    perform_app_action: Callable
    appAction: Any
    appActionRequested: Any
    command: Callable
    feature_command: Callable
    is_closed: Callable
    library_cache: Callable
    load_document: Callable
    log: Callable
    media_path: Callable
    model_registry: Callable
    playback: Callable
    player: Callable
    queue: Callable
    refresh_jobs: Callable
    refresh_library: Callable
    resume_inbox: Callable
    save_position: Callable
    seek: Callable
    set_document: Callable
    set_state: Callable
    settings: Callable
    setting: Callable
    start_job: Callable
    start_action_jobs: Callable
    store: Callable
    task_workers: Callable
    thread: Callable
    update_playback: Callable
    workspaces: Callable


class UiActionController:
    def __init__(self, state, ports):
        self.state, self.ports = state, ports

    @staticmethod
    def _check(cancelled):
        if cancelled and cancelled():
            raise RequestCancelled("应用操作已停止。")

    def _media_bound(self, context):
        if not context.media_path or canonical_path(self.ports.media_path()) != canonical_path(
            context.media_path
        ):
            raise ActionError("当前视频已改变，操作未转移到新视频；请重新发起请求。", "media_changed")

    @staticmethod
    def _jobs_public(rows):
        keys = ("id", "name", "status", "completed_cues", "total_cues", "stage_name", "decision")
        return [
            {**{k: j.get(k) for k in keys}, "workflow": j["settings"].get("preparation_workflow", "classic")}
            for j in rows
        ]

    def _save_setting(self, cancelled, key, value):
        self._check(cancelled)
        self.ports.setting(key, value)
        if getattr(self.ports.settings(), key) != value:
            raise ActionError("设置未保存，请检查允许范围。")

    def _perform_app_action(self, name, args, context, cancelled):
        self._check(cancelled)
        controller = self.ports.workspaces()
        if context.workspace_id and (controller is None or context.workspace_id != controller.workspace_id):
            raise ActionError("请求属于另一个学习工作区，请在原工作区继续。", "workspace_changed")
        handler = ACTION_HANDLERS.get(name)
        if handler is None:
            raise ActionError("此操作不属于界面动作。")
        return getattr(self, handler)(name, args, context, cancelled)

    def action_workspace(self, name, args, context, cancelled):
        controller = self.ports.workspaces()
        if controller is None:
            raise ActionError("学习工作区服务当前不可用。")
        if name in ("configure_speech_api", "assign_local_text_model", "workspace_media"):
            from .workspace_actions import apply_workspace_action

            return apply_workspace_action(controller, name, args)
        return controller.handle_agent_action(name, args, context)

    def _request_app_action(self, name, args, context, cancelled):
        from tingmu.app_actions import ActionError
        from tingmu.providers import RequestCancelled

        if name == "recommend_workspace_model":
            from tingmu.app_actions import action_tools, validate_arguments

            schema = next(
                t["function"]["parameters"] for t in action_tools() if t["function"]["name"] == name
            )
            validate_arguments(args, schema)
            if self.ports.is_closed() or (cancelled and cancelled()):
                raise RequestCancelled("应用操作已停止。")
            controller = self.ports.workspaces()
            if controller is None:
                raise ActionError("学习工作区服务当前不可用。")
            return controller.recommend(args, context, cancelled)
        envelope = {
            "name": name,
            "args": deepcopy(args),
            "context": deepcopy(context),
            "cancelled": cancelled,
            "done": threading.Event(),
            "abandoned": threading.Event(),
        }
        if QThread.currentThread() == self.ports.thread():
            self._dispatch_app_action(envelope)
        else:
            self.ports.appActionRequested.emit(envelope)
        deadline = time.monotonic() + 30
        while not envelope["done"].wait(0.05):
            if self.ports.is_closed() or (cancelled and cancelled()):
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
                return (
                    self.ports.is_closed()
                    or envelope["abandoned"].is_set()
                    or bool(original_cancelled and original_cancelled())
                )

            if cancelled():
                raise RequestCancelled("应用操作已停止。")
            name, args = envelope["name"], envelope["args"]
            if name not in ACTION_NAMES:
                raise ActionError("未授权的应用操作。")
            schema = next(
                t["function"]["parameters"] for t in action_tools() if t["function"]["name"] == name
            )
            validate_arguments(args, schema)
            envelope["result"] = self.ports.perform_app_action(name, args, envelope["context"], cancelled)
        except (ActionError, RequestCancelled) as exc:
            envelope["error"] = exc
        except Exception:
            envelope["error"] = ActionError("应用操作未完成，请检查当前状态后重试。")
        finally:
            envelope["done"].set()

    def _start_action_jobs(self, job_ids):
        """Start exactly the requested pending jobs, without retrying unrelated failures."""
        selected = tuple(job_ids)
        if not selected or "queue" in self.ports.task_workers():
            return
        from tingmu.media_inbox import preparation_block_reason
        from tingmu.models import AppSettings
        from tingmu.app_actions import ActionError

        for job in self.ports.queue().snapshot():
            if job["id"] in selected:
                reason = preparation_block_reason(AppSettings(**job["settings"]), [job["media_path"]])
                if reason:
                    raise ActionError(reason, "model_setup_required")
        self.ports.start_job(
            "queue",
            lambda progress, cancelled: self.ports.queue().run(
                progress,
                cancelled,
                on_error=lambda exc: self.ports.log().exception(exc, "queue_error"),
                job_ids=selected,
                allow_model_downloads=False,
            ),
            lambda result: self.ports.set_state(
                domain="queue", status="指定视频处理已结束；可在队列查看状态"
            ),
        )

    def action_read_current_subtitle(self, name, args, context, cancelled):
        from tingmu.corrections import read_target, revise_target

        self._media_bound(context)
        if context.language != self.ports.settings().language:
            raise ActionError("当前学习语言已变化，请重新发起修订。", "scope_mismatch")
        if name == "read_current_subtitle":
            return read_target(
                self.ports.store().load_document(context.media_path, context.language), context.correction
            )
        if "import" in self.ports.task_workers() or any(
            row["media_path"] == context.media_path and row["status"] == "running"
            for row in self.ports.queue().snapshot()
        ):
            raise ActionError("当前视频正在处理，请在完成后重新发起修订。", "queue_busy")
        doc = revise_target(self.ports.store(), context, args, self.ports.settings().target_script, cancelled)
        self.ports.library_cache().invalidate(context.media_path)
        self.ports.set_document(doc)
        self.ports.refresh_library()
        self.ports.set_state(domain="learning", status="本句字幕、译文和讲解已保存")
        return {"saved": True, "backup_created": True, "updated": ["workspace_srt", "study_document"]}

    def action_get_app_context(self, name, args, context, cancelled):
        controller = self.ports.workspaces()
        profiles = [
            {k: p.get(k) for k in ("id", "label", "provider", "model")}
            for p in self.ports.model_registry().public_profiles()
        ]
        return {
            "media_name": Path(context.media_path).name,
            "current_media_matches": self.ports.media_path() == context.media_path,
            "selected_media_names": [Path(p).name for p in context.selected_media],
            "source_language": context.language.source,
            "target_language": context.language.target,
            "position_seconds": self.ports.player().position() / 1000,
            "duration_seconds": self.ports.player().duration() / 1000,
            "playing": bool(self.ports.playback().get("playing")),
            "queue_running": "queue" in self.ports.task_workers(),
            "jobs": self._jobs_public(self.ports.queue().snapshot()),
            "model_profiles": profiles,
            "study_workspace_id": controller.workspace_id if controller else "",
            "settings": {
                k: getattr(self.ports.settings(), k)
                for k in (
                    "theme",
                    "preparation_workflow",
                    "teaching_preferences",
                    "companion_reasoning_effort",
                    "annotation_profile_id",
                    "review_profile_id",
                    "companion_profile_id",
                )
            },
        }

    def action_control_playback(self, name, args, context, cancelled):
        self._media_bound(context)
        action = args["action"]
        value = args.get("value")
        if action in ("seek", "speed", "volume") and value is None:
            raise ActionError("此播放操作需要数值参数。", "invalid_arguments")
        if action == "play":
            self.ports.player().play()
        elif action == "pause":
            self.ports.player().pause()
            self.ports.save_position()
        elif action == "seek":
            self.ports.seek(round(value * 1000))
        elif action == "speed":
            if not 0.25 <= value <= 3:
                raise ActionError("播放速度须在0.25–3之间。")
            self.ports.command("setRate", {"value": value})
        elif action == "volume":
            if not 0 <= value <= 1:
                raise ActionError("音量须在0–1之间。")
            self.ports.command("setVolume", {"value": value})
        elif action == "replay":
            if not context.cue:
                raise ActionError("本次请求没有可重听的字幕。")
            self.ports.seek(round(context.cue.start * 1000) + self.ports.settings().subtitle_offset_ms)
            self.ports.player().play()
        else:
            self.ports.command(
                {"previous_sentence": "previousCue", "next_sentence": "nextCue", "clear_loop": "clearLoop"}[
                    action
                ],
                {},
            )
        self.ports.update_playback()
        return {"executed": True, "action": action, "position_seconds": self.ports.player().position() / 1000}

    def action_process_videos(self, name, args, context, cancelled):
        scope, mode = args["scope"], args["mode"]
        if scope == "current":
            self._media_bound(context)
            paths = [context.media_path]
        elif scope == "selected":
            paths = list(context.selected_media)
        else:
            paths = [
                j["media_path"]
                for j in self.ports.queue().snapshot()
                if j.get("settings", {}).get("target_language") == context.language.target
            ]
        paths = list(dict.fromkeys(canonical_path(p) for p in paths))
        if not paths:
            raise ActionError("本次请求没有符合范围的视频，请先选择视频。", "missing_context")
        if mode in ("teaching", "all") and ({"queue", "import"} & self.ports.task_workers().keys()):
            raise ActionError("请先暂停处理并等待当前步骤结束，再重做材料。", "queue_busy")
        settings = deepcopy(self.ports.settings())
        settings.preparation_workflow = args.get("workflow", settings.preparation_workflow)
        settings.source_language, settings.target_language, settings.level = (
            context.language.source,
            context.language.target,
            context.language.level,
        )
        existing = self.ports.queue().snapshot()
        selected = []
        for path in paths:
            matching = [
                j
                for j in existing
                if j["media_path"] == path
                and j["settings"].get("source_language") == context.language.source
                and j["settings"].get("target_language") == context.language.target
                and j["settings"].get("preparation_workflow", "classic") == settings.preparation_workflow
            ]
            self._check(cancelled)
            if matching:
                selected.append(matching[-1]["id"])
            else:
                selected.extend(j["id"] for j in self.ports.queue().enqueue([Path(path)], settings))
        self._check(cancelled)
        if mode in ("teaching", "all"):
            self.ports.queue().reset(selected, mode, True, settings)
            from .queue_controller import publish_material_reset

            publish_material_reset(
                paths,
                cache=self.ports.library_cache(),
                load_document=self.ports.load_document,
                refresh_jobs=self.ports.refresh_jobs,
                refresh_library=self.ports.refresh_library,
            )
        else:
            for ident in selected:
                self._check(cancelled)
                self.ports.queue().retry(ident)
            self.ports.refresh_jobs()
        rows = [j for j in self.ports.queue().snapshot() if j["id"] in selected]
        runnable = [j["id"] for j in rows if j["status"] in ("queued", "paused")]
        if args.get("start", True) and runnable and "queue" not in self.ports.task_workers():
            self._check(cancelled)
            self.ports.start_action_jobs(runnable)
        rows = [j for j in self.ports.queue().snapshot() if j["id"] in selected]
        return {
            "accepted": True,
            "mode": mode,
            "jobs": self._jobs_public(rows),
            "job_ids": selected,
            "queue_running": "queue" in self.ports.task_workers(),
            "processing_completed": all(j["status"] == "completed" for j in rows),
        }

    def action_manage_queue(self, name, args, context, cancelled):
        action = args["action"]
        jobs = self.ports.queue().snapshot()
        requested = set(args.get("job_ids", []))
        if requested - {j["id"] for j in jobs}:
            raise ActionError("队列任务已经变化，请先查询最新状态。")
        if action == "start" and "queue" not in self.ports.task_workers():
            self.ports.start_action_jobs(
                [
                    j["id"]
                    for j in jobs
                    if j["status"] in ("queued", "paused")
                    and j["settings"].get("target_language") == context.language.target
                    and (not requested or j["id"] in requested)
                ]
            )
            if not requested:
                self.ports.resume_inbox()
        elif action == "pause":
            self.ports.command("pauseQueue", {})
        elif action == "retry":
            targets = (
                [j for j in jobs if j["id"] in requested]
                if requested
                else [
                    j
                    for j in jobs
                    if j["status"] == "failed"
                    and j["settings"].get("target_language") == context.language.target
                ]
            )
            for job in targets:
                self._check(cancelled)
                self.ports.queue().retry(job["id"])
            self.ports.refresh_jobs()
        return {
            "executed": action != "status",
            "action": action,
            "queue_running": "queue" in self.ports.task_workers(),
            "jobs": self._jobs_public(self.ports.queue().snapshot()),
        }

    def action_set_teaching_preferences(self, name, args, context, cancelled):
        self._save_setting(cancelled, "teaching_preferences", args["preferences"].strip())
        return {"saved": True, "applies_to": "future_materials", "existing_materials_regenerated": False}

    def action_set_reasoning_effort(self, name, args, context, cancelled):
        self._save_setting(cancelled, "companion_reasoning_effort", args["effort"])
        return {"saved": True, "effort": args["effort"], "applies_to": "future_requests"}

    def action_select_model(self, name, args, context, cancelled):
        self.ports.feature_command("selectModel", {"role": args["role"], "id": args["profile_id"]})
        return {
            "saved": True,
            "role": args["role"],
            "profile_id": args["profile_id"],
            "applies_to": "future_requests",
        }

    def action_set_appearance(self, name, args, context, cancelled):
        self._save_setting(cancelled, "theme", args["theme"])
        return {"saved": True, "theme": args["theme"]}

    def action_set_player_preferences(self, name, args, context, cancelled):
        if not args:
            raise ActionError("请指定要调整的显示设置。")
        for key, value in args.items():
            self._save_setting(cancelled, key, value)
        return {"saved": True, "settings": args}

    def action_open_workspace(self, name, args, context, cancelled):
        if not args:
            raise ActionError("请指定工作区或模块。")
        self.ports.appAction.emit("workspace", args)
        return {"dispatched": True, **args}


ACTION_HANDLERS = {
    "read_current_subtitle": "action_read_current_subtitle",
    "revise_current_subtitle": "action_read_current_subtitle",
    "get_app_context": "action_get_app_context",
    "control_playback": "action_control_playback",
    "process_videos": "action_process_videos",
    "manage_queue": "action_manage_queue",
    "set_teaching_preferences": "action_set_teaching_preferences",
    "set_reasoning_effort": "action_set_reasoning_effort",
    "select_model": "action_select_model",
    "set_appearance": "action_set_appearance",
    "set_player_preferences": "action_set_player_preferences",
    "open_workspace": "action_open_workspace",
}
ACTION_HANDLERS.update({name: "action_workspace" for name in WORKSPACE_ACTIONS})
