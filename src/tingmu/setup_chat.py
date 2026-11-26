"""A local conversation route for starting study before a text provider is configured."""

import re

from .app_actions import ACTION_LABELS, ActionError
from .learning_notes import language_name
from .locale_catalog import resolve_language
from .providers import RequestCancelled


_START = re.compile(
    r"(?:(?:(?:我(?:现在|接下来)?(?:要|想要|想|打算)|请帮我|帮我)(?:开始)?|(?=用))"
    r"(?:用\s*(?P<source>.{1,64}?)\s*(?:来)?)?学(?:习)?\s*(?P<target>.{1,64}?)(?:了)?|"
    r"i(?: want to|'d like to| would like to) (?:start )?(?:learn|study) (?P<english>.{1,64}?)"
    r"(?: (?:using|through|in|from) (?P<known>.{1,64}))?)",
    re.IGNORECASE,
)
_CREATE = re.compile(
    r"(?:(?:请|帮我|请帮我))?(?:新建|创建|建)(?:一个|个)?"
    r"(?:(?:名为|叫)\s*[「『“](?P<name>[^」』”]{1,80})[」』”]\s*的?)?(?:学习)?工作区[，,：:]\s*(?P<learn>.+)"
)


def setup_intent(text, source_language):
    """Only consume a whole, unambiguous request. Freeform instructions go to the configured Agent."""
    text = text.strip().rstrip("。！!.").strip()
    create = _CREATE.fullmatch(text)
    match = _START.fullmatch(create.group("learn") if create else text)
    if not match:
        return None
    known = match.group("source") or match.group("known")
    source = resolve_language(known) if known else source_language
    target = resolve_language(match.group("target") or match.group("english"))
    if not source or not target:
        return None
    intent = {"source": source, "target": target}
    if create:
        intent.update(create=True, name=create.group("name") or "")
    return intent


class SetupAssistant:
    def __init__(self, intent):
        self.intent = intent
        self.last_response_metadata = {}

    def reply(self, message, history, language, current_cue=None, context=None, *, tool_executor,
              on_tool_event=None, on_delta=None, cancelled=None, **kwargs):
        def run(action_name, **arguments):
            if cancelled and cancelled():
                raise RequestCancelled("设置已停止。")
            event = {"name": action_name, "label": ACTION_LABELS[action_name]}
            if on_tool_event:
                on_tool_event({**event, "status": "running"})
            try:
                result = tool_executor(action_name, arguments)
                if result.get("ok") is False:
                    raise ActionError(result.get("error") or "工作区设置未完成")
            except Exception:
                if on_tool_event:
                    on_tool_event({**event, "status": "error"})
                raise
            if on_tool_event:
                on_tool_event({**event, "status": "success"})
            return result

        def say(reply):
            if on_delta:
                on_delta(reply)
            return reply

        source, target = self.intent["source"], self.intent["target"]
        catalog = run("list_study_workspaces")
        matches = [row for row in catalog["workspaces"] if row["source"] == source and row["target"] == target]
        active = next((w for w in matches if w["id"] == catalog["current_workspace_id"]), None)
        if len(matches) > 1 and not active and not self.intent.get("create"):
            return say("你有多个符合这组语言/方言的工作区：" + "、".join(w["name"] for w in matches)
                       + "。请在左上角选一个，再告诉我你想学的语言/方言。")
        workspace = active or (matches[0] if matches else None)
        created = workspace is None or self.intent.get("create", False)
        if created:
            name = self.intent.get("name") or language_name(target) + "学习"
            used = {w["name"].casefold() for w in catalog["workspaces"]}
            if name.casefold() in used:
                if self.intent.get("name"):
                    return say(f"工作区「{name}」已经存在。要再建一个，请给它一个不同的名称。")
                base = name
                index = 2
                while f"{base} {index}".casefold() in used:
                    index += 1
                name = f"{base} {index}"
            workspace = run("create_study_workspace", name=name, source=source, target=target)["workspace"]
        ident = workspace["id"]
        if not workspace.get("auto_prepare", workspace.get("auto_prepare_media", False)):
            run("configure_study_workspace", workspace_id=ident, auto_prepare=True)
        advice = run("recommend_workspace_model", workspace_id=ident, preference="balanced")
        candidate = advice.get("recommendation")
        response = [f"已为你选好「{workspace['name']}」（{language_name(source)} → {language_name(target)}）。"]
        if workspace.get("asr_backend") in ("local_api", "external"):
            response.append(f"保留此工作区已配置的转写 API，模型为 {workspace['api_model']}。")
            if workspace["asr_backend"] == "external" and not workspace["audio_upload_allowed"]:
                response.append("如要使用这个远程服务，请先在工作区的语音设置中允许发送音轨。")
        elif candidate:
            model = candidate["model"]
            hw = advice.get("hardware", {})
            device = "CUDA 显卡" if candidate.get("device") == "cuda" else "CPU"
            reason = f"此机可用{device}识别" + (f"，有 {hw['cpu_threads']} 个逻辑处理器" if hw.get("cpu_threads") else "")
            response.append(f"推荐 {candidate.get('name') or model}。{candidate.get('rationale') or reason}。")
            applied = run("propose_workspace_model", workspace_id=ident, model=model)
            if applied.get("applied"):
                response.append("已启用共享目录里安装好的模型。")
            elif applied.get("requires_human_confirmation"):
                response.append("请核对下载窗口里的模型和大小，点按钮或在本对话回复「同意下载」后开始。")
        else:
            response.append("当前没有符合条件的内置语音模型。可在「工作区设置 → 语音识别」配置转写接口。")
        if created or (ident != catalog["current_workspace_id"] and not workspace.get("text_configured", False)):
            reuse = run("reuse_workspace_text_models", workspace_id=ident)
            if reuse.get("requires_human_confirmation"):
                response.append("允许此工作区使用远程文本模型后，听幕会为每句生成讲解，并核对已知语言/方言的译稿。")
            elif reuse.get("applied"):
                response.append("已在此工作区启用全局分配的本机文本连接。")
            elif not workspace.get("text_configured"):
                response.append("要生成逐句讲解和译稿，请在「工作区设置 → 文本模型与 API」分配讲解模型。")
        if ident != catalog["current_workspace_id"]:
            run("use_study_workspace", workspace_id=ident)
        response.append("把视频放进这个工作区的 media 文件夹，听幕会在复制完成、识别模型就绪后自动准备字幕。")
        if workspace.get("media_folder"):
            response.append(str(workspace["media_folder"]))
        return say("\n\n".join(response))
