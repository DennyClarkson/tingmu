# Handoff — 2026-11-15 · 听幕 0.5.0

## 团队建仓（2026-11-15）

用户已明确授权整理项目、在个人 GitHub 账号创建新私有仓库并推送，以便团队继续开发；这覆盖此前只在本地开发、未提交远程的阶段。目标仓库为 `DennyClarkson/tingmu`，默认分支 `main`。团队成员访问权限由所有者另行分配，本轮没有邀请他人或公开仓库。

- 新增 `CONTRIBUTING.md`、`docs/development.md`、PR/问题模板、Windows GitHub Actions。新成员从源码启动，不依赖原开发机的便携包或私人样片。安装入口按 `uv.lock` 锁定同步；`.env.example` 只列变量名，应用不自动加载 dotenv。
- CI 使用 Python 3.12、CPU/dev 依赖、只读仓库权限和离屏 Qt。`.github/run_offline_tests.py` 拒绝 Python 进程的外部 DNS/TCP，允许本地 HTTP 测试。独立干净 CPU 环境实测 **505 passed、84 subtests，41.97 秒**，Ruff/actionlint 通过，测试不依赖 FFmpeg CLI、API Key 或模型权重。GitHub 托管运行状态以 Actions 页面为准。
- 扩充忽略规则，重新审阅首次提交。仅共享三份不含正文/密钥/个人绝对路径的 v0.5 汇总；其他历史报告和原索引备份留在本机。源码未包含视频、字幕文件、学习数据库、笔记、聊天、配置凭据、缓存、模型或运行时。
- 保留四款字体及 OFL 许可，新增第三方资源说明；没有擅自给私有项目选择开源许可证。磁盘文档改为通用目录说明，旧本机记录保留在 `.cache/team-bootstrap/`。历史迁移/截图脚本在开发文档中明确标注用途。
- 这轮整理没有改动应用处理逻辑或个人资料，也没有重新打包便携版；上一轮实际软件验证仍见以下 v0.5 记录。

## v0.5 当前交付与用户决定

入口仍是 `dist/Tingmu/Tingmu.exe`。本轮在真实 Windows 软件中验收 Agent、自配模型、收藏/Markdown 笔记、全屏选词和队列。测试资料在 `.cache/v5-qa` 隔离，未把测试收藏、笔记、模型配置或聊天写入生产数据；原始 `../22.mkv` 始终不改。无 Git 提交、远程操作或身份配置。

用户确认：收藏明确整理后写入笔记，保留原收藏并标记已整理；按语言分库、按主题组织。讲解偏好立即保存并用于后续任务，已有材料只有明确要求重做才重新生成。支持语言关系、个人目标、词句备注，不只做生词清单。聊天范围继续不包含联网搜索、上传图片或语音通话。字体仍随包提供开源思源黑体/宋体、Source Sans 3 和 Source Code Pro。

## v0.5 实现

