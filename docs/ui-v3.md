# 听幕 Qt Quick 工作区

入口为 `src/tingmu/quick/qml/Main.qml`，由 `quick/main.py` 注册 `backend` 并创建。界面使用 Qt Quick、Qt Quick Controls Basic 和 Qt Multimedia，控件配色与本地 SVG 图标随包提供。旧 QWidget 工作区不参与该入口。

## 使用

- **观看**：媒体库、主播放器、学习伴侣和滚动字幕。**准备**：媒体库与处理队列。顶部“对话”打开独立聊天窗口。
- 模块间细线调整宽高。拖动模块标题到另一模块，出现绿色目标框后释放交换位置；拖出工作区弹出独立窗口。模块右上角箭头弹出或放回。关闭浮动窗口会放回工作区，不销毁模块。
- “工作区”菜单可打开全部六个模块或恢复默认布局。观看、准备的模块位置分别保存；窗口大小、分隔比例、浮动窗口位置、播放器选项、浮层尺寸及笔记草稿保存在应用布局数据中。
- 文件选择器支持多个视频；目录扫描保留原始路径，不复制媒体。也可把视频拖到主窗口。媒体库勾选后加入队列，每个任务显示状态、进度及错误。队列可暂停、继续、重试失败项、删除待处理项、清除已完成项。
- 设置分别在模块齿轮中。队列管理 ASR、设备、处理音轨、全文复核和批量讲解；伴侣管理语言、程度与显示；字幕管理阅读时间轴；聊天独立选择陪伴模型；播放器管理播放音轨、嵌入字幕、画面适应方式、学习字幕与全屏浮层。
- 学习伴侣支持逐词收藏、当前句笔记、跨视频定位收藏、删除、导出和复盘。草稿按媒体路径、已知语言、学习语言及句 ID 保存；“来源”分别显示原始源时间、必要时的分句展示时间、原始文本、复核说明与时间依据。文本始终按纯文本渲染。

## 播放与全屏

主播放器与全屏共用一个 `VideoOutput.videoSink`，仅改变其视觉父节点，不重建 `QMediaPlayer`。全屏伴侣为固定宽高的可滚动面板：拖动标题移动，右下角拖动调整宽高。字幕长度、字号、释义及讲解变化不会改变浮层几何。字号、透明度和显示项可在播放器设置中调整。

快捷键：空格播放/暂停，左右箭头前后 5 秒，上下箭头前后句，F 全屏，Esc 退出全屏，M 静音，Ctrl+O 打开视频，Ctrl+Shift+O 打开目录。编辑文本时播放快捷键停用；聊天 Ctrl+Enter 发送。

## 组件与验证接口

“帮助 → 开发者模式”默认关闭，开关由应用设置持久保存；开启时记录 DEBUG 级别日志，关闭后恢复 INFO。开启后出现“查看日志”，打开独立开发者窗口，不占观看工作区。窗口分为运行信息和日志，提供等级筛选、文字搜索、跟随最新、可选择的纯文本详情，以及刷新、导出诊断和打开日志文件夹。关闭开发者模式会隐藏该窗口。Python 桥在开发者模式下每 2 秒更新诊断和日志；不记录字幕与对话正文。

开发者界面为 `DeveloperPanel.qml`，窗口 `open()` 方法先请求刷新。桥新增只读 `diagnostics` 和 `logs` 属性，以及 `refreshDiagnostics`、`exportDiagnostics`、`openLogFolder` 命令。稳定对象名为 `developerWindow`、`developerLogList`、`developerDiagnostics`、`developerModeAction`、`viewLogsAction`、`refreshDiagnostics`、`exportDiagnostics`、`openLogFolder`。

`DockPanel` 包装每个模块，`loadedItem` 为模块实例，`detach()` 切换浮动状态。播放器实例提供 `toggleFullscreen()`、`openSettings()`。`Main.changeView("watch"|"prepare")` 切换视图，`finishDrop(moduleId, globalPoint)` 完成放置，`resetWorkspace()` 恢复默认。

稳定对象名：`mainWindow`、`workspaceSplit`、`libraryModule`、`queueModule`、`playerModule`、`subtitleModule`、`companionModule`、`chatModule`、`playerVideo`、`fullscreenWindow`、`companionOverlay`、`overlayDrag`、`overlayResizeGrip`、`overlayScroll`、`playerSeek`、`playerPlay`、`enterFullscreen`、`leaveFullscreen`、`subtitleList`、`libraryList`、`queueList`、`chatHistory`、`chatInput`、`learningCaption`。模块齿轮与浮动按钮使用 `<moduleId>Settings`、`<moduleId>Detach`。

QML 通过只读属性 `state/settings/playback/cues/activeCue/activeIndex/library/jobs/messages/entries/layout` 读取数据，经 `command(name,payload)`、`setting(name,value)`、`saveLayout(map)`、`attachVideo(sink)` 发出请求。源字幕与阅读时间轴的正确性、任务取消、路径校验、文件对话框和存储均由 Python 桥负责。

全目录 `pyside6-qmllint` 已做语法检查；因 `backend` 为运行时 context property，静态检查会报告未限定访问。实际窗口、视频解码、浮层交互和便携 EXE 的验收结果由根任务记录于 handoff 与测试报告。
