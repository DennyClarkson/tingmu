"""Validated workspace preferences with explicit model, playback and UI ports."""

from dataclasses import dataclass, fields, replace
from typing import Any, Callable
import math

from tingmu.models import AppSettings, MAX_LLM_CONCURRENCY
from tingmu.locale_catalog import display_language
from tingmu.workspaces import language_code
from .localization import ui_text
from .state import setting_domain


@dataclass
class SettingsState:
    lesson_model_status: dict | None = None


@dataclass(frozen=True)
class SettingsPorts:
    dialogs: Any
    document: Callable
    error: Callable
    lessonModelChanged: Any
    load_document: Callable
    load_personal: Callable
    log: Any
    model_registry: Callable
    player_position: Callable
    refresh_diagnostics: Callable
    refresh_library: Callable
    replace_settings: Callable
    resume_inbox: Callable
    set_audio_output: Callable
    set_diagnostic_timer: Callable
    set_document: Callable
    set_languages: Callable
    set_state: Callable
    setting: Callable
    settings: Callable
    settingsChanged: Any
    start_job: Callable
    stop_model_jobs: Callable
    store: Any
    sync_cue: Callable
    task_keys: Callable


class SettingsController:
    def __init__(self, state: SettingsState, ports: SettingsPorts):
        self.state = state
        self.ports = ports

    def setting(self, name, value):
        allowed = {f.name: f for f in fields(AppSettings) if not f.name.startswith("_")}
        if name not in allowed:
            return
        self.ports.set_state(domain=setting_domain(name), error="")
        try:
            before = getattr(self.ports.settings(), name)
            if name == "audio_output_id":
                self.ports.set_audio_output(str(value))
                return
            if name in ("source_language", "target_language"):
                source = value if name == "source_language" else self.ports.settings().source_language
                target = value if name == "target_language" else self.ports.settings().target_language
                self.ports.set_languages(source, target)
                return
            if isinstance(before, bool):
                value = value if isinstance(value, bool) else str(value).lower() == "true"
            elif isinstance(before, int):
                value = int(value)
            elif isinstance(before, float):
                value = float(value)
                if not math.isfinite(value):
                    raise ValueError("请输入有效数字")
            else:
                value = str(value).strip()
                if name == "teaching_preferences":
                    if len(value) > 6000 or "\x00" in value:
                        raise ValueError("讲解偏好最多 6000 字符，不能包含空字符")
                elif name not in (
                    "asr_model",
                    "asr_endpoint",
                    "external_asr_model",
                    "asr_key_env",
                    "local_asr_endpoint",
                    "local_asr_api_model",
                    "local_asr_key_env",
                ):
                    value = value[:200]
            if name in (
                "asr_endpoint",
                "external_asr_model",
                "asr_key_env",
                "asr_backend",
                "asr_response_format",
                "local_asr_endpoint",
                "local_asr_api_model",
                "local_asr_key_env",
                "local_asr_response_format",
            ):
                from tingmu.external_asr import validate_speech_setting

                value = validate_speech_setting(name, value)
                if name in ("asr_backend", "asr_endpoint") and value != before:
                    self.ports.settings().allow_audio_upload = False
                if name in ("asr_backend", "asr_endpoint", "local_asr_endpoint") and value != before:
                    self.ports.set_state(speechModelChoices=[])
            limits = {
                "subtitle_gap_ms": (0, 1500),
                "subtitle_offset_ms": (-600000, 600000),
                "overlay_opacity": (0.15, 1.0),
                "overlay_font_size": (14, 48),
                "audio_track": (0, 31),
                "llm_max_concurrency": (1, MAX_LLM_CONCURRENCY),
            }
            if name in limits:
                value = max(limits[name][0], min(limits[name][1], value))
            if name == "theme" and value not in ("forest", "paper", "mist", "graphite"):
                raise ValueError("请选择可用主题")
            if name == "preparation_workflow" and value not in ("classic", "subtitle_agent"):
                raise ValueError("请选择可用的字幕处理流程")
            if name == "companion_reasoning_effort" and value not in (
                "default",
                "none",
                "minimal",
                "low",
                "medium",
                "high",
                "xhigh",
            ):
                raise ValueError("请选择可用的思考强度")
            if name in ("review_model", "companion_model", "model") and (
                not value.strip() or len(value) > 200
            ):
                raise ValueError("请输入有效的模型 ID")
            setattr(self.ports.settings(), name, value)
            self.ports.store.save_settings(self.ports.settings())
            if name == "auto_prepare_media" and value:
                self.ports.resume_inbox()
            self.ports.settingsChanged.emit()
            self.ports.set_state(cloudEnabled=self.ports.settings().cloud_text_enabled)
            if name in ("cloud_text_enabled", "text_models_enabled") and before and not value:
                self.ports.stop_model_jobs()
            self.ports.log.event("setting_changed", "设置已更新", "DEBUG", setting=name)
            if name == "developer_mode":
                self.ports.log.set_developer(value)
                self.ports.set_diagnostic_timer(value)
                self.ports.log.event("developer_mode", "开发者模式已开启" if value else "开发者模式已关闭")
                self.ports.refresh_diagnostics()
            if name in ("level", "audio_track"):
                self.ports.load_document()
                self.ports.load_personal()
                self.ports.refresh_library()
            elif name == "subtitle_gap_ms":
                self.ports.set_document(self.ports.document())
            elif name == "subtitle_offset_ms":
                self.ports.sync_cue(self.ports.player_position())
        except (ValueError, TypeError) as exc:
            self.ports.error(exc, domain=setting_domain(name))

    def set_languages(self, source, target):
        source, target = language_code(source), language_code(target)
        if (source, target) == (self.ports.settings().source_language, self.ports.settings().target_language):
            return
        updated = replace(self.ports.settings(), source_language=source, target_language=target)
        self.ports.store.save_settings(updated)
        self.ports.replace_settings(updated)
        self.ports.settingsChanged.emit()
        self.ports.load_document()
        self.ports.load_personal()
        self.ports.refresh_library()

    def _read_lesson_model_status(self):
        from tingmu.agent import provider_status
        from tingmu.providers import ProviderError

        settings = self.ports.settings()
        try:
            enabled, profile = self.ports.model_registry().text_role(settings, "annotation")
            usable = enabled and (
                (profile.get("key_source") == "none" or profile.get("has_key", False))
                if profile
                else provider_status().get(settings.provider, {}).get("available", False)
            )
        except ProviderError:
            usable = False
        return {"configured": usable, "language": display_language(settings.source_language)}

    def _invalidate_lesson_model(self):
        self.state.lesson_model_status = None
        self.ports.lessonModelChanged.emit()

    def saveSpeechKey(self, secret):
        from tingmu.external_asr import api_options, save_audio_key

        try:
            endpoint = api_options(self.ports.settings())[0]
            save_audio_key(
                self.ports.store.data_dir,
                endpoint,
                secret.strip(),
                local=self.ports.settings().asr_backend == "local_api",
            )
            self.ports.set_state(
                domain="workspace",
                status="已保存工作区语音密钥" if secret else "已清除工作区语音密钥",
                error="",
            )
            return True
        except (OSError, RuntimeError, ValueError) as exc:
            self.ports.error(exc, domain="workspace")
            return False

    def chooseWhisperFolder(self):
        from tingmu.local_whisper import model_reference
        from tingmu.runtime import cache_root, confined_path

        try:
            base = confined_path(cache_root(), "models")
            base.mkdir(exist_ok=True)
            folder = self.ports.dialogs.getExistingDirectory(
                None, ui_text("选择共享缓存内的 CTranslate2 Whisper 模型"), str(base)
            )
            if folder:
                self.ports.setting("asr_model", model_reference(folder))
        except (OSError, ValueError) as exc:
            self.ports.error(exc, domain="workspace")

    def checkSpeechModels(self):
        from tingmu.external_asr import api_options, list_models

        settings = replace(self.ports.settings())
        if settings.asr_backend not in ("local_api", "external") or "speech-models" in self.ports.task_keys():
            return

        def complete(items):
            if (
                self.ports.settings().asr_backend == settings.asr_backend
                and api_options(settings)[0] == api_options(self.ports.settings())[0]
            ):
                self.ports.set_state(
                    domain="workspace",
                    speechModelChoices=items,
                    status=f"服务返回 {len(items)} 个模型，可选择或手填 ID。",
                )

        self.ports.set_state(
            domain="workspace", speechModelChoices=[], status="正在查询语音服务的模型列表…", error=""
        )
        self.ports.start_job(
            "speech-models",
            lambda progress, cancelled: list_models(settings, self.ports.store.data_dir),
            complete,
        )
