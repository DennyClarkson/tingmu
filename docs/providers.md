# 模型连接与文本服务（v0.5）

## v0.5 当前约定

对话可直接选择默认、关闭、最低、低、中、高、更高思考强度；默认继承模型连接，其他选择在本次请求中冻结，编辑/重试沿用原值。各模型支持范围不同，服务拒绝时给出明确提示，不会静默丢掉参数或偷偷换模型。

OpenAI、自定义兼容服务、Gemini 和 Ollama 发送 `reasoning_effort`；DeepSeek 同时对应 `thinking` 开关；千问使用 `enable_thinking` 和 `thinking_budget`（最低1024、低4096、中8192、高16384、更高32768）。Anthropic 显式思考使用 adaptive thinking 和 `output_config.effort`，最低映射 low；不支持 adaptive 的旧模型应使用默认或返回明确的不支持提示。实际支持能力由所选模型决定。原生签名 thinking 块仅留在当前工具往返内，不展示、不写入聊天和诊断。

协议依据：[DeepSeek 思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[Anthropic 思考与成本](https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost)、[Gemini 兼容接口](https://aistudio.google.com/docs/openai)、[千问深度思考](https://www.alibabacloud.com/help/en/model-studio/deep-thinking)。这些映射有协议回归；真实账号验收范围另见交接报告。

用户追加要求后，公开思考文本和签名／私有状态明确分开：Chat Completions 的 `reasoning_content`、Anthropic 公开 `thinking` 文本、Responses 的 `summary_text` 通过独立增量通道显示。签名、redacted thinking 和 encrypted content 仅供当前工具轮往返，既不显示也不持久化。公开文字默认折叠，可复制、停止后保留和重新打开历史；每条消息最多显示／保存60,000字符，保存时脱敏，不写入运行日志，也不作为下一轮用户正文。计时累计实际收到公开思考期间的时间，不代表服务端内部计算的精确耗时。

OpenAI 和自定义连接可显式选择 `protocol='responses'`，请求 `/responses` 与 `reasoning.summary='auto'`，映射结构化输出和原生工具调用，使用 `store=false`。统计状态与可见摘要分开：模型不一定每次返回摘要，不能把没有摘要当作无响应。原有连接继续 Chat Completions，不自动迁移。依据：[思考摘要及无服务端存储的推理](https://developers.openai.com/api/docs/guides/reasoning)、[Responses 流式事件](https://developers.openai.com/api/docs/guides/streaming-responses)、[工具结果回传](https://developers.openai.com/api/docs/guides/function-calling)。

公开思考小样本真实验证见 `reports/v5-reasoning-live.json`：既有 DeepSeek 环境连接返回 deepseek-flash 的连续思考片段；既有 OpenAI 环境连接返回 gpt-6-astra 的摘要片段，并完成两轮原生工具往返。报告保留请求模型和实际返回模型的区别，不保存地址、密钥、请求或思考正文；不据此声称所有模型或其他提供商均已验收。

Agent 现有23项应用操作，覆盖视频准备/明确重做、队列、播放、已有模型选择、显示设置、讲解偏好、工作区、收藏、整理、笔记编辑保存和复习。详见 `app_actions.py`，替代下文历史的六工具限制。当前媒体/语种/选中范围和模型指纹在发送时冻结，单次最多100视频；UI操作在主线程执行。处理任务返回排队/运行状态，不把“已接受”宣称为“已完成”。失败操作允许重试，成功保存防止同轮重复。读取笔记按实际返回页记录范围，完整读取并核对修订后才可覆盖或修改。字幕、笔记和工具返回始终是资料，不能授予操作权限。无任意系统命令、文件路径或密钥操作。

三个任务角色分别选择连接：批量讲解、全文复核、学习对话。未选连接时沿用旧的 provider/model 与环境变量配置；不会改动默认模型，也不会因切换模型自动重做已有学习材料。新建连接允许先留空模型名保存，再加载列表，最后选择或手填服务方给出的模型 ID。模型列表可用不等于生成权限或教学质量已经验证。

## 官方接口核对

2026-11-14 查阅以下官方文档；预设不塞入推测的新模型名称。所有调用仅传文字。

| 预设 | Base URL | 协议与发现 |
| --- | --- | --- |
| OpenAI | `https://api.openai.com/v1` | Chat Completions；`GET /models` |
| DeepSeek | `https://api.deepseek.com` | Chat Completions；`GET /models` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | Chat Completions；同主机原生 `/api/v1/models` 分页 |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai` | OpenAI 兼容 Chat Completions；`GET /models` |
| Anthropic | `https://api.anthropic.com/v1` | 原生 Messages；`GET /models`，`after_id` 分页 |
| Ollama | `http://127.0.0.1:11434/v1` | OpenAI 兼容，无密钥；`GET /models` |
| 自定义 | 用户填写 | OpenAI 兼容端点与模型 ID |

OpenAI 的请求参数、工具格式和模型发现分别按 [Chat API](https://developers.openai.com/api/reference/resources/chat) 与 [Models API](https://developers.openai.com/api/reference/resources/models/methods/list) 实现。DeepSeek 使用 [Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/) 和 [模型列表](https://api-docs.deepseek.com/api/list-models/)。兼容协议的流式响应读取 SSE `data` 与 `choices[].delta`，以 `[DONE]` 结束；忽略推理过程字段，不作为给用户的回答。

千问按 [OpenAI 兼容说明](https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope) 与 [模型列表](https://help.aliyun.com/zh/model-studio/list-models) 实现。旧 dashscope 域名仍可使用；官方推荐业务空间专属域名，例如 `https://实际WorkspaceId.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`。用户应按自己的地域/空间修改地址；程序不猜 Workspace ID。列表使用同一 origin 的 `/api/v1/models?page_no=...&page_size=100&capabilities=TG`，读取 `output.models[].model` 和 `output.total`。如账号/旧域名不提供列表，可直接手填模型 ID。

Gemini 的兼容端点、流式与模型列表按 [Google 官方兼容文档](https://ai.google.dev/gemini-api/docs/openai) 实现。Ollama 的本机端点和模型列表按 [Ollama OpenAI 兼容文档](https://docs.ollama.com/api/openai-compatibility) 实现；程序不下载或启动模型服务。

Anthropic 使用 [认证说明](https://platform.claude.com/docs/en/api/overview)、[模型列表](https://platform.claude.com/docs/en/api/models/list) 和 [流式文档](https://platform.claude.com/docs/en/build-with-claude/streaming)：`x-api-key` 与 `anthropic-version: 2023-06-01`，system 独立字段，文字/tool_use/tool_result 块互转；SSE 读取 message_start/content_block_delta/message_delta/message_stop，包含工具 JSON 分片；不把 thinking 块展示或存入对话。

## 凭据与本地保存

`ModelRegistry(data_dir)` 在应用自己的 `model-profiles.json` 保存公开配置和 DPAPI 密文。手工密钥由 Windows 当前用户 DPAPI 加密，不明文持久化；复制到其他电脑或用户后须重输。非 Windows 不提供不安全的手工保存替代，使用环境变量。空密钥编辑保留原密钥，切换凭据来源或删除连接移除原密文；更换手工密钥连接地址/协议须重输，避免把原凭据无意送往新端点。

环境变量保留进程、Windows 用户/系统范围读取及 `OpenAI`、`OPENAI`、`DEEPSEEK` 旧别名兼容。自定义环境变量仅保存变量名。解析后的凭据通过私有惰性函数提供给传输层，公开 profile、AppSettings、队列快照不含密钥。动态密钥注册到日志脱敏器；错误不带 HTTP 正文、请求头、凭据、完整路径。返回元数据只保留模型、数字 token 用量、结束原因、延迟与计数。

Base URL 禁止用户信息、查询、fragment、控制字符或反斜杠；远端须 HTTPS。HTTP 仅允许显式无密钥的 loopback（localhost、127.0.0.1、::1 等）服务。所有 HTTP 重定向均拒绝，不携带 Authorization/x-api-key 跟随任何重定向。JSON、SSE 帧与总响应均有限长检查。配置采用共享线程锁、同目录临时文件与原子替换，Windows 文件短时被占用会有限重试；旧文件不先删除。该锁覆盖同一进程的多个 Registry 实例，未提供多进程同时编辑合并。

## 协议与学习工具

`LearningAgent`、`TranscriptReviewer`、`CompanionAgent` 支持可选 `profile=`，旧 provider/model 调用保持可用。字幕任务优先 strict JSON schema（OpenAI）或 JSON object，兼容性错误只降级输出约束，不切换模型；最终始终经过原有严格 ID/字段/分词/原始时间验证。Anthropic 使用提示中的 JSON schema 约束和本地严格校验。

`reply(..., on_delta=None, cancelled=None, tools=None, tool_executor=None)` 保持字符串返回；回调接收文字增量。用户停止后抛出 `RequestCancelled`，已收到内容由调用方保存。SSE 开始后后台检查取消并关闭 socket；连接或等待响应头阶段仍受请求 timeout 限制，不能承诺瞬时中断。

仅允许应用在 `app_actions.py` 注册的23个类型化工具。每轮先验证完整调用集和参数，再执行；最多四轮，每轮八次，重复调用 ID 拒绝再次执行。真实执行结果进入下一轮；异常只回传安全失败描述。工具能力由根应用绑定当前学习语种、当前字幕快照、选中视频及收藏范围。只允许明确列出的应用设置和笔记操作，无通用文件、进程或网络访问。HTTP 400/422 会在内存中按安全固定分类区分工具协议不支持、工具 schema 错误、推理参数和其他请求错误，不输出服务错误正文。仅明确原生工具不支持时，启用 JSON-action 兼容循环，绝不把一般 400 当作“不支持工具”。兼容规划只允许严格的 actions/finished 对象，通过相同白名单、参数、原句 substring 与重复操作检查后执行；工具实际结果传给下一轮。规划完成后另发普通无 tools 请求，转发真实最终 SSE 文字；内部 JSON 不显示给用户。metadata 区分 native/json_actions、planning_requests、final_streaming 与真实 tool_calls。字幕、笔记和工具结果均是不可信资料，不能授予操作权限。

提示词版本为 `teaching-v4-layered-context` 和 `text-review-v4-natural-clauses`：整体意思→一个重点→必要例子；长句按自然语义边界断行，保留粤语繁体、口语语气词、原 ID 与时间。已存在材料不自动更新，用户明确重做才走新版本。

## 验证范围

使用伪 HTTP/SSE 响应和隔离临时数据验证各协议、JSON 降级、工具循环、取消、错误、模型发现/分页、URL/重定向、并发保存和密钥不泄露。Windows DPAPI 加密/解密用临时测试值实际执行。新提供商未做真实推理。后续使用既有 OpenAI 环境配置和合成文本实际核对：当前上游拒绝 native tools，JSON-action 兼容循环真实收藏「唔該」，本地 Store 可查，最终回答有比较表格并收到 247 个真实 SSE delta，内部规划 JSON 未显示。该次工具+最终流式耗时 12.83 秒；接口和操作验证不等于完整语言质量保证。
