# 听幕 0.12.0 架构与扩展点

听幕在 Windows 上以 Python 和 Qt Quick/QML 运行。一次观看涉及三层数据。音轨或字幕提供有来源的原句，准备流程生成工作区的学习文档，播放器按当前时间从文档取显示行。修改其中一层时，要保留下一层追溯到原句的能力。

## 从入口找到代码

以下路径相对于 `src/tingmu/`。

| 修改目标 | 先看这里 | 对应回归 |
| --- | --- | --- |
| 启动、切换工作区、切换界面语言 | `quick/main.py`、`quick/workspace_controller.py`、`workspaces.py`、`quick/localization.py`、`quick/i18n/` | `test_qml_workspace.py`、`test_interface_language.py` |
| 播放、全屏、音轨和显示字幕 | `quick/bridge.py`、`quick/qml/`、`quick/playback_sources.py`、`quick/embedded.py`、`embedded_subtitles.py` | `test_qml_v5.py`、`test_player_sources.py`、`test_embedded_subtitles.py` |
| 入队、选择字幕来源、失败重试 | `media_inbox.py`、`batch.py`、`subtitle_sources.py`、`pipeline.py` | `test_workflow_queue.py`、`test_subtitle_sources.py`、`test_agent_preparation.py` |
| 语音识别、原文复核、讲解、译稿核验 | `media.py`、`speech.py`、`external_asr.py`、`review.py`、`agent.py`、`translation_review.py` | `test_external_asr.py`、`test_pipeline_review.py`、`test_agent_concurrency.py` |
| 模型与授权、学习对话、受限操作 | `global_models.py`、`providers.py`、`credentials.py`、`companion.py`、`app_actions.py` | `test_global_models.py`、`test_conversations.py`、`test_app_actions_v5.py` |
| 文档发布、播放时间轴、收藏与笔记 | `storage.py`、`timeline.py`、`lesson_material.py`、`corrections.py`、`notebooks.py` | `test_storage.py`、`test_timeline.py`、`test_word_recollection.py` |

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
