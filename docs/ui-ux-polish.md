# 听幕桌面界面审阅记录

本轮在 `ui/ux-and-visual-polish` 开发，基线为 `main` 的 `1a339f6`。审阅源码和 Windows Qt Quick 实窗即可。本文中的界面图来自隔离工作区，词句、文件名、任务与对话均为本地合成样例。

## Phase 1 · 控件与交互

| 范围 | 改动文件与行为 |
| --- | --- |
| 标尺、颜色、图标 | `Theme.qml`、`Icons.js`、`qmldir`。控件与紧凑行高分别为 34 / 28 px，圆角为 6 / 8 / 12 px，禁用透明度为 0.42。保留四种安装主题与随包字体。 |
| 色彩层次 | `isDark` 选择深浅配色。`accentHover / accentPressed / accentSoft` 区分主操作和轻选中。`danger / dangerSoft / dangerHover / dangerPressed`、`warningSoft` 和 `organizedSoft` 配套语义前景色。`surfaceElevated` 承载弹层，`modalScrim` 在深、浅主题分别使用 55%、28% 黑色。`scrollThumb / scrollThumbHover / tooltipBg / tooltipBorder` 统一附属控件。 |
| 基础控件 | `TButton.qml`、`Field.qml`、`Toggle.qml`、`Choice.qml`、`TrackSlider.qml`。补齐主次、危险、悬停、按压及键盘焦点反馈。可清空输入框预留图标位，同时支持 Esc。下拉允许独立配置弹框最小宽度。 |
| 复用组件 | 新增 `AppScrollBar.qml`、`AppProgressBar.qml`、`AppToolTip.qml`、`AppMenuSeparator.qml`、`AppTextArea.qml`、`StatusBadge.qml`。滚动条宽 6 px，不定进度采用滑动动画。多行输入沿用原生编辑器与 Flickable 的光标跟随。 |
| 弹层及使用方 | `AppDialog.qml`、`AppMenu.qml`、`SettingPopup.qml`、`Main.qml`、`DownloadProgress.qml`、`SubtitleCorrection.qml`、`TeachingPreferences.qml`、`WorkspacePicker.qml`、`WorkspaceSetup.qml`、`DeveloperPanel.qml`、`ModelManager.qml`、`QueuePanel.qml`、`CollectionInbox.qml`、`LearningPanel.qml`、`CompanionContent.qml`、`CompanionPanel.qml`。弹窗按 Qt 按钮角色分配样式，破坏性确认可指定动作名称。列表为行尾操作留出滚动空间。 |
| 播放与搜索 | `PlayerPanel.qml`、`SubtitlePanel.qml`、`LibraryPanel.qml`。空播放器可以直接点“打开视频”。单击跟随系统双击时间间隔，双击取消待执行的播放切换。快捷键直接响应。字幕空搜索直接使用原有模型，输入后才对原文、译文、读音过滤。媒体库按可见集合全选与取消，移出媒体后同步清理选择。 |
| 对话与复制 | `ChatPanel.qml`、`ChatReasoning.qml`、`MarkdownView.qml`。取消编辑恢复进入编辑前的输入草稿与焦点。改名预填、全选当前标题，回车提交。消息、思考和代码的复制反馈各自计时。 |
| 翻译 | `i18n/en.json`、`i18n/zh-TW.json` 同步收录新增控件文案。已有状态与证据翻译沿用原有入口。 |

### 自动化回归

- `tests/test_ui_polish_media.py` 使用真实点击验证空播放器按钮、双击与单击、即时快捷键、字幕筛选中的跳转与空态、媒体库全选与移出，以及徽标随状态变化。
- `tests/test_ui_polish_chat.py` 覆盖按钮和 Esc 取消、提交编辑时保留消息 ID、改名预填与回车、三个复制按钮各自恢复及系统剪贴板内容。
- `tests/test_ui_polish_windows.py` 从正在运行的 QML 引擎读取颜色，按 WCAG 相对亮度计算对比度，并在四种主题、三种语言、两种尺寸下切换页面、设置、弹窗和全屏。

## Phase 2 · 工作区与桌面窗口

