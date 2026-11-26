# 模型连接与文本服务

听幕将全文复核、批量讲解和学习对话分给三个文本模型角色。可以共用同一连接，也可分别选用本机或远程服务。同一份安装里的所有工作区共享连接、凭据来源和角色分配。每个工作区单独决定允许发送哪些学习文本。远程语音转写另有音轨授权。[语音转写设置](external-asr.md)

## 建立连接并使用

1. 打开「文件 → 全局文本模型…」，选择「新建连接」及服务预设。给连接起一个能辨认服务和模型的名称，填写 API 基础地址。使用 Ollama、LM Studio 或本机兼容服务时，先在该服务内启动推理并加载模型。
2. 选密钥来源。免密本机服务可选「无需密钥」。需要认证时，填写环境变量**名称**，或用 Windows 本机加密保存手工密钥。模型 ID 可手填，也可先留空，点「保存连接」。
3. 在「工作区设置 → 文本模型与 API」选择适合此工作区的文本权限。要查询远程服务的模型列表，需先允许远程文本 API。返回连接页点「加载列表」，选择实际模型并再次「保存连接」。若服务没有实现列表接口，可按服务给出的完整模型 ID 手填。
4. 打开全局模型窗口左侧的「任务分配」，为所需角色选择已保存的连接。未指定自定义连接的角色显示「沿用默认设置」，沿用应用的提供商、模型及其凭据配置。删除连接会让所有使用它的角色恢复默认设置。

| 任务分配 | 使用场景 |
| --- | --- |
| 全文复核 | 开启工作区的「全文文本复核」时，对需要复核的原文作文本检查。字幕 Agent 已直接采用的学习语言/方言原文跳过此步，原始转写与时间保留。 |
| 批量讲解 | 按源句生成词句讲解，对需要补译的句子生成并另行核验译稿。字幕 Agent 接受配对已有译文时，仅为这些句子请求讲解。 |
| 学习对话 | 回答当前句问题，处理授权的学习操作和「纠正此句」。可在对话栏选择模型，角色分配仍对整份安装生效。 |

干净发行包的默认工作区首次启动时停用文本模型。新建工作区可以使用已分配的本机模型，也可在创建时确认允许全局分配的远程模型。升级旧版时，工作区连接会合并到全局模型库。各工作区原有分配不一致时，优先沿用上次打开的工作区中明确设置的角色。切换模型后，已有学习材料可继续使用。要重做时在队列明确选择范围。[便携版与队列操作](portable.md)

## 工作区权限

| 选项 | 工作区内的行为 |
| --- | --- |
| 停用文本模型 | 保留本地字幕与词典，暂停模型复核、讲解和对话。 |
| 仅本机 API | 只允许分配给角色的回环地址，例如 `127.0.0.1`、`localhost`、`[::1]`。若希望推理也完全在本机进行，请核对该服务加载的模型和请求转发设置。 |
| 允许远程文本 API | 可向已配置的服务发送相应任务所需的文本，例如复核与讲解使用的字幕，或问答涉及的当前句、对话、收藏与笔记摘录。 |

收紧权限时，听幕会停止相关模型任务。语音识别设置独立生效，只有显式选择远程转写服务并勾选「允许此工作区发送音轨」，该服务才可接收所选音轨。

## 服务预设与协议

下表列出当前应用内的基础地址和模型发现方式。使用自有服务或不同地域的账号时，填写服务提供的实际地址。模型是否能生成、能否使用工具和推理参数，取决于账号和具体模型。[连接与协议实现](../src/tingmu/providers.py)

| 预设 | 初始基础地址 | 文本请求及模型发现 |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1` | 默认 Chat Completions，可显式选择 Responses。模型列表使用 `GET /models`。 |
| DeepSeek | `https://api.deepseek.com` | Chat Completions，`GET /models`。 |
| 通义千问（北京） | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 兼容 Chat Completions。列表从同一主机的原生 `/api/v1/models` 分页读取。 |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | 兼容 Chat Completions，`GET /models`。 |
| Anthropic | `https://api.anthropic.com/v1` | 原生 Messages，模型列表支持 `after_id` 分页。 |
| Ollama | `http://127.0.0.1:11434/v1` | 本机兼容端点，`GET /models`。 |
| LM Studio | `http://127.0.0.1:1234/v1` | 可选择 Chat Completions 或服务支持的 Responses，`GET /models`。 |
| 本机 OpenAI 兼容服务 | `http://127.0.0.1:8000/v1` | 可指定回环端口与路径，支持免密或认证，可选择服务支持的 Chat Completions 或 Responses。 |
| 自定义 OpenAI 兼容 | 自行填写 | 指定服务实际支持的 Chat Completions 或 Responses 协议，支持手填模型 ID。 |

