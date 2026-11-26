"""Desktop workspace commands. Each open window owns exactly one study store."""

from pathlib import Path
from types import MappingProxyType
from copy import deepcopy
import hashlib
import json
import threading
import time
import uuid

from PySide6.QtCore import QObject, Property, QThread, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from tingmu.runtime import cache_root, confined_path
from tingmu.workspaces import language_code, pair_label
from .runtime_setup import RuntimeSetup
from .workspace_text import TextSetupService


def download_model(model, progress, cancelled):
    from tingmu.workspace_models import download_model as download
    return download(model, progress, cancelled)


def model_status(model):
    from tingmu.workspace_models import model_status as status
    return status(model)


def model_inventory(model):
    from tingmu.workspace_models import model_inventory as inventory
    return inventory(model)


class _DeferredCheckTrigger:
    """Compatibility for former private timer callers; never schedules polling."""

    def __init__(self, schedule):
        self._schedule = schedule

    def start(self):
        self._schedule()

    def stop(self):
        pass

    def isActive(self):
        return False


class WorkspaceController(QObject):
    changed = Signal()
    preparationReadinessChanged = Signal()
    _recommendationReady = Signal(str, object, str)
    _settingsSnapshotRequested = Signal(object)
    _deferredCheckRequested = Signal()

    def __init__(self, bridge, catalog, workspace_id, show_setup, switch, initial_path, *, runtime_setup_dir=None):
        super().__init__(bridge)
        self.bridge = bridge
        self.catalog = catalog
        self.workspace_id = workspace_id
        self.show_setup = show_setup
        self.switch = switch
        self.initial_path = initial_path
        self.error = ""
        self.items = catalog.summaries()
        self.model = ""
        self.readiness = ""
        self._weight_checks = {}
        self.runtime_setup = RuntimeSetup(bridge, runtime_setup_dir)
        self.runtime_setup.detectionFinished.connect(self._runtime_updated)
        self.proposal = {}
        self.text_setup = TextSetupService(self)
        self.recommendations = {}
        self.pending_switch = ""
        self._switch_summary = ""
        self.switch_summary = ""
        self._switch_origin = None
        self._recommendationReady.connect(self._save_recommendation)
        self._settingsSnapshotRequested.connect(self._capture_recommendation_settings)
        self._language_refresh_session = ""
        self._deferred_check_queued = False
        self._deferredCheckRequested.connect(self._check_deferred_actions, Qt.ConnectionType.QueuedConnection)
        self.bridge.workersChanged.connect(self._schedule_deferred_checks)
        self.changed.connect(self._schedule_deferred_checks)
        self._switch_timer = _DeferredCheckTrigger(self._schedule_deferred_checks)
        self._language_timer = _DeferredCheckTrigger(self._schedule_deferred_checks)
        self.bridge.settingsChanged.connect(self._update_languages)
        self.bridge.task_progress.connect(self._download_progress)

    @property
    def text_proposal(self):
        return self.text_setup.proposal

    @text_proposal.setter
    def text_proposal(self, value):
        self.text_setup.proposal = value

    def _text_setup_pending(self):
        return self.text_setup.pending()

    def propose_text_models(self, workspace_id, context):
        return self.text_setup.propose(workspace_id, context)

    def decide_text_reuse(self, ident, approved):
        return self.text_setup.decide(ident, approved)

    def store_for(self, workspace_id):
        return self._store(workspace_id)

    def settings_for(self, workspace_id):
        return self._settings_for(workspace_id)

    def save_settings(self, workspace_id, settings):
        return self._save_settings(workspace_id, settings)

    def workspace_public(self, workspace_id):
        return self._workspace_public(workspace_id)

    @Slot()
    def _schedule_deferred_checks(self):
        if (self.bridge.is_closed or self._deferred_check_queued
                or not (self.pending_switch or self._language_refresh_session)):
            return
        self._deferred_check_queued = True
        self._deferredCheckRequested.emit()

    @Slot()
    def _check_deferred_actions(self):
        self._deferred_check_queued = False
        if self.bridge.is_closed:
            return
        self._finish_language_change()
        self._finish_switch()

    def _update_languages(self):
        current = next(row for row in self.items if row["id"] == self.workspace_id)
        pair = self.bridge.settings_snapshot().language
        label = pair_label(pair.source, pair.target)
        if label != current["languages"]:
            current["languages"] = label
            self.changed.emit()

    @Slot()
    def refresh(self):
        self._weight_checks.clear()
        self.preparationReadinessChanged.emit()
        self.readiness = ""
        self.items = self.catalog.summaries()
        self.changed.emit()

    @Property("QVariantList", constant=True)
    def languageChoices(self):
        from tingmu.locale_catalog import language_choices
        return language_choices()

    @Property(str, notify=changed)
    def speechStatus(self):
        from tingmu.speech_assets import speech_model_status
        modes = speech_model_status(cache_root())
        bgm = "Spleeter 已安装" if modes["bgm"].startswith("spleeter") else "频谱降噪（可下载 Spleeter）"
        speaker = "换人检测模型已安装" if modes["speaker"] != "unavailable" else "换人检测模型待下载"
        return f"{bgm} · {speaker}"

    @Property("QVariantMap", notify=changed)
    def info(self):
        current = next(row for row in self.items if row["id"] == self.workspace_id)
        return {"items": self.items, "id": self.workspace_id, "name": current["name"], "languages": current["languages"],
                "path": str(self.bridge.store.data_dir), "defaultParent": str(self.catalog.root / "workspaces"),
                "cache": str(cache_root()), "showSetup": self.show_setup, "error": self.error,
                "media_folder": str(self.bridge.store.data_dir / "media")}

    @Property("QVariantMap", notify=changed)
    def onboarding(self):
        return {"workspace_id": self.workspace_id, "pending_switch": self.pending_switch,
                "proposal": deepcopy(self.proposal), "text_proposal": deepcopy(self.text_proposal), "recommendations": deepcopy(self.recommendations),
                "status": self.proposal.get("status", "idle"),
                "media_folder": str(self.bridge.store.data_dir / "media")}

    def _store(self, workspace_id):
        from tingmu.storage import Store
        if workspace_id == self.workspace_id:
            return self.bridge.store
        return Store(self.catalog.directory(workspace_id))

    def _settings_for(self, workspace_id):
        if QThread.currentThread() != self.thread():
            raise RuntimeError("Workspace settings must be captured on the UI thread")
        return self.bridge.settings_snapshot() if workspace_id == self.workspace_id else self._store(workspace_id).load_settings()

    @staticmethod
    def _fingerprint(settings):
        values = {key: value for key, value in settings.to_dict().items()
                  if "asr" in key or key in ("source_language", "target_language", "device", "allow_audio_upload")}
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def _save_settings(self, workspace_id, settings):
        old_language = self.bridge.settings_snapshot().language
        self._store(workspace_id).save_settings(settings)
        if workspace_id == self.workspace_id:
            self.bridge.apply_settings_snapshot(settings)
            if old_language != settings.language:
                self.bridge.reload_document()
                self.bridge.refresh_library()
                self.bridge.learning.set_language(settings.target_language)
                self.bridge.learning.refresh()
                # Retain the originating chat until the frozen reply completes.
                if "chat" not in self.bridge.task_keys():
                    self.bridge.chat.activate_context_session()
                else:
                    self._language_refresh_session = self.bridge.chat.active_session_id
        self.items = self.catalog.summaries()
        self.changed.emit()

    def _finish_language_change(self):
        if self.bridge.is_closed or not self._language_refresh_session:
            return
        if "chat" in self.bridge.task_keys():
            return
        origin_session = self._language_refresh_session
        self._language_refresh_session = ""
        if self.bridge.chat.active_session_id == origin_session:
            self.bridge.chat.activate_context_session()

    def _workspace_public(self, workspace_id):
        from tingmu.workspace_text import effective_text_roles
        row = next((row for row in self.catalog.summaries() if row["id"] == workspace_id), None)
        if row is None:
            raise ValueError("工作区不存在，请重新列出工作区")
        settings = self._settings_for(workspace_id)
        folder = self.catalog.directory(workspace_id) / "media"
        # Tools receive app-relative locations; the trusted UI can open the absolute folder.
        return {"id": workspace_id, "name": row["name"], "source": settings.source_language,
                "target": settings.target_language, "level": settings.level,
                "preferences": settings.teaching_preferences, "model": settings.asr_model,
                "device": settings.device, "speech_status": model_status(settings.asr_model),
                "asr_backend": settings.asr_backend, "audio_upload_allowed": settings.allow_audio_upload,
                "api_model": settings.local_asr_api_model if settings.asr_backend == "local_api" else settings.external_asr_model,
                "auto_prepare": getattr(settings, "auto_prepare_media", False),
                "text_configured": bool(effective_text_roles(folder.parent, settings)),
                "media_folder": str(folder.relative_to(self.catalog.root)),
                "current": workspace_id == self.workspace_id}

    @Slot(object)
    def _capture_recommendation_settings(self, request):
        """Copy UI-owned settings before the requesting worker starts its probe."""
        from tingmu.app_actions import ActionError
        from tingmu.providers import RequestCancelled
        try:
            cancelled = request["cancelled"]
            if self.bridge.is_closed or request["abandoned"].is_set() or (cancelled and cancelled()):
                raise RequestCancelled("应用操作已停止。")
            origin = request["origin_workspace_id"]
            if origin and origin != self.workspace_id:
                raise ActionError("请求属于另一个学习工作区。", "workspace_changed")
            request["settings"] = self._settings_for(request["workspace_id"])
        except Exception as exc:
            request["error"] = exc
        finally:
            request["done"].set()

    def recommend(self, args, context, cancelled=None):
        """Probe worker-owned settings; publish an independent result snapshot."""
        from tingmu.app_actions import ActionError
        from tingmu.model_advisor import recommend_models
        from tingmu.providers import RequestCancelled
        args = deepcopy(args)
        workspace_id = args["workspace_id"]
        request = {"workspace_id": workspace_id, "origin_workspace_id": context.workspace_id,
                   "cancelled": cancelled, "done": threading.Event(), "abandoned": threading.Event()}
        if QThread.currentThread() == self.thread():
            self._capture_recommendation_settings(request)
        else:
            self._settingsSnapshotRequested.emit(request)
        deadline = time.monotonic() + 30
        while not request["done"].wait(.05):
            if self.bridge.is_closed or (cancelled and cancelled()):
                request["abandoned"].set()
                raise RequestCancelled("应用操作已停止。")
            if time.monotonic() > deadline:
                request["abandoned"].set()
                raise ActionError("界面未及时处理操作，请稍后重试。", "dispatch_timeout")
        if "error" in request:
            raise request["error"]
        if self.bridge.is_closed or (cancelled and cancelled()):
            raise RequestCancelled("应用操作已停止。")
        settings = request["settings"]
        fingerprint = self._fingerprint(settings)
        result = recommend_models(settings, language=settings.target_language,
                                  preference=args.get("preference", "balanced"), offline=args.get("offline", False))
        def public_hardware(value):
            if isinstance(value, dict):
                return {k: public_hardware(v) for k, v in value.items() if k not in ("cache_path", "interpreter")}
            if isinstance(value, list):
                return [public_hardware(v) for v in value]
            return value
        result = {**result, "hardware": public_hardware(result.get("hardware", {})), "workspace_id": workspace_id}
        if self.bridge.is_closed or (cancelled and cancelled()):
            raise RequestCancelled("应用操作已停止。")
        self._recommendationReady.emit(workspace_id, deepcopy(result), fingerprint)
        return result

    @Slot(str, object, str)
    def _save_recommendation(self, workspace_id, result, fingerprint):
        if self.bridge.is_closed:
            return
        self.recommendations[workspace_id] = {**deepcopy(result), "settings_fingerprint": fingerprint}
        self.changed.emit()

    def handle_agent_action(self, name, args, context):
        from tingmu.app_actions import ActionError

        if context.workspace_id and context.workspace_id != self.workspace_id:
            raise ActionError("请求属于另一个学习工作区，请在原工作区继续。", "workspace_changed")
        handlers = {
            "list_study_workspaces": self.list_study_workspaces,
            "create_study_workspace": self.create_study_workspace,
            "reuse_workspace_text_models": self.reuse_workspace_text_models,
            "configure_study_workspace": self.configure_study_workspace,
            "get_workspace_setup": self.get_workspace_setup,
            "propose_workspace_model": self.propose_workspace_model,
            "use_study_workspace": self.use_study_workspace,
        }
        handler = handlers.get(name)
        if handler is None:
            raise ActionError("未知的学习工作区操作。", "unknown_action")
        if name not in {"list_study_workspaces", "create_study_workspace"}:
            self.catalog.directory(args["workspace_id"])
        return handler(args, context)

    def list_study_workspaces(self, args, context):
        return {"workspaces": [self._workspace_public(row["id"]) for row in self.catalog.summaries()],
                "current_workspace_id": self.workspace_id}

    def create_study_workspace(self, args, context):
        from tingmu.models import AppSettings
        settings = AppSettings(source_language=args["source"], target_language=args["target"],
                               level=args.get("level", "beginner"), teaching_preferences=args.get("preferences", ""),
                               auto_prepare_media=True, cloud_text_enabled=False)
        workspace_id = self.catalog.create(args["name"], args["source"], args["target"], initial_settings=settings)
        return {"created": True, "workspace": self._workspace_public(workspace_id)}

    def reuse_workspace_text_models(self, args, context):
        workspace_id = args["workspace_id"]
        return self.propose_text_models(workspace_id, context)

    def configure_study_workspace(self, args, context):
        from tingmu.app_actions import ActionError

        workspace_id = args["workspace_id"]
        if workspace_id == self.workspace_id and set(self.bridge.task_keys()) - {"chat"}:
            raise ActionError("当前工作区有后台任务，请完成后再调整学习设置。", "workspace_busy")
        settings = self._settings_for(workspace_id)
        for field, key in (("source_language", "source"), ("target_language", "target")):
            if key in args:
                setattr(settings, field, language_code(args[key]))
        for field, key in (("level", "level"), ("teaching_preferences", "preferences"), ("auto_prepare_media", "auto_prepare")):
            if key in args:
                setattr(settings, field, args[key])
        self._save_settings(workspace_id, settings)
        if workspace_id == self.workspace_id and args.get("auto_prepare") is True:
            self.bridge.resume_media_inbox()
        return {"saved": True, "workspace": self._workspace_public(workspace_id), "applies_to": "future_materials"}

    def get_workspace_setup(self, args, context):
        workspace_id = args["workspace_id"]
        return {"workspace": self._workspace_public(workspace_id),
                "proposal": deepcopy(self.proposal) if self.proposal.get("workspace_id") == workspace_id else {},
                "switch_pending": self.pending_switch == workspace_id}

    def propose_workspace_model(self, args, context):
        from tingmu.app_actions import ActionError

        workspace_id = args["workspace_id"]
        result = self.recommendations.get(workspace_id, {})
        settings = self._settings_for(workspace_id)
        if result.get("settings_fingerprint") != self._fingerprint(settings):
            raise ActionError("请先按最新工作区设置检查电脑并推荐模型。", "recommendation_stale")
        candidate = next((item for item in result.get("candidates", []) if item["model"] == args["model"]), None)
        if not candidate or not candidate.get("available"):
            raise ActionError("此模型不可用于当前电脑，请选择本次推荐中的可用模型。", "model_unavailable")
        missing = self.runtime_setup.model_message(candidate["model"], candidate.get("device", "auto"))
        if missing:
            raise ActionError(missing, "runtime_missing")
        inventory = model_inventory(candidate["model"])
        if inventory["installed"]:
            if self.proposal.get("workspace_id") == workspace_id and self.proposal.get("status") != "downloading":
                self.proposal.update(status="superseded", error="", progress="已改用本地现有模型")
            settings.asr_model = candidate["model"]
            settings.device = candidate.get("device", "auto")
            settings.asr_backend, settings.allow_audio_upload = "local", False
            self._save_settings(workspace_id, settings)
            return {"applied": True, "reused_installed": True, "workspace": self._workspace_public(workspace_id)}
        if self.proposal.get("status") == "downloading":
            raise ActionError("已有获准的模型下载正在执行，请等待或暂停后再提出方案。", "download_busy")
        current = self.proposal
        if (current.get("status") == "pending" and current.get("workspace_id") == workspace_id
                and current.get("model") == candidate["model"] and current.get("origin_session_id") == context.session_id
                and current.get("settings_fingerprint") == self._fingerprint(settings)):
            return {"applied": False, "requires_human_confirmation": True, "proposal": deepcopy(current)}
        workspace = self._workspace_public(workspace_id)
        self.proposal = {"id": uuid.uuid4().hex, "workspace_id": workspace_id,
            "workspace_name": workspace["name"], "origin_workspace_id": self.workspace_id,
            "origin_session_id": context.session_id, "model": candidate["model"],
            "model_name": candidate.get("name", candidate["model"]), "device": candidate.get("device", "auto"),
            "download_bytes": inventory.get("download_bytes", candidate.get("download_bytes", 0)),
            "estimated_download_bytes": inventory.get("estimated_download_bytes", candidate.get("estimated_download_bytes", 0)),
            "settings_fingerprint": self._fingerprint(settings), "status": "pending", "error": "", "progress": "",
            "media_folder": workspace["media_folder"]}
        self.changed.emit()
        return {"applied": False, "requires_human_confirmation": True, "proposal": deepcopy(self.proposal)}

    def use_study_workspace(self, args, context):
        workspace_id = args["workspace_id"]
        if workspace_id == self.workspace_id:
            return {"deferred": False, "already_active": True, "workspace_id": workspace_id}
        self.pending_switch = workspace_id
        self._switch_origin = None
        if "chat" in self.bridge.task_keys():
            # Navigation may display another chat. Bind the durable reply in
            # the tool's originating session, never the current UI message.
            origin = self.bridge.conversations.load(context.session_id) or {}
            reply = next((row for row in reversed(origin.get("messages", []))
                          if row.get("role") == "assistant"
                          and row.get("status") in ("pending", "streaming")), {})
            self._switch_origin = (context.session_id, reply.get("id", ""))
        self._switch_summary = "已进入学习工作区「" + self._workspace_public(workspace_id)["name"] + "」。上一轮完整回复保留在原工作区；此处的聊天、媒体与学习资料独立保存。请将视频放入此工作区的 media 文件夹。"
        self.changed.emit()
        return {"deferred": True, "workspace_id": workspace_id, "reason": "等待本轮回复、下载确认和后台任务结束后进入；当前回复仍保存在原工作区"}


    @Slot(str, bool, result=bool)
    def decideTextReuse(self, ident, approved):
        return self.decide_text_reuse(ident, approved)

    @Slot(str, object)
    def _download_progress(self, key, text):
        if self.bridge.is_closed:
            return
        if key == "model-download" and self.proposal.get("status") == "downloading":
            if self.proposal.get("progress") != str(text):
                self.proposal["progress"] = str(text)
                self.changed.emit()

    @Slot(str, result="QVariantMap")
    def downloadPlan(self, kind):
        try:
            if kind == "downloadSpeech":
                self._check_model_runtime("small", "cpu")
                from tingmu.speech_assets import ARCHIVE_BYTES, planned_download_bytes
                size = planned_download_bytes(cache_root())
                return {"kind": kind, "label": "人声分离与换人检测", "bytes": float(size),
                        "packageBytes": float(sum(ARCHIVE_BYTES.values()))}
            settings = self.bridge.settings_snapshot()
            model = settings.asr_model
            self._check_model_runtime(model, settings.device)
            inventory = model_inventory(model)
            return {"kind": "download", "label": model, "model": model, "bytes": float(inventory["download_bytes"]),
                    "packageBytes": float(inventory["estimated_download_bytes"]), "installed": inventory["installed"]}
        except (ValueError, OSError, RuntimeError) as exc:
            self.error = str(exc)
            self.changed.emit()
            return {}

    @Slot(str, bool, result=bool)
    def decideProposal(self, proposal_id, approved):
        """Trusted UI entry point. It is deliberately absent from all model tool schemas."""
        self.error = ""
        proposal = self.proposal
        try:
            if proposal_id != proposal.get("id") or proposal.get("status") != "pending":
                raise ValueError("此确认已过期，请重新提出下载方案")
            if proposal["origin_workspace_id"] != self.workspace_id or proposal["origin_session_id"] != self.bridge.chat.active_session_id:
                raise ValueError("请回到提出该方案的原会话确认")
            if not approved:
                proposal.update(status="declined", progress="", error="")
                self.pending_switch = ""
                self.changed.emit()
                return True
            settings = self._settings_for(proposal["workspace_id"])
            if proposal["settings_fingerprint"] != self._fingerprint(settings):
                proposal["status"] = "stale"
                raise ValueError("工作区设置已改变，请重新推荐并确认")
            if "model-download" in self.bridge.task_keys():
                raise ValueError("已有模型下载正在执行")
            self._check_model_runtime(proposal["model"], proposal["device"])
            proposal.update(status="downloading", error="", progress="已确认，开始下载")
            frozen = deepcopy(proposal)
            def fetch(progress, cancel):
                try:
                    self.runtime_setup.check_before_transfer(frozen["model"], frozen["device"])
                    download_model(frozen["model"], progress, cancel)
                    if not model_inventory(frozen["model"])["installed"]:
                        raise ValueError("模型文件仍不完整，请重新确认后重试")
                    if cancel():
                        return {"status": "cancelled", "error": "下载已暂停，请重新提出方案后继续"}
                    return {"status": "ready", "error": ""}
                except Exception as exc:
                    return {"status": "cancelled" if cancel() else "failed", "error": str(exc)}
            def complete(result):
                if self.proposal.get("id") != frozen["id"]:
                    return
                self.proposal.update(result)
                if result["status"] == "ready":
                    settings = self._settings_for(frozen["workspace_id"])
                    if self._fingerprint(settings) != frozen["settings_fingerprint"]:
                        self.proposal.update(status="stale", error="模型已缓存；工作区设置已变化，请重新推荐以应用")
                    else:
                        settings.asr_model, settings.device = frozen["model"], frozen["device"]
                        settings.asr_backend, settings.allow_audio_upload = "local", False
                        self._save_settings(frozen["workspace_id"], settings)
                        self.bridge.publish_state(status="模型已就绪，可将视频放入工作区 media 文件夹")
                if self.proposal.get("error"):
                    self.error = self.proposal["error"]
                    if self.pending_switch == frozen["workspace_id"]:
                        self.pending_switch = ""
                self._runtime_updated()
            self.bridge.start_task("model-download", fetch, complete)
        except Exception as exc:
            self.error = str(exc)
            if proposal.get("status") == "downloading" and "model-download" not in self.bridge.task_keys():
                proposal.update(status="failed", error=self.error)
            if proposal.get("status") in ("failed", "stale") and self.pending_switch == proposal.get("workspace_id"):
                self.pending_switch = ""
            self.changed.emit()
            return False
        self.changed.emit()
        return True

    def _finish_switch(self):
        if not self.pending_switch or self.bridge.is_closed:
            return
        if self.bridge.task_keys() or self.proposal.get("status") in ("pending", "downloading") or self._text_setup_pending():
            return
        if self._switch_origin:
            session_id, message_id = self._switch_origin
            try:
                origin = self.bridge.conversations.load(session_id)
            except ValueError:
                origin = {}
            rows = origin.get("messages", [])
            reply = next((row for row in rows if row["id"] == message_id), {})
            if reply.get("status") not in ("complete", "completed"):
                self.pending_switch = ""
                self._switch_origin = None
                self.error = "本轮回复未完整保存、已停止或失败，已保留当前工作区；可重新请求进入。"
                self.changed.emit()
                return
        workspace_id = self.pending_switch
        self.pending_switch = ""
        self._switch_origin = None
        try:
            workspace = self._workspace_public(workspace_id)
            candidate = next((item for item in self.recommendations.get(workspace_id, {}).get("candidates", [])
                              if item["model"] == workspace["model"]), {})
            reasons = candidate.get("rationale", "")
            speech = (candidate.get("name", workspace["model"]) + " · " + workspace["device"] + " · " + workspace["speech_status"]
                      if workspace["asr_backend"] == "local" else "转写 API · " + workspace["api_model"])
            self.switch_summary = (
                self._switch_summary + "\n\n识别模型：" + speech
                + ("\n推荐依据：" + reasons if reasons else "")
                + "\n\n应用资料目录下的视频文件夹：" + workspace["media_folder"]
                + "\n可在「工作区设置 → 语言/方言与文件」点「打开视频目录」。复制完成后会按本工作区设置自动准备字幕。")
            self.switch(workspace_id, None, False)
        except Exception as exc:
            self.error = str(exc)
            self.changed.emit()

    @Slot(str, result=str)
    def readinessFor(self, model):
        message = self.runtime_setup.model_message(model, self.bridge.settings_snapshot().device)
        if message:
            return message
        if self.model != model or not self.readiness:
            self.model = model
            self.readiness = model_status(model)
        return self.readiness

    def _readiness_roles(self, settings, *, fresh=False):
        from tingmu.agent import provider_status
        from .preparation_readiness import text_roles
        if fresh:
            registry = self.bridge.store.model_registry
            registry.load_roles(settings)
            profiles = registry.public_profiles()
        else:
            profiles = self.bridge.modelProfiles
        return text_roles(settings, profiles, provider_status())

    @Slot(str, "QVariantMap", str, result="QVariantMap")
    def preparationReadiness(self, scenario, job, current_path):
        from .preparation_readiness import job_configuration, readiness_rows, subtitle_source

        workspace = self.bridge.settings_snapshot()
        settings, differs = job_configuration(workspace, job)
        source = subtitle_source(scenario, settings.preparation_workflow, job, current_path, settings.target_language)
        components, weights, supported = {}, True, True
        if source in {"asr", "evaluate"}:
            from tingmu.runtime import installation_root
            from tingmu.runtime_deps import last_probe
            for kind in ("ffmpeg", "qwen", "local-asr", "whisper-cuda"):
                row = self.runtime_setup.results.get(kind) or last_probe(installation_root(), kind)
                if row:
                    components[kind] = row
            if settings.asr_backend == "local":
                from tingmu.workspace_models import weight_readiness
                from tingmu.model_advisor import model_supports_language
                key = settings.asr_model, settings.target_language
                if key not in self._weight_checks:
                    self._weight_checks[key] = (weight_readiness(key[0]).ready, model_supports_language(*key))
                weights, supported = self._weight_checks[key]
        roles = self._readiness_roles(settings) if scenario not in ("speech", "watch") else {}
        rows = readiness_rows(settings, source, roles, components=components, weights=weights,
                              language_supported=supported, portable=self.runtime_setup.available,
                              include_text=scenario != "speech", workspace=workspace)
        # Agent inspection can reuse a caption without ASR. Surface fallback
        # prerequisites before the queue starts, separately from definite issues.
        fallback = readiness_rows(settings, "asr", {}, components=components, weights=weights,
                                  language_supported=supported, portable=self.runtime_setup.available,
                                  include_text=False) if source == "evaluate" else []
        if differs:
            can_retry_teaching = (job.get("needs_teaching") and settings.preparation_workflow == "subtitle_agent"
                                  and job.get("status") in ("failed", "paused", "cleared"))
            rows.append({"code": "frozenRetry" if can_retry_teaching else "frozenSpec", "action": "", "value": ""})
        return {"source": source, "issues": rows, "asrFallback": fallback, "job": bool(job.get("settings")), "workflow": settings.preparation_workflow,
                "mode": settings.segmentation_mode, "backend": settings.asr_backend,
                "policy": "off" if not settings.text_models_enabled else
                          "remote" if settings.cloud_text_enabled else "local"}

    @Slot(result="QVariantMap")
    def remotePermissionOffer(self):
        roles = self._readiness_roles(self.bridge.settings_snapshot(), fresh=True)
        remote = {role: row for role, row in roles.items() if not row["local"] and (row["id"] or row["configured"])}
        signature = hashlib.sha256(json.dumps([self.workspace_id, remote], sort_keys=True).encode()).hexdigest()
        return {"signature": signature, "roles": [
            {"role": role, "model": row["model"], "host": row["host"]} for role, row in remote.items()]}

    @Slot(str, result=bool)
    def confirmRemotePermission(self, signature):
        if signature != self.remotePermissionOffer()["signature"]:
            return False
        self.bridge.command("textModelPolicy", {"mode": "remote"})
        return self.bridge.settings_snapshot().cloud_text_enabled

    def _runtime_updated(self):
        self._weight_checks.clear()
        self.preparationReadinessChanged.emit()
        self.readiness = ""
        self.changed.emit()

    def _check_model_runtime(self, model, device):
        message = self.runtime_setup.model_message(model, device)
        if message:
            self.runtime_setup.detect()
            raise ValueError(message)

    def _idle(self):
        if self.bridge.task_keys():
            raise ValueError("请等待后台任务完成，或暂停正在处理的任务后切换工作区")

    @Slot(result="QVariantMap")
    def textReuseOffer(self):
        from tingmu.workspace_text import text_reuse_offer
        return text_reuse_offer(self.bridge.store.data_dir, self.bridge.settings_snapshot())

    @Slot(str, "QVariantMap", result=bool)
    def action(self, name, values):
        self.error = ""
        try:
            handler = WORKSPACE_UI_ACTIONS.get(name)
            if handler is None:
                raise ValueError("工作区操作无效")
            handler(self, values)
        except Exception as exc:
            self.error = str(exc)
            self.changed.emit()
            return False
        self.changed.emit()
        return True

    def _ui_create(self, values):
        from tingmu.workspace_text import enable_global_text
        from tingmu.models import AppSettings
        self._idle()
        signature = values.get("textReuseSignature")
        if signature and self.textReuseOffer()["signature"] != signature:
            raise ValueError("文本连接已变更，请重新打开创建窗口核对。")
        initial = AppSettings(cloud_text_enabled=False, auto_prepare_media=True)

        def reuse_text(directory, settings):
            enable_global_text(self.bridge.store.data_dir, self.bridge.settings_snapshot(),
                               settings, allow_remote=True, expected_source=signature)

        workspace_id = self.catalog.create(values["name"], values["source"], values["target"],
                                           values.get("parent") or None, initial_settings=initial,
                                           configure=reuse_text if signature else None)
        self._enter_manual_selection(workspace_id, True)

    def _ui_switch(self, values):
        self._idle()
        self._enter_manual_selection(values["id"], False)

    def _enter_manual_selection(self, workspace_id, configure_models):
        self.items = self.catalog.summaries()
        if workspace_id != self.workspace_id:
            self.switch(workspace_id, self.initial_path, configure_models)

    def _ui_languages(self, values):
        self.bridge.set_languages(values["source"], values["target"])

    def _ui_discover(self, values):
        self.catalog.discover()
        self.refresh()

    def _ui_enter(self, values):
        self.catalog.select(self.workspace_id)
        if self.initial_path:
            path = Path(self.initial_path)
            if path.is_dir():
                self.bridge.add_paths([str(path)])
            else:
                self.bridge.open_media(str(path))
            self.initial_path = None

    def _ui_download(self, values):
        self._download_assets("download", values)

    def _ui_download_speech(self, values):
        self._download_assets("downloadSpeech", values)

    def _download_assets(self, kind, values):
        settings = self.bridge.settings_snapshot()
        model = settings.asr_model
        if kind == "download" and values.get("model", model) != model:
            raise ValueError("所选模型已改变，请重新确认下载。")
        engine_model, device = (model, settings.device) if kind == "download" else ("small", "cpu")
        self._check_model_runtime(engine_model, device)

        def complete(result):
            self.bridge.publish_state(status=f"{result} 模型文件已就绪")
            self._runtime_updated()

        def fetch(progress, cancel):
            self.runtime_setup.check_before_transfer(engine_model, device)
            if kind == "download":
                return download_model(model, progress, cancel)
            from tingmu.speech_assets import install
            install(cache_root(), progress=progress, cancelled=cancel)
            return "人声分离与换人检测"

        self.bridge.start_task("model-download", fetch, complete)

    def _ui_stop_download(self, values):
        self.bridge.cancel_task("model-download")

    @staticmethod
    def _open_folder(path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _ui_folder(self, values):
        self._open_folder(self.bridge.store.data_dir)

    def _ui_cache(self, values):
        self._open_folder(cache_root())

    def _ui_model_folder(self, values):
        path = confined_path(cache_root(), "models")
        path.mkdir(parents=True, exist_ok=True)
        self._open_folder(path)

    def _ui_media_folder(self, values):
        self._open_folder(self.bridge.store.data_dir / "media")


# Trusted desktop actions only. This declaration is not an Agent tool schema;
# method names supplied by callers can never select arbitrary controller methods.
WORKSPACE_UI_ACTIONS = MappingProxyType({
    "create": WorkspaceController._ui_create,
    "switch": WorkspaceController._ui_switch,
    "languages": WorkspaceController._ui_languages,
    "discover": WorkspaceController._ui_discover,
    "enter": WorkspaceController._ui_enter,
    "download": WorkspaceController._ui_download,
    "downloadSpeech": WorkspaceController._ui_download_speech,
    "stopDownload": WorkspaceController._ui_stop_download,
    "folder": WorkspaceController._ui_folder,
    "cache": WorkspaceController._ui_cache,
    "modelFolder": WorkspaceController._ui_model_folder,
    "mediaFolder": WorkspaceController._ui_media_folder,
})
