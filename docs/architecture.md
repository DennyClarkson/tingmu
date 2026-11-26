# 听幕 0.13.0 架构与扩展点

听幕在 Windows 上以 Python 和 Qt Quick/QML 运行。一次观看涉及三层数据。音轨或字幕提供有来源的原句，准备流程生成工作区的学习文档，播放器按当前时间从文档取显示行。修改其中一层时，要保留下一层追溯到原句的能力。

## 从入口找到代码

以下路径相对于 `src/tingmu/`。

| 修改目标 | 先看这里 | 对应回归 |
| --- | --- | --- |
| 启动、切换工作区、切换界面语言 | `quick/main.py`、`quick/workspace_controller.py`、`workspaces.py`、`quick/localization.py`、`quick/i18n/` | `test_qml_workspace.py`、`test_interface_language.py` |
| 播放、全屏、音轨和显示字幕 | `quick/playback_controller.py`、`quick/bridge.py`、`quick/qml/`、`quick/playback_sources.py`、`quick/embedded.py` | `test_qml_v5.py`、`test_player_sources.py`、`test_embedded_subtitles.py` |
| 入队、选择字幕来源、失败重试 | `media_inbox.py`、`batch.py`、`subtitle_sources.py`、`pipeline.py` | `test_workflow_queue.py`、`test_subtitle_sources.py`、`test_agent_preparation.py` |
| 文本模型前的时码规整、英语句点、可选智能分句 | `acoustic.py`、`pre_llm.py`、`sentence_punctuation.py`、`regroup_stage.py` | `test_acoustic_periods.py`、`test_pre_llm.py`、`test_regroup_preparation.py` |
| 准备工作室的实时批次和已核验字幕 | `studio_stream.py`、`studio_metrics.py`、`quick/qml/PreparationStudio.qml` | `test_studio_stream_pipeline.py`、`test_studio_metrics.py`、`test_studio_qml.py` |
| 语音识别、原文复核、讲解、译稿核验 | `preparation.py`、`review.py`、`agent.py`、`translation_verifier.py`、`batch_scheduler.py`、`llm_client.py` | `test_external_asr.py`、`test_pipeline_review.py`、`test_agent_concurrency.py`、`test_batch_scheduler.py` |
| 模型与授权、学习对话、受限操作 | `global_models.py`、`providers.py`、`credentials.py`、`companion.py`、`app_actions.py` | `test_global_models.py`、`test_conversations.py`、`test_app_actions_v5.py` |
| 文档发布、播放时间轴、收藏与笔记 | `storage.py`、`database.py`、`timeline.py`、`corrections.py`、`notebooks.py`、`quick/collections_controller.py` | `test_storage.py`、`test_database.py`、`test_word_recollection.py` |

## 准备一份学习文档

`LanguagePair.target` 是音轨的学习语言，`source` 是讲解和译文使用的已知语言。两个字段可以相同。队列在入队时保存任务所选的流程。工作区更改「新任务流程」影响随后入队的任务。聊天中的处理视频操作也可以为单次任务选用流程。

| 工作流及输入 | 学习原文从哪来 | 后续处理 |
| --- | --- | --- |
| 经典流程（默认） | 导入指定或匹配的随附字幕，否则转写所选音轨 | 按工作区配置做原文复核、逐句讲解及译稿核验。停用文本模型时可仅准备本地字幕。 |
| 字幕 Agent 找到可信学习语言原文 | 直接采用原字幕与时码 | 跳过 ASR 和原文复核。生成讲解，只为缺少配对译文的句子生成并核验译稿。完整双语来源仅需讲解。 |
| 字幕 Agent 只有已知语言译文 | 识别所选音轨，或复用同片、同音轨及时间范围内兼容的原声转录 | 只把有时间重叠的译文用作复核与讲解的参考，再依据原文生成并核验译稿。 |
| 字幕 Agent 没有可信文本原文或参考 | 回到该音轨的转录路径 | 队列说明排除原因。可列出图像轨，学习流程尚无 OCR。 |

