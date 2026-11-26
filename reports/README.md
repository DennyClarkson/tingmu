# 听幕验收记录 · Tingmu validation reports

[简体中文](#zh-cn) · [English](#english)

<a id="zh-cn"></a>

## 0.12.0 发布验收

2026-11-23 的发布交接列出了以下结果。日常改动在本机按开发指南验证，结果随 PR 记录。[本次维护最后一次托管检查](https://github.com/DennyClarkson/tingmu/actions/runs/45179352534) · [0.12.0 交接记录](../handoff.md) · [开发环境与检查命令](../docs/development.md)

| 验证对象 | 发布时记录的范围 |
| --- | --- |
| 回归与便携播放 | 离线通过 744 项测试与 113 项子测试，Ruff 与离线依赖锁检查通过。两份 EXE 各通过 16 项检查，包括启动、实际解码、定位、倍速、音轨切换与暂停。 |
| 字幕来源决策 | 在一集视频与配套 ASS 中逐条对照 673 组双语字幕和时码，列出 6 条图像字幕轨，识别出选错集数的外挂字幕。只提供译文时，用其中 29 秒原声做了本机 Whisper CPU 转录与重叠参考检查。此次没有测量整集识别错误率。 |
| 文本模型 | 另用 3 句自编双语字幕发出 1 次真实 DeepSeek Flash 讲解请求，保留 3 句已有译文并验证导出。 |
| Windows 界面与更新 | 在两份随包运行时中操作文件选择器，核对三种界面语言下的处理决定、缺少模型后的恢复提示，以及语言切换后选项和勾选的保留。交接另记录了程序文件与源码比对和学习资料保留检查。 |

对应的本机专项记录位于已忽略的 `.cache/qa/subtitle-agent-v12/`。以下合成用例随源码分发，可在本机复跑。[来源识别](../tests/test_subtitle_sources.py)、[准备流程](../tests/test_agent_preparation.py)、[队列与导出](../tests/test_workflow_queue.py)、[界面语言](../tests/test_interface_language.py)。

## 随仓库保存的历史汇总

以下三个 JSON 均为 **0.5 时代**的脱敏报告，供追溯当时的覆盖范围。

| 文件 | 内容 |
| --- | --- |
| [acceptance-v5.json](acceptance-v5.json) | 自动回归、真实窗口交互、DeepSeek/GPT 文本调用与数据保留 |
| [portable-v5-validation.json](portable-v5-validation.json) | 当时便携包的构建、媒体与已有学习资料保留检查 |
| [v5-reasoning-live.json](v5-reasoning-live.json) | 公开思考与摘要的文本接口实测计数、服务返回模型名 |

其他版本按日期和版本在[交接记录](../handoff.md)中查找。历史文档中标为本机的样片、原始报告及 `.cache/` 复现路径不会随 Git 克隆分发。

## 共享范围

`.gitignore` 默认忽略新增报告。若需共享，先整理为去除密钥、机器路径和个人学习内容的汇总，人工审阅后再加入允许列表。私人媒体与字幕、原始模型请求或回答、截图、诊断转储和个人数据库留在本机。

## English

### Release validation for 0.12.0

The November 23, 2026 handoff records the checks below. Contributors run the development checks locally and record the results in their pull requests. [Hosted check for this round of maintenance](https://github.com/DennyClarkson/tingmu/actions/runs/45179352534) · [0.12.0 handoff](../handoff.md) · [Local test commands](../docs/development.md)

| Area | What was checked |
| --- | --- |
| Regression tests and portable playback | 744 offline tests and 113 subtests passed, along with Ruff and the offline lockfile check. Each of two executables passed 16 checks covering startup, video decoding, seeking, playback speed, audio track switching, and pause. |
| Subtitle selection | For one episode, 673 bilingual subtitle pairs and their timestamps were compared against the accompanying ASS file. The player identified six image-based subtitle tracks and an external subtitle file for the wrong episode. In the translation-only case, 29 seconds of the original audio were transcribed locally with Whisper on CPU and checked against the overlapping reference subtitles. These checks did not measure recognition accuracy for the full episode. |
| Text model | A single live DeepSeek Flash request generated explanations for three original bilingual test lines. All three supplied translations were preserved and the export was verified. |
| Windows interface and updates | Checks covered the file picker in both bundled runtimes, subtitle selection decisions and missing-model recovery prompts in all three interface languages, and whether selections survived a language switch. The handoff also records checks of packaged files against source and preservation of study data. |

Local evidence for these checks was stored in the ignored `.cache/qa/subtitle-agent-v12/` directory. These synthetic tests ship with the repository and can be run locally. [Source selection](../tests/test_subtitle_sources.py) · [Preparation](../tests/test_agent_preparation.py) · [Queue and export](../tests/test_workflow_queue.py) · [Interface languages](../tests/test_interface_language.py)

### Archived reports

These three redacted JSON reports describe the scope of testing during the **0.5 series**.

| Report | Coverage |
| --- | --- |
| [acceptance-v5.json](acceptance-v5.json) | Automated regression tests, native-window interaction, DeepSeek and GPT text requests, and data retention |
| [portable-v5-validation.json](portable-v5-validation.json) | Portable build checks and preservation of media and existing study materials |
| [v5-reasoning-live.json](v5-reasoning-live.json) | Counts from live requests for provider-exposed reasoning and summaries, and the model names returned by the services |

For other versions, look up the date or version in the [handoff history](../handoff.md). Sample media, original reports, and paths under `.cache/` identified there as local are kept on the test machine.

### Sharing a report

Git ignores new reports by default. To share one, prepare a summary with credentials, machine-specific paths, and personal study content removed. Have it reviewed before adding it to the allowlist. Keep private media and subtitles, raw model requests and responses, screenshots, diagnostic dumps, and personal databases on the local machine.