| 范围 | 改动文件与行为 |
| --- | --- |
| 主窗口与停靠 | `Main.qml`、`DockPanel.qml`、`SegmentBar.qml`。顶部三视图共用分段底槽；底部状态栏增加分隔线。40 px 的模块标题栏保留拖动、浮动及放回操作，用矢量抓手和胶囊区分标题与计数。 |
| 放映 | `PlayerControls.qml`、`PlayerPanel.qml`、`LibraryPanel.qml`、`SubtitlePanel.qml`、`CaptionLayer.qml`。圆形播放钮的三态共用主题语义；倍速弹框最小 96 px。点击时长可在总时长与剩余时间之间切换，两个播放窗口共享偏好。全屏浮层使用矢量缩放抓手。窄栏缩短操作标签并保留完整提示。 |
| 对话 | `ChatPanel.qml`、`ChatReasoning.qml`、`MarkdownView.qml`。用户消息使用有边框的卡片，伴侣回复使用带署名的文档排版。工具记录有状态徽标；代码标题和内容共用一个容器。短窗保留消息阅读高度，英文工具行在空间不足时分为两行。 |
| 伴侣 | `CompanionPanel.qml`、`CompanionContent.qml`。原文、操作、译文、讲解、读音和词卡依次呈现。讲解使用轻强调色；已收藏、已整理和再次收藏确认各有状态反馈。窄停靠区的操作可换行。 |
| 学习与准备 | `LearningPanel.qml`、`CollectionInbox.qml`、`QueuePanel.qml`、`StatusBadge.qml`。学习页共用分段器，复习卡宽度上限 760 px；题目和答案可滚动，评分按钮保持可达。单条删除确认保存打开时的收藏 ID。队列徽标在卡片顶部，媒体库区分已准备、进行中、失败及缺失。 |
| 管理 | `WorkspaceSetup.qml`、`WorkspaceRow.qml`、`ModelManager.qml`、`DeveloperPanel.qml`、`NavButton.qml`。工作区与连接列表使用一致的选中指示。连接表单分组，开发者窗口左侧切换运行信息与日志。 |
| 滚动容器与确认窗口 | `AppScrollView.qml` 给 ScrollView 的垂直滚动条明确设置宿主、右侧坐标和高度；用于伴侣、笔记、复习、模型与工作区表单、纠正、思考记录和模块设置。`AppScrollBar.qml` 给溢出内容保留低调的滚动提示。`SettingPopup.qml` 和确认窗口补充测试名称。 |

第二阶段新增回归覆盖总时长/剩余时间切换与持久化、紧凑倍速选择及窄窗英文标签；280 / 300 px 伴侣面板中的操作与 380 / 920 px 对话工具行；工具状态原位更新。`tests/test_ui_polish_learning.py` 覆盖收藏删除的取消、确认与目标身份，复习卡居中、长答案滚动及评分按钮，队列徽标和右侧安全距离。

逐文件比对基线中的 **195 个 objectName 表达式**，全部保留。英文和繁中目录各有 1083 个键，本轮各增加 18 个，原有键值完全保留。自动化校验覆盖 648 个不同的 QML 直译字面量。播放进度、消息和任务继续订阅领域状态或增量模型。既有测试、旧 QWidget、LLM 操作接口与打包脚本均无差异。

## Windows 真窗口截图

截图测试要求 `QApplication.platformName() == "windows"`，通过 `QQuickWindow.grabWindow()` 保存实际渲染结果。测试在隔离数据目录创建两条媒体记录、两条队列记录、词卡、短对话和本机模型配置。打开状态使用合成字幕与空视频帧。桌面为 1920 × 1080；主窗实测 1480 × 930 和 980 × 620，独立聊天为 920 × 760 和 920 × 572，模型管理为 840 × 720 和 840 × 584。

已逐图检查 **40 张 PNG**。下表中的图片链接指向本机被 Git 忽略的 [QA 目录](../.cache/qa/ui-polish/)。另外两套主题在繁中界面抽查播放、聊天及全屏。引擎警告检查在全部 24 个主题、语言、窗口尺寸组合中执行。