`plan_subtitle_sources()` 检查指定外挂或匹配的随附字幕，也检查内嵌轨。判断依据包括可识别的集数、所选音轨、实际语种、时间对应和对白覆盖。已提供的配对译文与模型核验的译文各自记录来源。仅供观看的「导入播放字幕」由播放器单独管理。

`prepare_media()` 先存来源、原文与时码，再分阶段保存成功句及错误信息。字幕 Agent 需要逐句讲解才能完成。缺少可用的讲解模型时保留已有材料，配置后可重试。队列顺序处理视频，每段视频开始或恢复时读取工作区的文本并发上限（默认 4，可调 1–16）。复核、讲解、核验依次进行，各阶段可并行发送不同批次，按源句 ID 校验，只补缺失或失败的句子。程序重启后，中断的运行中任务恢复为暂停。转录阶段中途退出时，尚未完成的单次 ASR 需要重跑。

## 播放与界面

`ui.run_app` 进入 `quick/main.py`。每个工作区会话有自己的 `Store`、`Bridge` 和 QML 引擎。主窗口组织「观看 / 准备 / 学习」、浮窗和独立聊天。一个会话内由同一 `QMediaPlayer` 和 `QAudioOutput` 负责普通与全屏播放。暂停时可把最后一帧重新送到切换后的场景。界面线程执行播放和 QML 操作，耗时的准备与模型调用在后台运行。

`StudyDocument.cues` 保存源句 ID、原始文字与时码。`timeline.py` 为长句派生显示行，分段文本须完整重组正文，每段最多 42 个显示列。分段时间按文字比例估算，标记为 `text_proportional`。相邻源句各自保留，短空档可延续前句显示（默认 500 毫秒，最多 1500 毫秒）。播放器与译文 SRT 使用相同的分段边界。进度变化只刷新时钟与当前句，字幕总数、词卡收藏状态和已整理状态分别来自文档或内存索引。

学习字幕、内嵌字幕和导入的播放字幕可独立显示。内嵌轨由独立的 FFmpeg 字幕解码路径提供文本或位图。界面字符串由 `quick/localization.py` 和 QML 翻译，支持简体中文、繁體中文与 English。切换语言时保留工作区、播放状态和选项实际值。字幕、文件名、个人命名和学习内容保留原文。Windows 文件选择器的系统控件使用系统显示语言。

## 工作区、安装与服务权限

- **安装共享**。开发版使用项目 `.data/` 和 `.cache/`，便携版使用 EXE 旁的 `data/` 和 `.cache/`。数据根保存工作区目录册、`model-profiles.json` 中的文本连接与复核、讲解、对话三项任务分配，以及 `interface-language.json` 中的界面语言。语音模型与对齐器存入共享模型缓存，同一仓库的下载以跨进程锁协调。
- **工作区独有**。默认工作区沿用原数据根，新工作区位于其 `workspaces/` 下。媒体库、准备队列、播放进度、语言、远程许可、聊天和笔记均随工作区。媒体库可引用外部原片，只有显式复制才会把视频与随附字幕放入 `media/`。
- **发布与恢复**。`study.db` 保存收藏、出处及播放记录等数据。`documents/` 中的 JSON 是生成 SRT 的依据，原文 SRT 与完整译文 `.translation.srt` 按发布日志恢复。材料按规范化媒体路径与语言组合（含程度）索引，复用时检查媒体指纹和相关处理配置。重做前备份生成材料。便携搬迁只调整内部媒体引用，保留迁移备份、手写笔记与外部路径。
- **接口权限**。语音默认在本机处理。只有工作区明确允许发送音轨时，外部转写服务才可收到所选音轨。队列逐段核对当前授权与目标服务。文本权限另设，支持停用、仅本机或允许远程。服务凭据可来自环境变量，或用 Windows DPAPI 加密保存。
- **聊天与修订**。模型通过类型化应用操作处理队列、播放、工作区和学习记录。工具按发起时的会话、媒体、语种及选句执行。笔记改写需读取全文并核对修订号。单句修订保留原文、声学时间与其他句子。工具接口不开放任意系统命令或密钥读取。

