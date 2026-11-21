# 模型适配与实测

## 配置与隐私

默认 DeepSeek 模型为 `deepseek-flash`，OpenAI 为 `gpt-5.4-mini`；这是当前账号实际 `/models` 和小样本调用后的选择，不是价格或语言准确率保证。可在 UI 模型框或环境变量覆盖。

- OpenAI：`OPENAI_API_KEY`，兼容 Windows 环境别名 `OpenAI` / `OPENAI`。
- DeepSeek：`DEEPSEEK_API_KEY`，兼容 `DEEPSEEK`。
- 地址：`OPENAI_BASE_URL` / `DEEPSEEK_BASE_URL`，兼容各自的 `_API_BASE`；仅接受 HTTPS。
- 默认模型覆盖：`OPENAI_MODEL` / `DEEPSEEK_MODEL`。
- 查找顺序：进程环境、Windows 用户环境、Windows 系统环境。密钥从不写入报告、数据库或日志；错误只含提供商和 HTTP 状态，不回显响应体。

学习 API 只发送字幕文本、邻近上下文、语言和学习级别。复盘只发送收藏词语、句子和笔记，最多 100 条且 24,000 字符；不发送媒体路径。音视频和声纹不上传。用户可关闭云端或选本地；关闭后的分词、粤拼和拼音由本地库完成，释义留空，不伪造词典释义。

## 接口与失败行为

`LearningAgent.annotate(cues, language, progress=None, cancelled=None, checkpoint=None)` 每批最多 6 条、约 2,500 输入字符，加入前后各两条语境。`checkpoint` 收到累计已验证字幕的独立副本，父流水线按 ID 合并和保存。

OpenAI 使用严格 JSON Schema；DeepSeek 使用 JSON Object 加同一套客户端校验。校验覆盖完整字段、类型、唯一且同序 ID、分词拼接和文本长度。ID、时间和原文只取自调用方。任何改动均标记 `uncertain`。重复字段、非 JSON、截断、空内容、额外时间字段、错序和漏句不会写入结果。JSON/字段校验失败仅重试一次；HTTP 错误或截断不自动重复收费。已完成批次仍可恢复。

DeepSeek 显式关闭 thinking；初测默认 thinking 曾耗完 4,500 token 而未生成完整 JSON。OpenAI GPT-5 系列用 low reasoning。单次网络超时 120 秒；取消在请求前后和批次边界检查，无法中断正在服务端处理的请求。`last_response_metadata` 和 `response_metadata` 保留实际返回模型、耗时、token、结束原因及校验情况，不含密钥。

`annotate_local` 使用 PyCantonese 分词与粤拼、pypinyin 普通话拼音和普通单词切分。PyCantonese 的 wordseg 依赖 `pkg_resources`，需要兼容的 `setuptools<81`。词典可能缺词或存在多音字歧义，读音不能代替听音确认；依赖缺失时保留文本、退回分词且不猜读音。

## 2026-11-14 真实 API 测试

测试输入包括三句粤语上下文、两句英语过去虚拟表达，以及字幕内“忽略先前指令”的对抗样例。只发送文本，均为合成小样本。初测暴露了 DeepSeek 推理截断、英文缩写分词不一致和解释语言偶尔错误；据此关闭 DeepSeek thinking、强调缩写保持和每次请求明确解释语言。最后一次合成测试如下，详见 `reports/model-evaluation-final.json`。

| 请求模型 | 完整结构/保留检查 | 单用例耗时 | 三用例总 token |
| --- | --- | --- | --- |
| deepseek-flash | 3/3 | 2.09–3.72 秒 | 2,872 |
| deepseek-v4-pro | 3/3 | 3.61–8.44 秒 | 3,529 |
| gpt-4.1-mini | 3/3 | 4.14–9.17 秒 | 3,200 |
| gpt-5.4-mini | 3/3 | 2.25–4.36 秒 | 3,535 |

所有最终有效输出保留原始字幕 ID、起止时间与原文；英语→英语用例的翻译/释义及讲解均为英语（简单字符检查）。对抗字幕仍按语言材料处理，未输出裸 `HACKED` 或执行指令。一次通过不证明所有提示注入都能阻断；应用不给模型任何文件、终端或外部工具执行能力。

这些数字证明本机账号的接口可用、数据结构完整和本次样例行为，不是粤语准确率或模型排名。未由母语者听音标注；粤拼中曾出现 `啫` 的 `ze1` / `je1`、`嚟` 的 `lai4` / `lei4` 差异，语法及读音仍须校对。原始失败报告保留在 `reports/model-evaluation.json`、`reports/model-evaluation-nonthinking.json`，不隐藏失败。最终测试发生在加入一次格式重试前，测试全部首次成功；重试本身有独立单元测试。