- Agent 扩为 23 个类型明确的应用操作：读上下文/队列/收藏/笔记，指定视频准备或重做，播放控制，模型/推理强度/主题/工作区，收藏整理与备注，追加/修改/保存/删除笔记，复习/练习及讲解偏好。没有任意命令或文件系统工具。原生工具不支持时的严格 JSON 规划兼容路径保留。
- 会话请求固定发起时的语言、视频、选中媒体、字幕、模型指纹和推理强度；切换会话不会串写。编辑/重试创建分支，原消息保留；模型配置改变后旧请求不静默使用新连接。笔记覆盖/修改/删除要求读取完整内容及匹配修订，分页预算追踪实际读取范围；成功写入才去重。
- 视频处理通过主线程执行，队列限定本次请求 ID，不连带启动无关失败任务；重做前备份。超时/取消有执行前与步骤间检查，已有字幕/收藏/笔记不因偏好更改而自动重跑。讲解偏好纳入提示词和缓存身份。
- 收藏箱与笔记分开；旧 Markdown 逐字节迁移保留，建立索引与来源归档。个人备注有修订保护和历史，整理时可合入语言对比/目标/误区/自拟例句。手写 Markdown 不被重新渲染覆盖；长编辑器键盘编辑跟随光标、鼠标阅读保持位置。
- 伴侣空白态区分无视频、已打开但无字幕、处理中及字幕空档。全屏按钮不再抢空格；全屏暂停可拖选原文并准确收藏。浮动模块释放主窗占位和空列，放回仍复用原实例。各窗口键盘操作按焦点窗口隔离。
- 聊天可选推理强度，显示连接/思考/工具执行/生成回复/停止中；停止立即反馈，连接或等待响应头阶段仍受传输超时约束。公开思考默认折叠、可展开/复制、内部滚动、计时，保存到原会话并可重启恢复。
- DeepSeek `reasoning_content`、Anthropic 公开 thinking、OpenAI Responses `summary_text` 接入真实流式事件。OpenAI/自定义连接显式选择 Responses，旧连接不自动切换；请求摘要，私有加密/签名块仅用于当前工具往返，不进入界面、日志或历史。流式密钥跨片段脱敏在首次显示与首次 SQLite 检查点前执行；公开思考上限 60000 字符。
- 模型发现列表按连接保存与显示，新建连接不再串用上一厂商缓存；已保存协议/手填 ID 不被候选列表改变。手工密钥 Windows DPAPI 加密、保存后输入框清空，不明文回显。启动器使用独立 `TingmuRuntime.exe`，避免 Windows 把窗口归到 IDLE。
- 最终生产目视发现旧布局将聊天换到播放器槽位并压到 300px，发送/模型/强度被裁切。现按实际槽位占用者分配最小高度，短面板紧凑排布；真实拖动在 1042/720/600 高窗口下验证控件可见，正式旧布局也再次目视通过，播放器仍保留空间。

## v0.5 实际验收证据

- 最终全部修复后的完整回归 **505 passed、84 subtests**，42.87 秒；Ruff 与 diff whitespace 检查通过。25 项 QML 聚焦回归也通过。中间一次聚焦运行在 fixture 初始化/GC 中发生 Qt 原生访问异常，复跑及最终完整套件均通过，记录在 `docs/ui-v5.md`。最终证据见 `reports/acceptance-v5.json`、`reports/portable-v5-validation.json`。
- 实际全屏拖选收藏成功，鼠标点全屏后空格暂停且保持全屏；空字幕视频显示正确提示；独立媒体库释放整列；真实 Markdown 编辑/保存、手写区保留、复习评分、复制代码、编辑/重试分支、停止反馈和多次重启恢复均通过。
- 实际模型设置：OpenAI 环境变量连接加载 96 个模型；DeepSeek 预设加载 2 个模型；手工假凭据只做隔离 DPAPI roundtrip，不调用网络。所有云端实测只发送文本，不上传音视频。
- 实际 Astra Agent 保存后续讲解偏好、整理收藏、读写完整笔记与个人备注，指定 12 秒视频完成本地 Qwen CUDA ASR→文本复核→讲解 5/5。UI 再执行仅重做讲解仍 5/5；备份对比确认原 ID/顺序/raw/正文/时间/分段、ASR 与复核元数据不变。最初隔离包缺少 portable marker 的夹具问题已修正，生产包一直有该 marker。
- 实际 DeepSeek `deepseek-flash` 高强度连续两轮，读取收藏和笔记、追加主题笔记、读取后修改保存，工具均成功；公开思考分别 2458/1301 字符、7156/3828 ms，展开与重启保留通过。最后目视笔记包含真实出处、个人备注、普通话/粤语对比、自拟例句、自测和追加目标，手写内容/原收藏不变。
- GPT Responses 软件内三轮（high/high/low）均正常回答，但服务未附公开摘要；不声称这三轮见到了摘要。独立真实传输探测返回 87 个摘要增量/397 字符；真实 Responses 原生工具往返成功，另一次工具请求也未返回摘要。模型请求名与服务返回名、安全计数详见 `reports/v5-reasoning-live.json`。纯内存完整 Companion 链核对 summary=auto/effort/stream/tools 和回调贯通；显示能力的 QML 回归通过，不伪造摘要。
- 生产刷新前 `.cache/v5-release-backup` 已备份：52 个非日志/非数据库文件逐一 SHA256、8 张旧表逐行内容、原视频 hash。正常启动后原笔记字节、全部文档、5 条收藏及旧聊天均保留；预期迁移新增收藏/笔记索引/来源表，设置只加两个默认字段；实际播放更新一条进度。原 22.mkv 898 源句/1015 显示段仍准备完整。
- 最终包 87 个应用源文件逐字节一致，55 个 QML/字体/图标资产纳入 manifest；正式 EXE 再次通过 12 项离屏媒体检查，正常 Windows 窗口已打开。最终再审计 51/52 原文件字节不变，只有 settings.json 新增默认字段；原 8 张表只允许已观察到的一条播放进度变化，其余旧行原样，SQLite 完整性 ok。