| 画面 | 森林 · 简中 | 纸页 · 简中 | 森林 · 窄窗英文 | 纸页 · 窄窗英文 | 实窗核查 |
| --- | --- | --- | --- | --- | --- |
| 放映空态 | [PNG](../.cache/qa/ui-polish/forest-1480-zh-CN-watch.png) | [PNG](../.cache/qa/ui-polish/paper-1480-zh-CN-watch.png) | [PNG](../.cache/qa/ui-polish/forest-980-en-watch.png) | [PNG](../.cache/qa/ui-polish/paper-980-en-watch.png) | 欢迎卡主按钮和拖入提示完整 |
| 字幕与伴侣 | [PNG](../.cache/qa/ui-polish/forest-1480-zh-CN-subtitles.png) | [PNG](../.cache/qa/ui-polish/paper-1480-zh-CN-subtitles.png) | [PNG](../.cache/qa/ui-polish/forest-980-en-subtitles.png) | [PNG](../.cache/qa/ui-polish/paper-980-en-subtitles.png) | 时间码、原文、操作和滚动条分开，窄栏操作换行 |
| 准备与队列 | [PNG](../.cache/qa/ui-polish/forest-1480-zh-CN-prepare.png) | [PNG](../.cache/qa/ui-polish/paper-1480-zh-CN-prepare.png) | [PNG](../.cache/qa/ui-polish/forest-980-en-prepare.png) | [PNG](../.cache/qa/ui-polish/paper-980-en-prepare.png) | 两条任务的状态、进度、重排队及移出操作清晰 |
| 学习收藏 | [PNG](../.cache/qa/ui-polish/forest-1480-zh-CN-learn.png) | [PNG](../.cache/qa/ui-polish/paper-1480-zh-CN-learn.png) | [PNG](../.cache/qa/ui-polish/forest-980-en-learn.png) | [PNG](../.cache/qa/ui-polish/paper-980-en-learn.png) | 侧栏与分隔线之间有留白，收藏状态和删除入口可见 |
| 间隔复习 | [PNG](../.cache/qa/ui-polish/forest-1480-zh-CN-review.png) | [PNG](../.cache/qa/ui-polish/paper-1480-zh-CN-review.png) | [PNG](../.cache/qa/ui-polish/forest-980-en-review.png) | [PNG](../.cache/qa/ui-polish/paper-980-en-review.png) | 宽窗卡片居中，长答案可滚动，四项评分留在卡内 |
| 独立聊天 | [PNG](../.cache/qa/ui-polish/forest-1480-zh-CN-chat.png) | [PNG](../.cache/qa/ui-polish/paper-1480-zh-CN-chat.png) | [PNG](../.cache/qa/ui-polish/forest-980-en-chat.png) | [PNG](../.cache/qa/ui-polish/paper-980-en-chat.png) | 双角色消息完整，窄窗的输入与设置行可读 |
| 工作区设置 | [PNG](../.cache/qa/ui-polish/forest-1480-zh-CN-workspace.png) | [PNG](../.cache/qa/ui-polish/paper-1480-zh-CN-workspace.png) | [PNG](../.cache/qa/ui-polish/forest-980-en-workspace.png) | [PNG](../.cache/qa/ui-polish/paper-980-en-workspace.png) | 遮罩、选中区、表单和完成按钮有层次，长表单向下滚动 |
| 全局模型 | [PNG](../.cache/qa/ui-polish/forest-1480-zh-CN-models.png) | [PNG](../.cache/qa/ui-polish/paper-1480-zh-CN-models.png) | [PNG](../.cache/qa/ui-polish/forest-980-en-models.png) | [PNG](../.cache/qa/ui-polish/paper-980-en-models.png) | 选中连接与表单明确分区，窄窗内可滚动到保存与删除 |

四种主题的全屏顶栏、底栏与伴侣抓手均在 1920 × 1080 原生全屏检查。[forest](../.cache/qa/ui-polish/forest-1480-zh-CN-fullscreen.png) · [paper](../.cache/qa/ui-polish/paper-1480-zh-CN-fullscreen.png) · [mist](../.cache/qa/ui-polish/mist-1480-zh-TW-fullscreen.png) · [graphite](../.cache/qa/ui-polish/graphite-1480-zh-TW-fullscreen.png)

繁中补充画面包含 [mist 字幕](../.cache/qa/ui-polish/mist-1480-zh-TW-subtitles.png) · [mist 对话](../.cache/qa/ui-polish/mist-1480-zh-TW-chat.png) · [graphite 字幕](../.cache/qa/ui-polish/graphite-1480-zh-TW-subtitles.png) · [graphite 对话](../.cache/qa/ui-polish/graphite-1480-zh-TW-chat.png)。

