"""First-run capability checks using the same clients and permissions as preparation."""
from tingmu import runtime_deps
from tingmu.external_asr import check_transcription
from tingmu.llm_client import LlmClient
from tingmu.model_advisor import model_supports_language
from tingmu.runtime import installation_root
from tingmu.workspace_models import weight_readiness


def check_speech(mode, settings, data_dir, cancelled):
    local = mode == "local"
    expected = "local" if local else "external"
    if settings.asr_backend != expected:
        raise ValueError("语音识别方式已更改，请重新选择处理方式。")
    components = ["ffmpeg"]
    if local:
        if not model_supports_language(settings.asr_model, settings.target_language):
            raise ValueError("所选模型不支持当前学习语言，请更换语音模型。")
        weights = weight_readiness(settings.asr_model)
        if not weights.ready:
            raise ValueError(weights.message)
        qwen = settings.asr_model.lower().removeprefix("qwen/").startswith("qwen3-asr")
        components.append("qwen" if qwen else "local-asr")
        if settings.device == "cuda" and not qwen:
            components.append("whisper-cuda")
    for kind in components:
        status = runtime_deps.probe(installation_root(), kind, cancelled=cancelled)
        if not status["ready"]:
            return "runtime", f"{kind} 未就绪。{status['detail']}"
    if not local and not cancelled():
        check_transcription(settings, data_dir)
    return "", ""


def check_text(settings, registry, cancelled):
    roles = [("annotation", "逐句讲解"), ("companion", "学习伴侣")]
    if settings.review_enabled or (settings.segmentation_mode == "llm_regroup" and settings.review_profile_id):
        roles.append(("review", "字幕复核"))
    clients = {}
    for role, label in roles:
        allowed, profile = registry.text_role(settings, role)
        if not allowed:
            raise ValueError(f"请为「{label}」分配文本模型，并在当前工作区允许使用该连接。")
        provider = settings.provider if role == "annotation" else getattr(settings, role + "_provider")
        model = settings.model if role == "annotation" else getattr(settings, role + "_model")
        if profile and profile["key_source"] != "none" and not profile["has_key"]:
            raise ValueError(f"请在文本模型设置中为「{label}」保存密钥。")
        key = profile["id"] if profile else (provider, model)
        if key not in clients:
            clients[key] = (label, LlmClient(provider, model, profile=profile))
    for label, client in clients.values():
        if cancelled():
            return
        try:
            reply = client.complete("仅回复 OK。", {"text": "连接测试"}, cancelled=cancelled)
            if not reply.strip():
                raise ValueError("模型返回了空内容。")
        except (ValueError, RuntimeError, OSError) as exc:
            raise ValueError(f"「{label}」验证失败：{exc}") from exc
