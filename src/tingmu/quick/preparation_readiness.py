"""Offline facts for the preparation studio. Reading a report never starts a task."""

from dataclasses import fields, replace
from pathlib import Path
from urllib.parse import urlsplit

from tingmu.media_files import find_subtitle
from tingmu.models import AppSettings


def subtitle_source(scenario, workflow, job, current_path, language):
    if scenario in {"watch", "text", "caption"}:
        return scenario
    if scenario in {"asr", "speech"} or job.get("force_asr"):
        return "asr"
    if job.get("teaching_only") or job.get("reuse_existing_transcript"):
        return "caption"
    path = job.get("media_path") or job.get("path") or current_path
    if workflow == "subtitle_agent":
        # A selected file alone may be a translation, a different episode or an image track.
        counts = job.get("evidence_counts") or []
        if len(counts) == 3 and job.get("settings", {}).get("preparation_workflow") == workflow:
            return "caption" if counts[0] > 0 else "asr"
        return "evaluate"
    if job.get("subtitle_path") or (path and find_subtitle(Path(path), language)):
        return "caption"
    return "asr" if path else "choose"


def text_roles(settings, profiles, defaults):
    rows = {}
    for role, model_field in (("annotation", "model"), ("review", "review_model"),
                              ("companion", "companion_model")):
        ident = getattr(settings, role + "_profile_id")
        model = getattr(settings, model_field)
        if ident:
            profile = next((p for p in profiles if p["id"] == ident), {})
            model = profile.get("model", "")
            local = profile.get("is_loopback", False)
            endpoint = profile.get("base_url", "")
            host = urlsplit(endpoint).hostname or ""
            configured = bool(model and (profile.get("key_source") == "none" or profile.get("has_key")))
        else:
            provider = settings.provider if role == "annotation" else getattr(settings, role + "_provider")
            default = defaults.get(provider, {})
            local = False
            host = default.get("endpoint_host") or ""
            endpoint = host
            configured = bool(provider != "local" and default.get("available") and model)
        rows[role] = dict(id=ident, model=model, local=local, host=host, endpoint=endpoint,
                          configured=configured)
    return rows


def job_configuration(workspace, job):
    """Match the queue's saved spec, retry overrides and live consent intersection."""
    if not job.get("settings"):
        return workspace, False
    allowed = {f.name for f in fields(AppSettings) if not f.name.startswith("_")}
    snapshot = {**job["settings"], **(job.get("retry_text_settings") or {})}
    saved = AppSettings(**{key: value for key, value in snapshot.items() if key in allowed})
    keys = ("preparation_workflow", "segmentation_mode", "target_language", "source_language",
            "asr_backend", "device", "asr_model", "audio_track", "asr_endpoint", "local_asr_endpoint",
            "external_asr_model", "local_asr_api_model", "allow_audio_upload", "text_models_enabled",
            "cloud_text_enabled", "annotation_profile_id", "review_profile_id", "review_enabled",
            "provider", "model", "review_provider", "review_model")
    differs = any(getattr(saved, k) != getattr(workspace, k) for k in keys)
    effective = replace(saved,
                        text_models_enabled=saved.text_models_enabled and workspace.text_models_enabled,
                        cloud_text_enabled=saved.cloud_text_enabled and workspace.cloud_text_enabled,
                        allow_audio_upload=saved.allow_audio_upload and workspace.allow_audio_upload
                        and workspace.asr_backend == "external" and workspace.asr_endpoint == saved.asr_endpoint
                        and workspace.asr_key_env == saved.asr_key_env)
    return effective, differs


def readiness_rows(settings, source, roles, *, components, weights, language_supported, portable,
                   include_text=True, workspace=None):
    """Return stage-specific consequences and repair destinations, never an execution gate."""
    rows = []
    workspace = workspace or settings

    def add(code, action="", value=""):
        rows.append(dict(code=code, action=action, value=value))

    if source == "watch":
        return rows

    runtime_action = "runtime" if portable else "checkRuntime"
    if source == "asr":
        if settings.asr_backend == "local":
            qwen = settings.asr_model.lower().removeprefix("qwen/").startswith("qwen3-asr")
            engine = "qwen" if qwen else "local-asr"
            needs = ["ffmpeg", engine]
            if settings.device == "cuda" and not qwen:
                needs.append("whisper-cuda")
        else:
            needs = ["ffmpeg"]
        unknown = []
        for kind in needs:
            ready = components.get(kind, {}).get("ready")
            if ready is None:
                unknown.append(kind)
            elif not ready:
                add(kind, "runtime" if portable else "speech")
        if unknown:
            add("unchecked", runtime_action)
        if settings.asr_backend == "local":
            if not weights:
                add("weights", "speech", settings.asr_model)
            if not language_supported:
                add("language", "speech", settings.target_language)
        else:
            from tingmu.external_asr import api_options, validate_speech_setting
            endpoint, model, _, _ = api_options(settings)
            local = settings.asr_backend == "local_api"
            try:
                validate_speech_setting("local_asr_endpoint" if local else "asr_endpoint", endpoint)
                validate_speech_setting("local_asr_api_model" if local else "external_asr_model", model)
            except ValueError:
                add("apiConfig", "speech")
            if not local and not settings.allow_audio_upload:
                add("audioConsent", "speech")
            elif not any(r["code"] == "apiConfig" for r in rows):
                add("localApi" if local else "externalApi", "", urlsplit(endpoint).hostname or "")
    if not include_text:
        return rows

    def role_problem(role):
        row = roles[role]
        # A live permission click cannot widen the consent frozen in an existing job.
        same_role = getattr(workspace, role + "_profile_id") == getattr(settings, role + "_profile_id")
        if same_role and not row["id"]:
            provider_key, model_key = ("provider", "model") if role == "annotation" else (role + "_provider", role + "_model")
            same_role = (getattr(settings, provider_key), getattr(settings, model_key)) == (
                getattr(workspace, provider_key), getattr(workspace, model_key))
        if not settings.text_models_enabled:
            if workspace.text_models_enabled:
                return "frozen", ""
            grant = ("allowLocal" if row["local"] else "allowRemote") if row["configured"] and same_role else "text"
            return "off", grant
        if not row["local"] and not settings.cloud_text_enabled and row["configured"]:
            if workspace.cloud_text_enabled:
                return "frozen", ""
            return "remote", "allowRemote" if same_role else "text"
        if not row["configured"]:
            return "missing", "models"
        return "", ""

    teaching, action = role_problem("annotation")
    if teaching:
        add("teach_" + teaching, action, roles["annotation"]["model"])
    if settings.segmentation_mode == "llm_regroup":
        role = "review" if settings.review_profile_id else "annotation"
        problem, action = role_problem(role)
        if problem:
            add("smart_" + problem, action, roles[role]["model"])
    elif settings.review_enabled and (source != "caption" or settings.preparation_workflow == "classic"):
        problem, action = role_problem("review")
        if problem:
            add("review_" + problem, action, roles["review"]["model"])
    return rows