### 复现截图

在仓库根目录使用 PowerShell 运行。测试使用捆绑字体、Qt 软件绘制后端和本机 FFmpeg。测试运行器限制外部网络，仅允许本机模拟服务。

```powershell
$qa = Join-Path $PWD '.cache/qa/ui-polish/reproduce'
New-Item -ItemType Directory -Force $qa | Out-Null
$env:TEMP = $qa
$env:TMP = $qa
$env:TINGMU_CACHE_DIR = Join-Path $qa 'runtime'
$env:TINGMU_DATA_DIR = Join-Path $qa 'data'
$env:QML_DISK_CACHE_PATH = Join-Path $qa 'qml'
$env:QT_QPA_PLATFORM = 'windows'
$env:QT_QUICK_BACKEND = 'software'
$env:PYTHONUTF8 = '1'
$env:HF_HUB_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:TINGMU_POLISH_SCREENSHOTS = '1'
.\.venv\Scripts\python.exe .github/run_offline_tests.py -q --require-ffmpeg --basetemp="$qa/pytest" tests/test_ui_polish_windows.py
```

### 验证期间修正与残余不确定性

首次控件集成暴露了 Qt Basic 多行占位文字的宽度绑定循环，以及主按钮亮色前景返回无效 QColor 的问题。分别改用原生 TextArea 与 Flickable 的挂接方式和有效的主题颜色别名。实际主题配对最低对比度为 **4.89:1**，禁用控件另外应用指定的 0.42 透明度。

前期截图脚本误设平台变量，产生过离屏图。这批图已由带平台断言的 Windows 截图覆盖。目视检查发现的滚动条遮住时间码、宽屏复习卡偏左及窄窗侧栏和伴侣操作溢出均已修正。矩阵测试按名称在实际宿主中依次打开和关闭弹窗，以免保留已销毁的临时 Qt 对象。

两个阶段的早期全量进程各遇到一次 Python 垃圾回收期间的 PySide 原生访问异常，分别落在模块设置与旧草稿恢复测试。相同模块设置套件连续五轮共 30 项通过。历史记录也有同类原生访问异常；现有证据尚未定位根因。原始失败输出保留于 [第一阶段日志](../.cache/qa/ui-polish/p1-suite/output.txt) 和 [第二阶段日志](../.cache/qa/ui-polish/p2-full/output.txt)。

## 独立验证记录

### Phase 1

离线专项包含新增交互、主题颜色与页面矩阵、三语目录和既有热路径回归。完整离线复跑在进入第二阶段之前结束。下面保留终端原文。

```text
................................                                         [100%]
32 passed in 39.75s

......................................... [  4%]
........................................................... [ 10%]
.................................................................................................. [ 20%]
........................................................................ [ 27%]
........................................................................ [ 34%]
........................................................................ [ 42%]
........................................................................ [ 49%]
........................................................................ [ 56%]
........................................................................ [ 64%]
................................................. [ 69%]
........................................................................ [ 76%]
........................................................................ [ 83%]
........................................................................ [ 90%]
........................................................................ [ 98%]
.................                                                        [100%]
============================== warnings summary ===============================
tests/test_agent.py::ValidationTests::test_blank_chinese_readings_use_complete_local_dictionary
  E:\GitProjs\ZhihuHacathonProj\SubtitleAssist\tingmu\.venv\Lib\site-packages\wordseg\__init__.py:1: UserWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html. The pkg_resources package is slated for removal as early as 2025-11-30. Refrain from using this package or pin to Setuptools<81.
    import pkg_resources

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
984 passed, 1 warning, 113 subtests passed in 147.05s (0:02:27)
```

### Phase 2 原生 Windows 专项

在首轮已验证的原生截图运行中，Qt 实际平台为 `windows`，屏幕几何为 `QRect(0, 0, 1920, 1080)`。补强按名称查找弹窗的回归后再次执行相同专项。以下为最终原文。

```text
........................................................................ [ 80%]
..................                                                       [100%]
90 passed in 90.20s (0:01:30)
```

专项执行的测试文件包括四个 `test_ui_polish_*.py`、`test_interface_language.py`、`test_qml_workspace.py`、`test_qml_v4.py`、`test_qml_v5.py`、`test_step0_qml_hotpaths.py`、`test_draft_exit_guards.py`、`test_export_dialog_ports.py` 和 `test_switch_draft_rollback.py`。