## v0.5 保留边界

公开思考/摘要和推理强度取决于具体服务及模型，未逐个厂商账号实测；Anthropic 等走协议回归。手工密钥换 Windows 账号/电脑需重新输入。连接建立阶段停止不是瞬时断开。没有联网/多模态功能；Markdown 不执行脚本或加载远程图片。全文复核仍仅文本，不等同于听音核实；ASR 内部无断点、ASS 特效/OCR、第二台干净电脑验收等旧限制不变。

优先读 `docs/contracts.md`（Revision 7）、`docs/providers.md`、`docs/ui-v5.md`。本轮主要服务是 app_actions、providers/companion、conversations、notebooks/learning_notes、quick/features/bridge 与 QML。以下保留 v0.4.1 故障恢复及旧版历史，不以旧摘要覆盖当前个人资料状态。

---

# Handoff — 2026-11-14 · 听幕 0.4.1

## 0.4.1 实际队列失败修复

用户实际22.mkv的898句任务在全文复核40句后失败。检查点记录两次`分句过碎`，界面仅显示`AnnotationValidationError`。这是展示分段约束使整批中止，之前合成小样本验收未覆盖，不是ASR或密钥失败。

- review先严格验证schema、ID、原始分段逐字重构及正文更改依据，再对过碎/过长显示段保字恢复；不跨源句合并，不改源ID、raw或时间。每句记录`segmentation_repair`方法/原因/宽度/数量，代码修复与模型分段明确区分。保持复核版本以复用已完成40句。
- 边界专用迁移脚本segment_existing继续要求纯模型边界，不能把代码修复误记为模型结果；坏缓存及已完成文档仍拒绝。
- 全片讲解实测又发现mini反复抄错一条20位hash ID，停在892/898；agent现在只在明确身份错误后的第二次请求使用短别名，先严格校验别名/顺序/唯一性，再恢复真实ID并走全部正文/token校验，仍固定最多2次请求。未知/重复/调序/原hash冒充别名、schema或正文更改仍拒绝；记录identity_alias_retry/map/mapping_validated。真实受控触发后的别名请求6句通过，未减弱任何验证。
- 队列保留机器错误类型，另提供脱敏中文原因、失败阶段和检查点计数；开发者日志记录错误堆栈但不记录正文。旧AnnotationValidationError有中文兼容显示。单项重试直接开始；仅有失败项时顶部“重试失败”。完整原因可换行选中复制。
- 最终自动回归357 passed、84 subtests；Ruff通过。14项QML交互含330px窄窗错误展示/实际点击命令通过。实际便携EXE已刷新0.4.1，12项启动/媒体smoke再次通过。
- `scripts/verify_checkpoint_recovery.py --live --data dist/Tingmu/data --job <id> --output .cache/annotation-recovery`：在隔离副本上验证整片续跑，显式禁止调用ASR/重新导入字幕，复用输出前校验job/settings/媒体/语言/基线一致。实际复核898/898、讲解898/898全部完成；最终10项检查全true，见`.cache/annotation-recovery/validation.json`与`reports/annotation-recovery-v0.4.1.json`。初次长片续跑1074.86秒完成882句讲解，随后仅重试未完成16句到892句，最后6句成功；不会重跑已完成材料。
- 全片独立结构审计：源ID/顺序/raw/时间全不变，原40句正文与分段保持；1015显示段、104源句分拆、最大42列、逐字重构；25条short_source_split布局修复。复核仅文本，不代表听音/人工确认，仍有211条存疑与4条拒绝改写保留原文。
- 已正常关闭用户旧窗口，核对原doc基线未变后，备份原文档和队列至`dist/Tingmu/data/material-backups/recovery-0.4.1-20261114T084212Z`，仅安装最终doc及单job完成状态。其余47个个人文件逐个hash不变；学习笔记/收藏/会话未覆盖。随后启动正常Windows GUI，日志确认0.4.1与队列completed 898/898。