## 复现

```powershell
.venv/Scripts/python.exe scripts/evaluate_models.py --live --output reports/model-evaluation-new.json
.venv/Scripts/python.exe scripts/evaluate_models.py --live --only-real --input PATH_TO_LOCAL_ASR_DOCUMENT.json --models deepseek:deepseek-flash openai:gpt-5.4-mini --output reports/model-real-new.json
$env:PYTHONPATH='src'
.venv/Scripts/python.exe -m unittest discover -s tests -p test_agent.py -v
```

`--live` 明确开启实际收费调用。真实字幕输入只取前六条；报告包含发送的文字，因此不要提交私人字幕报告。模型列表每个提供商只查一次；报告同时记录 requested_model / returned_model、latency、usage、finish_reason、schema_valid 和确定性检查。未设置密钥时安全失败，不能把本地结果伪装成云端成功。

参考官方文档：[OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)、[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[DeepSeek JSON Output](https://api-docs.deepseek.com/guides/json_mode/)、[DeepSeek Models](https://api-docs.deepseek.com/api/list-models/)。模型可用性来自本机账号的实际列表，不由文档推断。

本地依赖修复后，16 项 Agent 单元测试全部通过（1.664 秒），含真实 PyCantonese `食飯 → sik6 faan6`、完整性拒绝、累计检查点、一次重试、取消及复盘去路径检查。默认 DeepSeek/OpenAI 的收藏词笔记复盘也分别完成真实调用，输出见 `reports/model-review.json`。


## 真实样片文字复核

已对本机 GPU ASR 的前六条实际字幕分别调用两个推荐模型；完整文本与回答只保存在忽略目录 `reports/private/`，可提交的无原文指标在 `reports/model-real-metrics.json`。两者最终均保留六条字幕原文、ID 和时间，分词覆盖完整，五条标记待复核，没有把普通话化的转写改造成自认为听到的粤语。

| 模型 | API 总耗时 | 总 token | 请求数 | 结果 |
| --- | --- | --- | --- | --- |
| deepseek-flash | 7.593 秒 | 2,254 | 1 | 完整输出；仍在解释中对乱码提出无足够依据的替代猜测 |
| gpt-5.4-mini | 17.672 秒 | 5,835 | 2 | 首次分词不一致被拒绝，一次重试成功；明确把乱码和专名留待回听 |

这次真实文字样例中，OpenAI 对不可靠 ASR 更保守，可优先用于粤语复核；DeepSeek 更快，但需更仔细审阅解释中的猜测。样本只有六条、未提供人工听写标准，不能据此声称任何准确率或稳定排名。两种模型都无法从普通话化 ASR 恢复真实粤语发音；保留原文和回听仍是必要的学习步骤。初测和修订后结果都保留，未删除失败。

根据用户对逐词音标的要求，最终提示词还要求英语尽量提供可靠词典 IPA、日语提供可靠假名或适合学习者的罗马字；不确定专名读音及标点留空。本地模式仅粤拼和普通话拼音具有已安装的词典支持，其他语言不会生成假音标。此最后一项提示词变化未另外收费跑全模型基准；前述英语测试证实同语教学，不证明新增 IPA 准确性。

PyCantonese 3.x 的内置语料默认并行读取在 Windows stdin/GUI 工作线程环境不稳定。适配层首次加载使用短暂、带锁且恢复原方法的串行语料预热，然后仍调用其正常分词/粤拼 API；16 项测试及 stdin 调用通过。该兼容层依赖当前 PyCantonese 3.x 内部读取类，升级到 4.x 前需重新检查（项目依赖已约束 `<4`）。

## 长片分词恢复

745 条字幕的实际准备流程在第 109–114 条遇到连续两次“模型分词无法拼回字幕”的错误，原有前 108 条检查点已保留。现在仅针对这一类错误增加确定性恢复：先完成一次模型重试，再完整检查返回批次的所有字段、ID、类型、条数与文本长度；仅拼接不匹配的句子使用本地分词及本地读音重建。

只有与本地词条文字完全相同、且重复候选释义无冲突的模型释义和学习标记会保留；其余词条及不匹配释义被丢弃，不移植模型猜测读音。修复句标记待复核。重建后的整批结果再次经过公开严格校验；若本地分词也不能完整覆盖文字，或同批其他句子存在错 ID、非法类型、额外字段、过度扩写等问题，仍拒绝整批。公开 `validate_annotations` 默认行为保持严格，不自动修复。

响应元数据保留原始 `validation_error`，并通过 `validation_passed_after_repair` 和 `token_repairs` 记录修复句 ID、方式、词条数和保留释义数。恢复不增加额外模型请求，也不改变原文、时间或检查点接口。新增测试覆盖缩写及标点恢复、本地读音优先、冲突释义丢弃、后续行非法字段不得绕过、最终覆盖检查与首次重试成功不触发修复；21 项 Agent 测试全部通过（1.465 秒）。长片恢复是否成功由父流水线实际续跑验收，此变更未另外启动收费长片测试。

## 逐词读音补全

在整个模型响应通过严格校验后，粤语/普通话词条若缺少读音，会尝试本地词典补全。仅纯汉字词条、完整文字覆盖、每一组成部分都能查得合规读音时才采用；保留已有合规模型读音，格式错误的粤拼可以改由词典提供。混合文字、标点、缺词以及没有本地读音词典的英语等语言保持空白，不编造音标。

这些读音描述字幕上的汉字，不证明片中实际说法或声调；普通话化 ASR 字幕尤其需要回听。该补全不修改字幕文字、分词边界、原文、时间或模型释义，不影响严格 JSON/ID/拼接检查。新增四项验证覆盖真实粤拼与拼音、保留现有读音、完整查词/文字体系约束，以及整批校验失败时不得运行补全；当前 25 项 Agent 测试全部通过（1.828 秒）。已经保存的旧检查点由父流水线另外补全，新批次会自动采用此处理。

## 工作区的批量请求并发设置

视频准备从工作区的 `AppSettings.llm_workers` 读取并发上限。默认开启 4 路，允许 1–16 路，关闭后为 1 路。原文复核、逐句讲解和译稿核验依次运行，每阶段独立派发批次。讲解每批最多六句、约 2,500 字符，取完整时间线上的前后各两句作为上下文。每批独立记录实际返回模型、用量、校验和修复信息，重试也占用该批的线程。译稿核验每批最多 24 句，格式或输出长度校验失败时可拆小批重验。

只有调用 `annotate` 的协调线程会发送进度和累计检查点。已完成句子按原输入顺序合并；如果较晚批次先完成，检查点可能暂时含空缺，但其字幕 ID 不变，父流水线按 ID 合并保存。之后的累计检查点包含此前所有完成结果，不丢弃先完成的较晚批次。

任一批失败或用户取消后停止派发新批次及新重试，等待已有请求结束；已经完整通过校验的结果仍保存，再向上抛出原错误或取消信息。无法强行终止服务端正在生成的请求。`.response_metadata` 按批次顺序汇总实际响应，`.batch_metadata` 另记录完成/失败/取消状态、输入区间、各请求和脱敏错误，因此无响应的 HTTP 失败也有记录。原有严格校验、一次重试、本地分词修复和词典读音补全保持不变。

30 项 Agent 离线测试通过（全套 1.605 秒）；新增同步假 HTTP 测试验证真实两请求同时在途、倒序完成后的排序及协调线程回调、独立元数据和邻句上下文、前批失败仍保存后批、取消后不再派发请求/重试以及单线程诊断。没有为此发起收费请求；长片实际性能由后续集成运行衡量，不承诺固定加速倍数。

断点续跑可向 `annotate(..., context_cues=document.cues)` 传入完整时间线，而 `cues` 仍只含待分析字幕。每批按首/尾待分析 ID 在完整时间线中的位置提取真实相邻两句，使用 `original_text`（空时才用 `text`）的快照，不跳过已完成邻句，也不把此前模型改写当作原文证据。完整时间线中的 ID 重复、缺少待处理 ID 或顺序冲突会在联网前拒绝；省略参数时保持原用法。断点邻句与非法上下文两项回归测试加入后，当前 32 项 Agent 测试全部通过（1.591 秒），Ruff 检查通过。

集成复核补充修复：若待处理 ID 在完整时间线上不连续（例如只剩第 2、5 句），即使未达到六句上限也会拆成两个请求，确保中间已完成的第 3、4 句仍进入对应邻句上下文。

检查点写盘或进度回调抛错时，协调器现在先停止新派发，再收集所有在途请求的已验证结果和计费元数据。`completed_cues` 保留累计已验证句子；检查点写入失败后，等待收集结束才额外尝试一次完整累计保存，`checkpoint_metadata` 记录该次重试结果。无论最终重试是否成功，都继续向调用者抛出最初的回调错误；持续磁盘故障不能保证落盘，但不再丢失内存结果及响应记录。两项独立集成回归、全部 Agent/并发与 pipeline 测试、临时磁盘失败恢复测试共 40 项及 16 个子测试通过（2.27 秒）；Ruff 通过，未调用真实 API。