Chat Completions 用 SSE 接收增量。Anthropic 使用 `x-api-key`、独立的系统消息和原生工具内容块。选择 Responses 时，听幕向 `/responses` 发送 `store=false`，请求可见的 `reasoning.summary=auto`。服务实际返回摘要时才会显示。[OpenAI Responses](https://developers.openai.com/api/docs/guides/reasoning) · [Anthropic 流式事件](https://platform.claude.com/docs/en/build-with-claude/streaming) · [DeepSeek API](https://api-docs.deepseek.com/api/create-chat-completion/) · [千问兼容接口](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope) · [Gemini 兼容接口](https://ai.google.dev/gemini-api/docs/openai)

本机连接绕过系统代理。基础地址禁止携带用户名、密码、查询参数或片段。远程连接要求 HTTPS。使用密钥的回环 HTTP 服务应选择 Ollama、LM Studio 或本机 OpenAI 兼容预设。听幕拒绝 HTTP 重定向，凭据只传往配置的地址。[Ollama 接口](https://docs.ollama.com/api/openai-compatibility) · [LM Studio 接口](https://lmstudio.ai/docs/developer/openai-compat)

## 凭据、推理设置与对话

**凭据。** 全局公开配置和加密凭据位于安装数据目录的 `model-profiles.json`。手工密钥使用当前 Windows 用户的 DPAPI 保护，换电脑或 Windows 用户后需重输。编辑同一连接时，留空密钥框会保留已有密钥。修改使用手工密钥的服务地址或协议时需要重输。改用其他凭据来源会移除该连接的旧密文。选择环境变量时只保存名称，可读取进程、Windows 用户或系统环境中的值。非 Windows 开发环境用环境变量提供密钥。

**思考强度。** 可在连接的「高级选项」配置最大输出 token 数及推理强度。在聊天输入栏选择「默认 · 沿用模型设置」或覆盖本次请求的强度。编辑、重试会保留发起时的模型、语种及强度。服务拒绝参数时，在该连接上改为模型支持的档位或默认设置。模型供应商自定义的映射如下。

| 协议或服务 | 应用发送的参数 |
| --- | --- |
| 兼容 Chat Completions | `reasoning_effort`。DeepSeek 还使用 `thinking` 开关，关闭时省略 `reasoning_effort`。 |
| 通义千问 | `enable_thinking`，开启时按最低、低、中、高、更高分别设置 `thinking_budget` 为 1024、4096、8192、16384、32768。 |
| Anthropic | 显式开启时使用 adaptive thinking 和 `output_config.effort`，最低对应 `low`。关闭时设置 `thinking.type=disabled`。 |
| Responses | `reasoning.effort`，同时请求可见摘要。 |

**公开思考与摘要。** 服务公开返回的 Chat Completions `reasoning_content`、Anthropic `thinking` 文本及 Responses 思考摘要，在聊天中单独折叠显示，可展开、复制和随会话保存。每条消息最多保留 60,000 字符。签名、遮盖或加密的内容仅供当前工具轮往返，排除在界面、持久化记录和日志之外。显示的计时按收到公开片段的时间累积。[会话保存实现](../src/tingmu/conversations.py)

**学习操作。** 对话伴侣可在当前工作区处理视频和队列、控制播放、调整已有模型或学习偏好，也可按要求整理和编辑笔记。请求绑定发起时的媒体、语种、选中范围及模型。工具调用由应用验证名称、参数和范围，返回实际处理状态。服务明确拒绝原生工具时，听幕使用同一组受限操作校验 JSON 计划，再将执行结果交给模型生成最终回答。可执行操作与界面状态以应用返回为准。[应用操作](../src/tingmu/app_actions.py) · [对话实现](../src/tingmu/companion.py)

## 常见问题

- **列表查到了模型，生成时仍报错。** 先看任务卡提示，再核对角色分配、工作区权限、连接地址、模型 ID、凭据或余额。模型发现与生成由服务分别授权。
- **导入双语字幕后提示需要模型。** 字幕 Agent 会保留能配对的译文，但依然需要逐句讲解。检查「批量讲解」分配的连接能否在该工作区使用，配置后点原任务的「重试」。
- **批量处理被限流。** 在工作区的「文本模型与 API」或队列齿轮中降低并发，按提示等待后重试。已通过的字幕批次会从检查点恢复。
- **关心哪些服务做过实测。** 仓库保存了 DeepSeek Flash 公开思考、GPT 摘要与工具往返的小样本记录。其他预设能否在自己的服务上工作，应以实际调用为准。[历史接口验收](../reports/v5-reasoning-live.json) · [版本交接与验证范围](../handoff.md)
