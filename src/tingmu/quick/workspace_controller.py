"""Desktop workspace commands. Each open window owns exactly one study store."""

from pathlib import Path
from dataclasses import asdict
from copy import deepcopy
import hashlib
import json
import uuid

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from tingmu.runtime import cache_root, confined_path
from tingmu.workspaces import pair_label
from tingmu.workspace_text import TextSetupMixin, effective_text_roles, enable_global_text, text_reuse_offer
from tingmu.workspace_models import download_model, model_status, model_inventory


class WorkspaceController(TextSetupMixin, QObject):
    changed = Signal()
    _recommendationReady = Signal(str, object, str)

    def __init__(self, bridge, catalog, workspace_id, show_setup, switch, initial_path):
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
        self.proposal = {}
        self._text_setup_init()
        self.recommendations = {}
        self.pending_switch = ""
        self._switch_summary = ""
        self._switch_origin = None
        self._recommendationReady.connect(self._save_recommendation)
        self._switch_timer = QTimer(self)
        self._switch_timer.setInterval(200)
        self._switch_timer.timeout.connect(self._finish_switch)
        self._language_refresh_session = ""
        self._language_timer = QTimer(self)
        self._language_timer.setInterval(200)
        self._language_timer.timeout.connect(self._finish_language_change)
        self.bridge.settingsChanged.connect(self._update_languages)
        self.bridge._progress.connect(self._download_progress)

    def _update_languages(self):
        current = next(row for row in self.items if row["id"] == self.workspace_id)
        pair = self.bridge._settings.language
        label = pair_label(pair.source, pair.target)
        if label != current["languages"]:
            current["languages"] = label
            self.changed.emit()

    @Slot()
    def refresh(self):
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
        return deepcopy(self.bridge._settings) if workspace_id == self.workspace_id else self._store(workspace_id).load_settings()

    @staticmethod
    def _fingerprint(settings):
        values = {key: value for key, value in asdict(settings).items()
                  if "asr" in key or key in ("source_language", "target_language", "device", "allow_audio_upload")}
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()

    def _save_settings(self, workspace_id, settings):
        old_language = self.bridge._settings.language
        self._store(workspace_id).save_settings(settings)
        if workspace_id == self.workspace_id:
            self.bridge._settings = settings
            self.bridge.settingsChanged.emit()
            if old_language != settings.language:
                self.bridge._load_document()
                self.bridge._refresh_library()
                self.bridge._notebook_language = settings.target_language
                self.bridge._refresh_notebooks()
                # Retain the originating chat until the frozen reply completes.
                if "chat" not in self.bridge._workers:
                    self.bridge._activate_context_session()
                else:
                    self._language_refresh_session = self.bridge._active_session_id
                    self._language_timer.start()
        self.items = self.catalog.summaries()
        self.changed.emit()

    def _finish_language_change(self):
        if self.bridge._closed:
            self._language_timer.stop()
            return
        if "chat" in self.bridge._workers:
            return
        self._language_timer.stop()
        if self.bridge._active_session_id == self._language_refresh_session:
            self.bridge._activate_context_session()
        self._language_refresh_session = ""

    def _workspace_public(self, workspace_id):
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

    def recommend(self, args, context):
        """Read/probe on the requesting worker; publish results through a queued Qt signal."""
        from tingmu.model_advisor import recommend_models
        workspace_id = args["workspace_id"]
        settings = self._settings_for(workspace_id)
        result = recommend_models(settings, language=settings.target_language,
                                  preference=args.get("preference", "balanced"), offline=args.get("offline", False))
        def public_hardware(value):
            if isinstance(value, dict):
                return {k: public_hardware(v) for k, v in value.items() if k not in ("cache_path", "interpreter")}
            if isinstance(value, list):
                return [public_hardware(v) for v in value]
            return value
        result = {**result, "hardware": public_hardware(result.get("hardware", {})), "workspace_id": workspace_id}
        self._recommendationReady.emit(workspace_id, result, self._fingerprint(settings))
        return result

    @Slot(str, object, str)
    def _save_recommendation(self, workspace_id, result, fingerprint):
        self.recommendations[workspace_id] = {**deepcopy(result), "settings_fingerprint": fingerprint}
        self.changed.emit()

    def handle_agent_action(self, name, args, context):
        from tingmu.app_actions import ActionError
        from tingmu.workspaces import language_code
        if context.workspace_id and context.workspace_id != self.workspace_id:
            raise ActionError("请求属于另一个学习工作区，请在原工作区继续。", "workspace_changed")
        if name == "list_study_workspaces":
            return {"workspaces": [self._workspace_public(row["id"]) for row in self.catalog.summaries()],
                    "current_workspace_id": self.workspace_id}
        if name == "create_study_workspace":
            from tingmu.models import AppSettings
            settings = AppSettings(source_language=args["source"], target_language=args["target"],
                                   level=args.get("level", "beginner"), teaching_preferences=args.get("preferences", ""),
                                   auto_prepare_media=True, cloud_text_enabled=False)
            workspace_id = self.catalog.create(args["name"], args["source"], args["target"], initial_settings=settings)
            return {"created": True, "workspace": self._workspace_public(workspace_id)}
        workspace_id = args["workspace_id"]
        self.catalog.directory(workspace_id)
        if name == "reuse_workspace_text_models":
            return self.propose_text_models(workspace_id, context)
        if name == "configure_study_workspace":
            if workspace_id == self.workspace_id and set(self.bridge._workers) - {"chat"}:
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
                self.bridge._resume_inbox()
            return {"saved": True, "workspace": self._workspace_public(workspace_id), "applies_to": "future_materials"}
        if name == "get_workspace_setup":
            return {"workspace": self._workspace_public(workspace_id),
                    "proposal": deepcopy(self.proposal) if self.proposal.get("workspace_id") == workspace_id else {},
                    "switch_pending": self.pending_switch == workspace_id}
        if name == "propose_workspace_model":
            result = self.recommendations.get(workspace_id, {})
            settings = self._settings_for(workspace_id)
            if result.get("settings_fingerprint") != self._fingerprint(settings):
                raise ActionError("请先按最新工作区设置检查电脑并推荐模型。", "recommendation_stale")
            candidate = next((item for item in result.get("candidates", []) if item["model"] == args["model"]), None)
            if not candidate or not candidate.get("available"):
                raise ActionError("此模型不可用于当前电脑，请选择本次推荐中的可用模型。", "model_unavailable")
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
        if name == "use_study_workspace":
            if workspace_id == self.workspace_id:
                return {"deferred": False, "already_active": True, "workspace_id": workspace_id}
            self.pending_switch = workspace_id
            self._switch_origin = ((context.session_id, self.bridge._messages[-1]["id"])
                                   if "chat" in self.bridge._workers and self.bridge._messages
                                   and self.bridge._active_session_id == context.session_id else None)
            self._switch_summary = "已进入学习工作区「" + self._workspace_public(workspace_id)["name"] + "」。上一轮完整回复保留在原工作区；此处的聊天、媒体与学习资料独立保存。请将视频放入此工作区的 media 文件夹。"
            self._switch_timer.start()
            self.changed.emit()
            return {"deferred": True, "workspace_id": workspace_id, "reason": "等待本轮回复、下载确认和后台任务结束后进入；当前回复仍保存在原工作区"}
        raise ActionError("未知的学习工作区操作。", "unknown_action")

    @Slot(str, bool, result=bool)
    def decideTextReuse(self, ident, approved):
        return self.decide_text_reuse(ident, approved)

    @Slot(str, object)
    def _download_progress(self, key, text):
        if key == "model-download" and self.proposal.get("status") == "downloading":
            if self.proposal.get("progress") != str(text):
                self.proposal["progress"] = str(text)
                self.changed.emit()

    @Slot(str, result="QVariantMap")
    def downloadPlan(self, kind):
        try:
            if kind == "downloadSpeech":
                from tingmu.speech_assets import ARCHIVE_BYTES, planned_download_bytes
                size = planned_download_bytes(cache_root())
                return {"kind": kind, "label": "人声分离与换人检测", "bytes": float(size),
                        "packageBytes": float(sum(ARCHIVE_BYTES.values()))}
            model = self.bridge._settings.asr_model
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
            if proposal["origin_workspace_id"] != self.workspace_id or proposal["origin_session_id"] != self.bridge._active_session_id:
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
            if "model-download" in self.bridge._workers:
                raise ValueError("已有模型下载正在执行")
            proposal.update(status="downloading", error="", progress="已确认，开始下载")
            frozen = deepcopy(proposal)
            def fetch(progress, cancel):
                try:
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
                        self.bridge._set_state(status="模型已就绪，可将视频放入工作区 media 文件夹")
                if self.proposal.get("error"):
                    self.bridge._set_state(error=self.proposal["error"])
                    if self.pending_switch == frozen["workspace_id"]:
                        self.pending_switch = ""
                self.readiness = ""
                self.changed.emit()
            self.bridge._start_job("model-download", fetch, complete)
        except Exception as exc:
            self.error = str(exc)
            if proposal.get("status") == "downloading" and "model-download" not in self.bridge._workers:
                proposal.update(status="failed", error=self.error)
            if proposal.get("status") in ("failed", "stale") and self.pending_switch == proposal.get("workspace_id"):
                self.pending_switch = ""
            self.changed.emit()
            return False
        self.changed.emit()
        return True

    def _finish_switch(self):
        if not self.pending_switch or self.bridge._closed:
            self._switch_timer.stop()
            return
        if self.bridge._workers or self.proposal.get("status") in ("pending", "downloading") or self._text_setup_pending():
            return
        if self._switch_origin:
            session_id, message_id = self._switch_origin
            rows = self.bridge.conversations.load(session_id)["messages"]
            reply = next((row for row in rows if row["id"] == message_id), {})
            if reply.get("status") in ("stopped", "error"):
                self.pending_switch = ""
                self.error = "本轮回复已停止或失败，已保留当前工作区；可重新请求进入。"
                self._switch_timer.stop()
                self.changed.emit()
                return
        workspace_id = self.pending_switch
        self.pending_switch = ""
        self._switch_timer.stop()
        try:
            workspace = self._workspace_public(workspace_id)
            candidate = next((item for item in self.recommendations.get(workspace_id, {}).get("candidates", [])
                              if item["model"] == workspace["model"]), {})
            reasons = candidate.get("rationale", "")
            speech = (candidate.get("name", workspace["model"]) + " · " + workspace["device"] + " · " + workspace["speech_status"]
                      if workspace["asr_backend"] == "local" else "转写 API · " + workspace["api_model"])
            self.bridge._workspace_switch_summary = (
                self._switch_summary + "\n\n识别模型：" + speech
                + ("\n推荐依据：" + reasons if reasons else "")
                + "\n\n应用资料目录下的视频文件夹：" + workspace["media_folder"]
                + "\n可在「工作区设置 → 语言与文件」点「打开视频目录」。复制完成后会按本工作区设置自动准备字幕。")
            self.switch(workspace_id, None, False)
        except Exception as exc:
            self.error = str(exc)
            self.changed.emit()

    @Slot(str, result=str)
    def readinessFor(self, model):
        if self.model != model or not self.readiness:
            self.model = model
            self.readiness = model_status(model)
        return self.readiness

    def _idle(self):
        if self.bridge._workers:
            raise ValueError("请等待后台任务完成，或暂停正在处理的任务后切换工作区")

    @Slot(result="QVariantMap")
    def textReuseOffer(self):
        return text_reuse_offer(self.bridge.store.data_dir, self.bridge._settings)

    @Slot(str, "QVariantMap", result=bool)
    def action(self, name, values):
        self.error = ""
        try:
            if name in ("create", "switch"):
                self._idle()
                if name == "create":
                    from tingmu.models import AppSettings
                    signature = values.get("textReuseSignature")
                    if signature and self.textReuseOffer()["signature"] != signature:
                        raise ValueError("文本连接已变更，请重新打开创建窗口核对。")
                    initial = AppSettings(cloud_text_enabled=False, auto_prepare_media=True)
                    def reuse_text(directory, settings):
                        enable_global_text(self.bridge.store.data_dir, self.bridge._settings,
                                                settings, allow_remote=True, expected_source=signature)

                    workspace_id = self.catalog.create(values["name"], values["source"], values["target"],
                                                       values.get("parent") or None, initial_settings=initial,
                                                       configure=reuse_text if signature else None)
                else:
                    workspace_id = values["id"]
                self.items = self.catalog.summaries()
                if workspace_id != self.workspace_id:
                    self.switch(workspace_id, self.initial_path, name == "create")
            elif name == "languages":
                self.bridge.set_languages(values["source"], values["target"])
            elif name == "discover":
                self.catalog.discover()
                self.refresh()
            elif name == "enter":
                self.catalog.select(self.workspace_id)
                if self.initial_path:
                    path = Path(self.initial_path)
                    if path.is_dir():
                        self.bridge.add_paths([str(path)])
                    else:
                        self.bridge.open_media(str(path))
                    self.initial_path = None
            elif name in ("download", "downloadSpeech"):
                model = self.bridge._settings.asr_model
                if name == "download" and values.get("model", model) != model:
                    raise ValueError("所选模型已改变，请重新确认下载。")
                def complete(result):
                    self.readiness = ""
                    self.bridge._set_state(status=f"{result} 模型文件已就绪")
                    self.changed.emit()
                def fetch(progress, cancel):
                    if name == "download":
                        return download_model(model, progress, cancel)
                    from tingmu.speech_assets import install
                    install(cache_root(), progress=progress, cancelled=cancel)
                    return "人声分离与换人检测"
                self.bridge._start_job("model-download", fetch, complete)
            elif name == "stopDownload":
                worker = self.bridge._workers.get("model-download")
                if worker:
                    worker[1].set()
            elif name in ("folder", "cache", "modelFolder", "mediaFolder"):
                if name == "mediaFolder":
                    path = self.bridge.store.data_dir / "media"
                elif name == "modelFolder":
                    path = confined_path(cache_root(), "models")
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path = cache_root() if name == "cache" else self.bridge.store.data_dir
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
            else:
                raise ValueError("工作区操作无效")
        except Exception as exc:
            self.error = str(exc)
            self.changed.emit()
            return False
        self.changed.emit()
        return True