项目的接口与兼容约定见[集成契约](contracts.md)。启动、回归命令与发布验收入口见[开发入门](development.md)。

## Phase 1 / 2 状态边界重构

工作区偏好通过 `AppSettings.to_dict()` 序列化，安装级角色经缓存的 `Store.model_registry` 读取并通过显式角色入口更新。旧式直接编辑角色字段的调用保留增量适配，`replace()` 不再丢失修改基线。`DraftStorage` 在工作区数据库中保存未提交草稿，布局不再承担笔记正文持久化。

QML 读取 `mediaState`、`queueState`、`chatState`、`downloadState`、`learningState`、`modelState`、`workspaceState`、`applicationState` 与 `notificationState` 的独立信号。Bridge 的旧状态和 list 属性继续作为兼容视图，聊天与队列 ListView 使用稳定 ID 的 `messageModel/jobModel`。Markdown 缓存仅重新解析正文变化的消息。

队列变更与任务完成驱动界面和工作区切换；收件箱复用单线程池。媒体库摘要和批量播放位置在后台读取，以文件 stat、媒体指纹、语言和 `.pending` 状态检查缓存，界面用代次拒绝迟到结果。模型、教学偏好和字幕修订弹窗统一由 Main.qml 管理，并使用发起控件所属窗口。

上述边界已通过 Phase 1 / 2 架构审阅，后续六项修正和 Phase 3 / 4 拆分见下节。原阶段设计与验证见 [Phase 1 / 2 审阅报告](refactor-phase1-2.md)。

## Phase 3 / 4 模块边界（待最终审阅）

`pipeline.prepare_media()` 是公开兼容入口，实际执行 `preparation.py` 的七个显式阶段；`PreparationContext` 冻结请求设置，`PreparationPlan` 保存来源、角色和复用决策。复核、教学、译稿核验适配同一个 `ConcurrentBatchScheduler`，各自提供提示词、校验和合并策略；传输由组合的 `LlmClient` 负责。客户端按请求惰性取得凭据，响应元数据不含凭据。`unicode_words.py` 提供无 Qt 的边界实现。新阶段使用 `stage_code`，旧记录仍有展示兼容回退。

`Bridge(QObject)` 负责 Qt 接口和组合装配。播放、媒体库/收件箱、队列、聊天、收藏、笔记、模型、设置各有领域状态和有限端口。`feature_services.py` 集中绑定及旧成员适配，`features.py` 只保留旧 import；新代码不依赖业务 Mixin。`BackgroundTaskManager` 管理工作线程、取消令牌、完成事件和共享执行池。`DialogPort` 支持替换原生对话框。`commands.py` 同时提供 UI 分发和错误域元信息；可信 UI 路由与 Agent 工具白名单独立。

工作区控制器和 `quick/main.py` 只调用公开端口；文本授权服务与工作区动作移入 quick 层，旧根模块保留兼容导出。工作区切换摘要通过 `_open_session(..., switch_summary=...)` 显式传入，不再动态塞入 Bridge 属性。原始请求的工作区、会话、媒体、语言、修订版本和模型快照仍在任务开始前冻结。

`Store.database` 统一 study.db schema 和版本化迁移；Notebook、Conversation、Draft 复用它。嵌套事务用 savepoint，成组读取可用 `Database.session()` 复用连接，最外层范围结束关闭句柄。聊天新会话、旧投影及迁移标记原子写入，UI 无 SQL。文档 JSON、`.pending` 和便携媒体引用契约保持；旧 QWidget 文件没有变化。

模块依赖图、代码行数、完整兼容清单、失败记录及逐阶段验证见 [Step 0 / Phase 3 / Phase 4 审阅报告](refactor-phase3-4.md)。