### 最终完整离线验证原文

以下四个命令均从仓库根目录执行，退出码均为 0。空白检查没有标准输出。完整测试在 `QT_QPA_PLATFORM=offscreen`、`QT_QUICK_BACKEND=software`、两项 Hugging Face 离线开关和仓库内独立缓存、数据及临时目录下运行。

```text
> uv lock --check --offline
Resolved 68 packages in 0.86ms

> .venv\Scripts\python.exe -m ruff check .
All checks passed!

> git diff --check


> .venv\Scripts\python.exe .github/run_offline_tests.py -q --require-ffmpeg
......................................... [  4%]
........................................................... [ 10%]
.................................................................................................. [ 19%]
........................................................................ [ 27%]
........................................................................ [ 34%]
........................................................................ [ 41%]
........................................................................ [ 48%]
........................................................................ [ 56%]
........................................................................ [ 63%]
................................................. [ 68%]
........................................................................ [ 75%]
........................................................................ [ 82%]
........................................................................ [ 90%]
........................................................................ [ 97%]
.........................                                                [100%]
============================== warnings summary ===============================
tests/test_agent.py::ValidationTests::test_blank_chinese_readings_use_complete_local_dictionary
  E:\GitProjs\ZhihuHacathonProj\SubtitleAssist\tingmu\.venv\Lib\site-packages\wordseg\__init__.py:1: UserWarning: pkg_resources is deprecated as an API. See https://setuptools.pypa.io/en/latest/pkg_resources.html. The pkg_resources package is slated for removal as early as 2025-11-30. Refrain from using this package or pin to Setuptools<81.
    import pkg_resources

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
992 passed, 1 warning, 113 subtests passed in 164.34s (0:02:44)
```

本轮新增 30 个参数展开后的回归用例，既有 962 项与 113 项子测试均保留。唯一警告来自第三方 `wordseg/pkg_resources`。分支停在工作树审阅点。

## 整体审阅后的四项修复

| 修复 | Windows 实窗复核 |
| --- | --- |
| 伴侣顶栏按父布局可用宽度排版，标签靠左、来源与重听靠右 | 1480 × 930 简中截图中，「当前句」与「来源」的场景 Y 均为 119 px。单独测量 320 / 364 px 简中栏，纵向差均为 0 px。280 px 英文栏的操作下移 36 px，四个控件都在栏内。 |
| 滚动条仅在 `0 < size < 0.999` 时显示，140 ms 渐变只用于交互反馈 | 新增断言从溢出切换到 0、1 和 0.9995，同步读取到的滑块透明度均为 0。截图时读取了 70 个滚动条状态，56 个隐藏、14 个随内容溢出显示。 |
| `TButton`、`Toggle`、`Choice` 的手形指针跟随启用状态 | 三种控件依次启用、禁用、再次启用时，手形指针处理器同步切换。 |
| 聊天的会话、模型及思考强度下拉框统一使用常规控件高度 | 常规聊天窗实测均为 34 px；紧凑聊天窗均为 30 px。 |

原生窗口专项 **21 passed in 40.35s**，24 个主题、语言与尺寸组合均无 QML 引擎警告。更新了观看、字幕、模型、工作区和聊天的 24 张截图。字幕短列表、单连接侧栏和短对话没有满高灰条；窄窗的溢出表单保留滚动提示。回归位于 `tests/test_ui_polish_chat.py` 与 `tests/test_ui_polish_windows.py`。[截图目录](../.cache/qa/ui-polish/) · [截图时的几何与透明度](../.cache/qa/ui-polish/review-native/frames.json) · [原生专项输出](../.cache/qa/ui-polish/review-native/output.txt)

四项离线检查全部通过。锁文件解析 68 个包，Ruff 通过，`git diff --check` 无输出。完整测试为 **994 passed, 1 warning, 113 subtests passed in 158.45s**。唯一告警仍来自第三方 `wordseg` 导入 `pkg_resources`。[锁文件原文](../.cache/qa/ui-polish/review-lock/output.txt) · [Ruff 原文](../.cache/qa/ui-polish/review-ruff/output.txt) · [完整测试原文](../.cache/qa/ui-polish/review-full/output.txt)
