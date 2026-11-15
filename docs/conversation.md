# 对话式学习伴侣

对话伴侣与批量字幕分析分开。字幕分析仍可使用 `gpt-5.4-mini`；持续问答默认使用 OpenAI `gpt-6-astra`，也可明确选择 `gpt-6.1-sol`。不会自动降级到小模型或其他提供商。

## 模型与实测

2026-11-14 从本机已授权 OpenAI `/models` 实际获取到 `gpt-6-astra`、`gpt-6.1-sol`（另有较早 Sol 型号，但当前伴侣只提供前两者）。针对同一组合成粤语文本和真实软件功能说明，每个模型真实调用两次：首轮询问语言知识与播放/收藏操作，次轮去掉当前字幕，依靠前两条对话继续造句和笔记指导。

| 请求模型 / 返回模型 | 首轮耗时 | 后续轮耗时 | 两轮总 token | 完整回答 |
| --- | --- | --- | --- | --- |
| gpt-6-astra / gpt-6-astra | 7.157 秒 | 4.562 秒 | 2,274 | 2/2 |
| gpt-6.1-sol / gpt-6.1-sol | 7.438 秒 | 4.687 秒 | 2,441 | 2/2 |

测试使用 medium reasoning、每次最多 1,500 输出 token；实际应用默认每次最多 2,400。两者都结合上一轮教学给出作业例句，并说明播放速度、单句循环、收藏及笔记操作，没有声称替用户完成点击。完整合成输入、输出、实际模型 ID、延迟和计费 token 见 `reports/model-companion.json`。报告不含用户原片转写、媒体文件或本地完整路径。

此测试证明这两个账号模型可调用且该小样本的对话延续可用，不证明总体语言能力或粤拼准确率。回答中仍有一处读音拼写差异，未进行母语者听音评审。模型默认为用户要求的 Astra；Sol 是明确可选项，不根据这四次调用推断稳定速度排名。

官方模型页面：[GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra)、[GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol)。本功能采用它们支持的 Chat Completions 文本请求，无工具调用。模型可用性另经账号列表和实际请求验证。

## 接口

```python
from tingmu.companion import CompanionAgent
from tingmu.models import LanguagePair

agent = CompanionAgent(provider="openai", model="gpt-6-astra")
answer = agent.reply(
    message="刚才这个词还可以怎么用？",
    history=[{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}],
    language=LanguagePair("zh-CN", "yue", "beginner"),
    current_cue=None,  # 可传当前 Cue；仅提取学习文字，不发送原片
    context={"media_name": "demo.mkv", "cloud_text_enabled": True},
)
print(agent.last_response_metadata)  # 无密钥；含 requested_model / returned_model
```

UI 在后台工作线程调用，不阻塞播放器。历史由 Store 持久化、UI 传入；模型只接收最近至多 12 条、合计 12,000 字符的 user/assistant 记录，单条历史最多 2,400 字符，过长历史会截短。当前问题超过 6,000 字符则提示缩短。系统角色和工具记录不会从历史直接发送。对话源语言/学习语言可相同；粤语引文和新例句采用繁体，普通话解释默认简体。

构造时显式模型优先；省略时读取 `TINGMU_COMPANION_MODEL` 或 `OPENAI_COMPANION_MODEL`，否则 Astra。它不继承批量分析的 `OPENAI_MODEL`。应用设置中的 `companion_provider` / `companion_model` 由 UI 明确传入；凭据仍复用安全环境读取，不保存密钥。

## 文本范围与软件指导

上下文采用白名单：选中文件的 basename、播放时间/速度/音轨/字幕延迟、播放和云端开关、字幕状态、语言/级别、当前可用控件名。完整路径、任意嵌套设置、API 密钥等字段不发送。当前字幕只发送文字、原文字、翻译、解释、不确定标记及最多 50 个词条，均有长度限制。调用设置 `store=False`；应用本地的聊天保存由 Store 管理。

关闭云端文本时 UI 不应发起对话，适配层收到 `cloud_text_enabled=False` 也会在联网前拒绝。服务错误、空回答、截断回答会显示错误，不伪装为另一个模型成功。不会上传音视频，不具备文件/播放器/笔记执行工具，因此只能指导用户操作，不能宣称已经改速、收藏、保存或听过声音。

功能指南来自当前 UI 控件与快捷键源码，并已与 UI 负责人核对最终标签：主窗口「伴侣」打开学习伴侣；底部「讲解 / 对话 / 笔记」分栏，「问伴侣」进入问答，「对话」输入后点「发送」；词条收藏按钮是「收藏词」，笔记在「笔记」页点「保存笔记」，「生词本」位于主窗口「更多」菜单。模型设置位于点击顶部语言摘要打开的「设置」面板，字幕模型与对话模型分开。ASR 文本和历史中的指令都被当作数据；模型没有任何执行通道。乱码、人名或普通话化转写只能依据文字解释或建议回听，不能把猜测当成音频事实。词典或模型给出的音标仍需结合原声核对。

## 验证

8 项测试覆盖强模型默认与专用环境覆盖、无降级、历史延续与长度限制、角色过滤、安全上下文、关闭云端/空问题/超长问题不联网、错误处理以及字幕指令不提升权限。运行：

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -p test_companion.py -v
.venv/Scripts/python.exe -m ruff check src/tingmu/companion.py tests/test_companion.py
```

已全部通过。真实模型测试之后仅对齐了最终 UI 标签和繁体粤语例句提示，未为这些措辞调整额外发起收费测试。


## Qt Quick v3 操作指南更新

当前入口为独立聊天窗口，顶部“对话”打开，模型在聊天模块自己的齿轮设置。伴侣提示词已同步观看/准备布局、媒体多选队列、模块齿轮、短空隙衔接（不合并短句）及固定浮层右下角缩放。11项测试和31项标签子测试通过；真实Astra指南请求返回gpt-6-astra，10.25秒、2205reported tokens。证据reports/companion-v3-evaluation.json。上文较早测试作为历史记录，最新UI以docs/ui-v3.md为准。
