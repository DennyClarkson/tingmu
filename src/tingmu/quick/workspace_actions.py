"""Declared Agent operations on workspace services, connections and indexed media."""

import hashlib
from urllib.parse import urlsplit

from tingmu.app_actions import ActionError
from tingmu.external_asr import validate_speech_setting
from tingmu.providers import ModelRegistry, loopback_url, validate_base_url


def _editable_settings(controller, args):
    ident = args["workspace_id"]
    if ident == controller.workspace_id and controller.bridge.task_keys() - {"chat"}:
        raise ActionError("请先结束当前工作区的处理任务，再更改模型。")
    return controller.settings_for(ident)


def configure_speech_api(controller, args):
    ident = args["workspace_id"]
    settings = _editable_settings(controller, args)
    local = args["backend"] == "local_api"
    field = "local_asr_endpoint" if local else "asr_endpoint"
    endpoint = validate_speech_setting(field, args["endpoint"])
    if not local and urlsplit(endpoint).scheme != "https":
        raise ActionError("远程转写请使用 HTTPS 服务地址。")
    model_key = "local_asr_api_model" if local else "external_asr_model"
    fmt_key = "local_asr_response_format" if local else "asr_response_format"
    setattr(settings, model_key, validate_speech_setting(model_key, args["model"]))
    setattr(settings, fmt_key, validate_speech_setting(fmt_key, args.get("format", "verbose_json")))
    if getattr(settings, field) != endpoint:
        setattr(settings, "local_asr_key_env" if local else "asr_key_env", "")
        settings.allow_audio_upload = False
    if settings.asr_backend != args["backend"]:
        settings.allow_audio_upload = False
    settings.asr_backend = args["backend"]
    setattr(settings, field, endpoint)
    controller.save_settings(ident, settings)
    return {
        "saved": True,
        "model": args["model"],
        "backend": settings.asr_backend,
        "audio_permission_required": not local and not settings.allow_audio_upload,
    }


def assign_local_text_model(controller, args):
    ident = args["workspace_id"]
    settings = _editable_settings(controller, args)
    base = validate_base_url(args["endpoint"], key_source="none", local_connection=True)
    if not loopback_url(base):
        raise ActionError("本机模型连接必须使用回环地址。")
    model = args["model"].strip()
    if not model:
        raise ActionError("请提供本机服务中的模型 ID。")
    registry = ModelRegistry(controller.catalog.directory(ident))
    existing = next(
        (
            p
            for p in registry.public_profiles()
            if (p["provider"], p["base_url"], p["model"]) == (args["provider"], base, model)
        ),
        None,
    )
    profile = existing or registry.save_profile(
        {"provider": args["provider"], "base_url": base, "model": model, "label": model, "key_source": "none"}
    )
    roles = ("annotation", "review", "companion") if args["role"] == "all" else (args["role"],)
    registry.assign(roles, profile["id"])
    registry.load_roles(settings)
    if not settings.text_models_enabled:
        settings.cloud_text_enabled = False
    settings.text_models_enabled = True
    if "review" in roles:
        settings.review_enabled = True
    controller.save_settings(ident, settings)
    controller.bridge.models.refresh()
    return {"saved": True, "roles": list(roles), "model": model, "connection_tested": False}


def rescan_media(controller, args):
    controller.bridge.scan_workspace_media()
    return {"scan_requested": True}


def open_media_folder(controller, args):
    return {"opened": controller.action("mediaFolder", {})}


def _indexed_media(controller):
    return {hashlib.sha256(row["path"].encode()).hexdigest()[:24]: row for row in controller.bridge.library}


def list_media(controller, args):
    media = _indexed_media(controller)
    offset = args.get("offset", 0)
    rows = [{"id": key, "name": row["name"], "status": row["status"]} for key, row in media.items()]
    return {"count": len(rows), "items": rows[offset : offset + 80]}


def open_media(controller, args):
    video = _indexed_media(controller).get(args.get("media_id"))
    if not video:
        raise ActionError("请先列出媒体库，选择其中一个视频。")
    controller.bridge.open_media(video["path"])
    return {"opened": True, "name": video["name"]}


MEDIA_ACTIONS = {
    "rescan": rescan_media,
    "open_folder": open_media_folder,
    "list": list_media,
    "play": open_media,
}


def workspace_media(controller, args):
    if args["workspace_id"] != controller.workspace_id:
        raise ActionError("请先进入要操作的工作区。")
    handler = MEDIA_ACTIONS.get(args["action"])
    if handler is None:
        raise ActionError("未知的工作区媒体操作。", "unknown_action")
    return handler(controller, args)


WORKSPACE_API_ACTIONS = {
    "configure_speech_api": configure_speech_api,
    "assign_local_text_model": assign_local_text_model,
    "workspace_media": workspace_media,
}


def apply_workspace_action(controller, name, args):
    handler = WORKSPACE_API_ACTIONS.get(name)
    if handler is None:
        raise ActionError("未知的工作区服务操作。", "unknown_action")
    controller.catalog.directory(args["workspace_id"])
    return handler(controller, args)