---

## 最新交付与用户决定

完整升级到0.4.0，生产入口仍为 `dist/Tingmu/Tingmu.exe`，只刷新应用，复用模型与运行环境。用户确认按所学语言分库、按主题整理；对话范围是Markdown/表格/代码、流式、停止/重试/编辑、多会话与学习工具，不含本轮未要求的联网搜索、图片上传或语音通话。用户后来明确所有字体必须开源，已随包提供思源黑体/宋体、Source Sans 3、Source Code Pro及许可。

## v4 实现

- 队列单选/多选清除全部识别讲解，或仅清讲解，确认后可重新排队。当前文档先备份到data/material-backups；全量重做绕过同名字幕强制ASR；后续文本阶段失败重试仍复用刚完成的ASR。只重做讲解保留已有复核正文/分段和原ASR音轨设置，包括云端关闭时。视频/单词/笔记/对话/进度和其他语言材料不清除。
- ModelRegistry统一复核/讲解/伴侣配置，7个提供商预设、动态发现、手填ID、输出长度/推理选项。环境变量含旧别名兼容，手工密钥仅Windows DPAPI保护；无明文回显/导出，重定向拒绝，HTTP仅无密钥loopback。复核provider独立于companion，选择新伴侣不会串改复核服务。
- Reviewer/Teacher更新可读性提示词版本，保留ID/原文/时间及粤语口语；严格分句重构，教学不改已复核正文。升级不会主动批处理现有个人材料。
- 对话安全Markdown/表格/代码分块、复制、主题排版、真实SSE、多会话、停止、编辑/重试分支，原回答保留。部分流式回复每秒检查点；过期pending在重新打开时转stopped。请求绑定发起session/media/language，导航不会写错库。
- 伴侣6个受限学习工具：读取笔记、收藏当前词/句、整理、追加笔记、读取复习词。没有文件系统/命令/网络搜索工具。当前已配置服务端拒绝原生tools（已实测）；仅明确unsupported响应转严格JSON-action规划，验证后执行，再独立SSE生成最终回答。未知400/schema错误不自动降级，内部JSON不显示，重复与越权操作拒绝。
- 学习工作区真实Markdown按语言保存，AI只为现有ID分配主题，本地重新渲染来源，不能编造/丢失收藏。手写区、修订hash冲突、备份、来源跳转、视频筛选、持久间隔复习均可用。编辑草稿按语言保存在quick-layout，600ms去抖和关闭前保存，跨重启可恢复。
- 森林/纸页/雾青/石墨四主题跨窗口实时应用。顶部菜单/下拉/确认框统一，修复下拉框被播放器方向键抢焦点、流式重置阅读位置、TButton文字测量导致省略号、延后排版访问已销毁delegate等真实界面问题。暂停全屏可选字/点词/收藏整句并进入学习，浮层尺寸仍固定。

## v4 验收

- 最终完整测试 **335 passed、68 subtests passed**；Ruff全部通过。唯一既有wordseg/pkg_resources弃用警告，未更动兼容固定。
- 新功能/跨模块回归覆盖重做保留个人数据、缓存/失败续跑、profile角色、DPAPI实际roundtrip、HTTP重定向、协议/SSE/兼容工具、笔记冲突、会话串写与取消、真实QTextDocument安全渲染、跨重启草稿等。
- 真实合成文本模型验收9项全通过，见reports/v4-model-validation.json：动态模型列表、Astra复核、mini教学、原ID/时间不变、精确分句、实际工具收藏「唔該」、195个最终SSE delta、Markdown表格及笔记整理。没有上传用户音视频。此为小样本行为验证，不代表ASR准确率或全语种质量。
- Windows原生实际视频验收10项true，QML warnings=[]，见.cache/v4-native-final/acceptance.json与截图。另在.cache/v4-native-ui目视观看、学习、Markdown聊天、小窗口聊天、模型表单；13项QML交互测试通过。
- 实际便携EXE已在offscreen/windows两模式验证0.4.0、随附开源字体、学习工作区/模型预设、视频解码/seek/倍速/音轨/暂停，见reports/portable-v4-validation.json。刷新前48个非日志个人文件SHA256、45750个既有运行时文件大小/mtime、EXE和原样片SHA256作基线，最终逐项核对。

## v4 限制与继续开发

所有厂商协议有模拟测试，真实推理只用本机已配置OpenAI兼容服务，未逐个厂商账号验收。DPAPI密钥换电脑/Windows账号需重输。网络建立连接/等待响应头时停止受超时约束，进入SSE后可以关闭响应。模型上下文有界，聊天不包含联网/多模态；Markdown不执行HTML/脚本、不加载图片资源。复核仍仅文本，分句时间仍为估算，片内ASR无断点、ASS复杂特效/图片字幕OCR未实现、未在第二台干净电脑验收等既有限制保留。

优先读docs/v4-development.md、docs/providers.md、docs/ui-v4.md。新代码服务：providers/credentials、notebooks/conversations/richtext、quick/features/typography；桥仍quick/bridge，QML Theme等组件。用户个人原材料保留其实际现状，不以旧v3交接的ready描述覆盖后来形成的partial状态。无远程提交、无代填Git身份。

---

## 以下为 v0.3 历史记录（不代表当前个人材料状态）

## 当前交付

`dist/Tingmu/Tingmu.exe`，Python后端+Qt Quick/QML界面，完整便携目录约17.3GiB。用户只需双击EXE并整体保留文件夹；现有本地模型和运行环境复用，没有重新下载大依赖。根目录仍只有tingmu/和原样片22.mkv。

用户纠正v2：禁止合并短句，只消除短字幕间的空白；长行交给强模型拆分。浮层固定尺寸并有右下角缩放。任何位置的视频可多选，所有学习资料必须留APP目录、按视频路径对应。旧QtWidgets UI已替换，模块设置各自收进齿轮。新增用户要求“开发者模式和日志”也已实现。

只用原生GPT-6-Astra子代理；用户明确禁用旧dispatching-parallel-agents skill，已写AGENTS.md/skill.md，不再使用或读取。

## 已实现

- QtQuick自定义Basic控件、本地图标。默认观看布局4模块：媒体、视频、伴侣、字幕；准备布局突出媒体和队列。独立聊天按需打开。所有6模块可拖动交换、浮动/放回、调整分隔并持久化。窗口适配屏幕及1280×680验证。
- `SubtitleTimeline`不再分组合并、不延长最短时长、不额外保留伴侣；字幕/伴侣同一句，500ms以内真实空档桥接，可调0–1500。重叠事件/后退/跳转直接计算。旧min/hold参数只兼容读取、忽略。
- `Cue.segmented_texts`由复核v2提供，逐源句严格重构正文，单条最多42显示列。源ID/raw/time不变；拆分显示ID source:part:N，时间按字数估算并明确标text_proportional。来源面板展示原声学起止和估算依据，讲解仍源句级。
- 主片已补做边界复核：898源句→1011显示句；三个片段52/65/64→56/72/75。只向Astra发125条长源句，共7请求，返回模型均gpt-6-astra、报告26373tokens。所有原字段（含讲解、词语、时间、review_spec）逐项保持；旧JSON备份在data/document-import-backups/segmentation-v3-20261114-093732-837019。原始计数合计1079，显示合计1214。
- `MediaLibrary`索引多个任意目录的视频，数据保存在app data/，源码默认项目.data/；不在视频旁写学习资料、不复制视频。多选入队→独立准备视图→逐视频阶段/进度→暂停继续重试；原有队列接口和处理检查点复用。
- 同一个QMediaPlayer/QMLVideoOutput用于正常/全屏/浮动。固定伴侣浮层内容内部滚动，标题移动/右下角缩放，透明度/字号/内容设置在播放器。暂停跨窗口时CPU重传最后一帧，已修复真实GPU场景黑屏。控制条暂停不隐藏。
- 修复FFmpeg加载时早期seek被丢弃：等待Loaded/Buffered后排队设置。真实180秒笔记定位、90秒播放位置恢复验证通过。
- 聊天与复盘互斥写对话，异步结果绑定发起时视频/语言；笔记草稿按媒体|源语言|目标语言|cue保存，搬迁内部媒体时仅重写键中的媒体路径。
- 原有音轨、字幕、倍速、音量、A/B循环、句子重听、视频导航、画面适应和截图接入新UI。ASS仅文本、无图片OCR的限制保留。
- 伴侣软件指南同步全部v3真实标签；一次实际Astra回答通过，返回gpt-6-astra，10.25秒/2205reported tokens。
- 帮助→开发者模式→查看日志。诊断信息、日志级别筛选/搜索/详情/跟随，导出到data/diagnostics；默认INFO、开发者DEBUG。events.jsonl每份2MiB+3备份；application.log每份2MiB+2备份。密钥脱敏、学习/请求/响应正文不进结构化日志，异常只记录类型/堆栈位置，日志损坏行跳过。

## 验收与证据

- 最新完整测试：208 passed、66 subtests passed；Ruff全部通过。一个已知wordseg/pkg_resources弃用警告，setuptools<81兼容固定不变。
- QML真实实例测试：观看/准备/独立聊天同一播放器，实际鼠标拖动浮层缩放+持久化，模块交换无重建，6个模块齿轮均可打开，开发者窗口开启/关闭，QML警告0。
- Windows原生可视验收（不是只看状态）：真实视频像素，全屏暂停画面、浮层稳定及缩放、返回主窗、聊天、开发者日志全部通过。截图`.cache/v3-ui/native/{watch,fullscreen,prepare,chat,developer}.png`；验收10项true，已目视检查watch/fullscreen/chat/developer。脚本scripts/verify_quick_ui.py仅操作自己窗口，用隔离.data/v3-native-final。
- 原v2“全屏未目视验证”限制已经解除。曾在新QML发现暂停全屏黑屏，已修复并通过像素采样与截图。QA脚本一次C++窗口已删除错误是Python临时包装对象链造成，显式保留item引用后正常，与产品QML窗口销毁无关。
- 包装验证见reports/portable-v3-validation.json及后续developer证据：实际Tingmu.exe offscreen/native均能解码、seek、切音轨、倍速和暂停。运行时40815文件、原EXE保持；39个原非日志data文件哈希保持（此基线在边界补充完成后）。最终开发者代码已刷新并通过实际EXE与隔离诊断测试，34个QML资源与源码一致。
- 字幕证据reports/review-v3-segmentation.json、segmentation-v3-existing.json；对话指南reports/companion-v3-evaluation.json。元数据token数不是全部费用或账单。
- 原样片../22.mkv未修改。早前已测H264 MP4/MOV、HEVC MKV、VP9 WebM、MPEG4 AVI；三段约3分钟的CUDA识别→Astra→讲解/批量暂停续跑已过，本轮没有无谓重跑ASR。

## 仍需明确的限制

文本模型没有听音，不能保证人名、漏字与语气词。原Qwen全片898句中1907零时长字词，末尾42:30–42:54无法可靠对齐，审计保留。此次短间隔衔接不会修好原声学时间；4个新拆分片段因源时长不足仍小于350ms，不合并源句或捏造时长掩盖问题。

拆分句沿用源句讲解/翻译；跨分段词语只保留可证实的整词读音/释义。外部ASS/SSA无复杂特效、图片字幕无OCR。片内ASR无断点。云请求中取消需等返回，退出最多等待本地清理3.5秒，未来可改善传输取消。未在另一台干净电脑验收；GPU仍需兼容驱动。

## 磁盘与原数据

本轮所有文件在项目内，缓存/临时目录涵盖QML/shader。之前C盘pip cache purge已清1701文件1264.5MB（约1.18GiB），仅剩806字节状态；其他软件和学习记录未动。更早67个本次uv缓存搬入.cache/relocated-uv，释放约8.28GiB并保留链接。不要再次清理其他模型/共享Python。

C盘历史学习记录只复制到便携版，原副本保留。当前data数据库/设置不被打包覆盖；只有用户明确要求的源文档分段字段增加，逐字段证明其他内容保持并有原字节备份。E盘旧Whisper分块约256MiB此前删除被策略拒绝，仍保留，不影响C盘。

## 继续开发

先读skill.md与docs/contracts.md Revision5。代码：quick/main+bridge+qml、diagnostics、library、timeline、review、pipeline、batch、storage/portable_paths、media、agent/companion。旧ui/app+workspace只作历史测试，不参与默认入口。不要误把旧QtWidgets功能入口写回新伴侣指南。

生产入口仍EXE，源码调试和测试命令见README；构建刷新只复制app，复用完整运行时。密钥、真实媒体/原文/用户数据库/dist/cache均不进Git。仓库没有设置提交身份，不代填身份、不发远程。
