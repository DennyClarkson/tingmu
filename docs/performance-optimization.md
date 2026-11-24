# 听幕运行性能优化

基线为 `main@948446e`，待审阅分支为 `perf/runtime-optimization`。在同一台 Windows 11（build 26200）机器、同一套 Python 3.12.14、PySide6 6.12.0、NumPy 2.5.4 和 SQLite 3.53.1 环境上，先运行测量脚本并保存 Before，再修改运行代码。

原生首帧中位数缩短 **476 ms**，首帧工作集减少 **98.5 MiB**，主窗口对象树减少 **3,413 个 QObject**。每次扫描 30 个稳定视频的写锁探测从 30 次降为 0 次。4 段素材、800 句字幕的本地准备吞吐从 **3,405 增至 4,591 句/秒**。

## 实测

| 工作负载与单位 | Before | After | 变化 |
| --- | ---: | ---: | ---: |
| 原生首帧延迟（ms） | 1713.89 | 1237.80 | -27.8% |
| 主窗口对象树（QObject） | 7727 | 4314 | -44.2% |
| 首帧进程工作集（MiB） | 371.24 | 272.70 | -26.5% |
| 30 个就绪视频扫描一轮（ms） | 8.94 | 1.48 | -83.4% |
| 每轮 Windows 写锁探测（次） | 30 | 0 | -100.0% |
| 6.2 秒空闲收件箱定时器唤醒（次） | 3 | 0 | -100.0% |
| 同一窗口内收件箱 UI 通知（次） | 6 | 0 | -100.0% |
| 同一窗口内进程 CPU 时间（ms） | 0 | 0 | 持平 |
| Python 读取队列领域状态 5,000 次（ms） | 11.61 | 2.70 | -76.8% |
| SQLite 逐次打开和关闭，读 50 条（ms） | 64.72 | 58.34 | -9.9% |
| SQLite 一个显式会话，读同样 50 条（ms） | 1.91 | 1.62 | -15.5% |
| 150 条收藏的笔记快照（ms） | 9.68 | 2.27 | -76.5% |
| 同一笔记快照的连接打开次数 | 5 | 1 | -80.0% |
| 150 条收藏同步（ms） | 20.32 | 2.44 | -88.0% |
| 保存 50 条消息及兼容投影（ms） | 4.97 | 4.11 | -17.3% |
| 89 KiB 含链接 Markdown 清洗（ms） | 9.71 | 5.67 | -41.6% |
| 89 KiB 纯文本 Markdown 清洗（ms） | 6.386 | 0.054 | -99.2% |
| 禁用 DEBUG 时记录 1,000 条事件（ms） | 4.834 | 0.087 | -98.2% |
| 读取 200 条未变日志（ms） | 0.483 | 0.038 | -92.1% |
| 600 句中间检查点（ms） | 21.08 | 7.36 | -65.1% |
| 检查点替换文件的累计大小（B） | 324512 | 177017 | -45.5% |
| 检查点显式 fsync（次） | 2 | 1 | -50.0% |
| 600 句最终发布（ms） | 21.67 | 15.83 | -27.0% |
| 最终发布替换文件的累计大小（B） | 324509 | 242725 | -25.2% |
| 最终发布显式 fsync（次） | 2 | 2 | 持平 |
| 50 次进度更新及阶段、终态的队列写入（次） | 53 | 3 | -94.3% |
| 512 × 4096 帧的 overlap-add（ms） | 3.03 | 2.04 | -32.7% |
| 10 秒单声道频谱降噪（ms） | 28.00 | 15.91 | -43.2% |
| 该降噪调用的峰值追踪分配（KiB） | 8633.9 | 3607.0 | -58.2% |
| 本机 HTTP/1.1 发出 48 个请求、8 线程（ms） | 397.39 | 11.82 | -97.0% |
| 服务端接受的 TCP 连接（条） | 48 | 8 | -83.3% |

- **启动**使用五个全新 Windows 进程、五个空工作区、独立的空 QML 磁盘缓存与 Qt 软件绘制。计时从父进程发起启动到第一个 `frameSwapped`。对象数包括主窗及其 QObject 子树，工作集取自 Windows `GetProcessMemoryInfo`。操作系统文件页缓存由 Windows 管理。
- **空闲**使用独立的 Bridge 进程和 Qt 事件循环。两次 6.2 秒采样的进程 CPU 时间都落在 Windows 计时器分辨率以下。可以确认收件箱唤醒和通知减少；此次数据无法量化整机功耗或空闲 CPU 百分比变化。
- **SQLite**两种 50 条读数均查询同一批来源。两个版本本来就都支持显式会话。产品路径的改进可单独从笔记快照的连接数、快照与同步耗时看出。
- **写盘**统计 Python 显式 `os.fsync` 和成功 `os.replace` 的文件大小之和。后者包括发布时移动已写好的暂存 SRT，不能作为物理磁盘写入量。600 句最终 JSON 文件实际从 291,725 B 降至 177,014 B（−39.3%）；两份源语言 SRT 均为 32,782 B。SQLite 内部同步和设备缓存没有用系统级追踪工具计数。
- **音频**使用固定种子生成 160,000 个 16 位 PCM 样本。最终两份输出逐样本比较，最大绝对差为 0。峰值是 `tracemalloc` 在该调用期间能观测到的分配量。ONNX 推理质量和模型权重占用未纳入本次测量。
- **HTTP**使用绑定 `127.0.0.1` 的 HTTP/1.1 服务，五轮各发出 48 个请求，直接连接场景显式模拟无系统代理。服务端统计实际 accept 次数。时延含连接与旧版 opener 构造开销，与公网 TLS 或模型推理服务的耗时口径不同。

### 批量准备吞吐

补充实验用 `git archive 948446e` 导出原始源码，再同当前源码分别运行同一份 `tests/perf_pipeline.py`。脚本核对实际导入路径。每轮在新工作区生成 4 个静音 WAV 和 4 份各 200 句的 SRT，运行真实 `PreparationQueue.run()`、字幕导入、本地分词、检查点与最终发布。两边都预热一次，取后续五轮中位数，计时不含素材生成。没有加载 ASR 模型或调用文本模型。

| 每轮 4 个任务、800 句 | Before | After |
| --- | ---: | ---: |
| 完整队列耗时 | 234.98 ms | 174.24 ms |
| 完整队列吞吐 | 3,404.51 句/s | 4,591.37 句/s |
| 文档保存次数 | 16 | 16 |
| Python 显式 fsync | 40 | 40 |

吞吐提高 **34.9%**。此快速任务各次进度更新间隔很短，前后都需要相同数量的持久化操作。表中另一个 50 次进度更新实验模拟较长准备阶段，覆盖了原来每 500 ms 落盘的路径。本次逐轮记录保存在 `.cache/qa/performance/supplemental-pipeline/before.json` 和 `.cache/qa/performance/supplemental-pipeline/after.json`。

## Phase 1

| 文件 | 运行行为及边界 |
| --- | --- |
| `media_inbox.py`、`quick/library_controller.py`、`quick/bridge.py` | Win32 签名只绑定一次，稳定文件复用写锁探测结果。扫描仍以不跟随链接的元数据检查排除替换后的重解析点。目录监视器合并事件，等待传输时 2 s 复查，稳定可见时 20 s，隐藏空闲时 60 s。关闭工作区时移除监视路径。后台无变化时保持通知静默；手动扫描仍可见。预检按设置、文件与目录快照失效，应用内设置及模型安装主动失效，外部模型变化由后续轮询复核。 |
| `database.py`、`notebooks.py`、`quick/learning_controller.py` | schema v2 在事务内增加六个索引，分别覆盖收藏语言及 ID、媒体及语言对、两种笔记时间顺序、按语言排序的会话、归档来源。WAL 连接设置 NORMAL 同步、内存临时表和 −16000 KiB 缓存目标。快照使用 SQL 分组计数，同批条目复用给复习；同步复用快照，未变笔记和条目的 Markdown 跳过解析。 |
| `quick/bridge.py`、`quick/playback_controller.py`、`quick/chat_controller.py`、`conversations.py` | 启动、打开媒体、刷新学习中心及切换聊天会话的多查询使用有界会话，退出后关闭 SQLite 句柄。聊天工作线程保留一次隔离副本。新会话保存只对消息做一次 Python 序列化，SQL 在同一事务内生成只含角色与正文的旧表投影。原有可替换钩子及失败回滚保留。 |
| `quick/state.py`、`quick/list_models.py`、`quick/playback_controller.py` | 标量免拷贝，变更只替换受影响的领域快照。Qt 读取列表行时交给 QVariant 转换；Python 快照、`get()` 和 `findById()` 保留隔离。播放 UI 连续通知合并到约 70 ms；定位、暂停、首尾位置立即发布，字幕、嵌入轨及 A–B 循环仍处理实时位置。 |
| `richtext.py`、`quick/qml/MarkdownView.qml`、`SubtitlePanel.qml`、`Icons.js` | 清洗器查找链接边界后分段切片，保留链接白名单、图片禁用和 HTML 转义。流式追加从已闭合围栏后的安全位置继续；稳定块复用原委托和 QTextDocument，尚未闭合的块重新解析。字幕每次文档更新仅取一次 Qt 快照，首次搜索才建立小写索引，输入防抖 110 ms，清空立即生效。图标按名称与颜色复用本地 SVG URL。 |
| `diagnostics.py` | 禁用的事件先判断日志级别；实际输出仍先脱敏。未变 JSONL 复用最近解析结果，按大小、修改时间和文件身份识别追加与轮换。 |

## Phase 2

| 文件 | 运行行为及边界 |
| --- | --- |
| `storage.py`、`batch.py`、`preparation.py`、`portable_paths.py` | 机读文档使用紧凑 JSON，配置仍缩进。中间检查点只写文档；就绪、显式发布或兼容的旧文档按需写字幕。重复检查点比较文件身份及摘要，防止保留大小和时间的外部编辑被覆盖。队列普通进度只更新内存，入队、实际阶段变化和终态仍原子落盘。搬迁未完成文档时按原字节保留上一次已发布的字幕。 |
| `providers.py`、`provider_transport.py` | 同源 HTTP/HTTPS 连接池共用上限 16，空闲 30 s 主动回收，TLS 使用验证证书及主机名的共享上下文。代理及替换 opener 的路径使用原 urllib 处理；本机回环显式绕过代理。3xx 禁止转发；取消、截断、未读完及异常连接退出池。旧连接断开时只允许幂等读取重试一次。Windows 取消测试覆盖等待响应头、正文与错误正文。连接建立前的 DNS、TCP 连接及 TLS 握手仍受请求超时约束。 |
| `speech.py`、`embedded_subtitles.py`、`quick/embedded.py` | 缓存 ONNX 输入名，FFT 按 32 帧分块，持久中间缓冲使用 float32/complex64。降噪以 partition 取相邻秩并线性插值，重叠叠加按步长成批累加并保持每个样本的加法顺序。位图提取通过重命名发布 PNG，轮询只统计新图和正在增长的临时图。保留最多两个字幕页，每页缓存四个按需编码的 URL，翻页和关闭释放暂存文件。 |
| `quick/qml/FirstUseLoader.qml`、`qmldir`、`Main.qml`、`ChatPanel.qml`、`LearningPanel.qml`、`QueuePanel.qml`、`DeveloperPanel.qml`、`ModelManager.qml`、`PlayerPanel.qml`、`WorkspaceSetup.qml`、`SpeechSettings.qml`、`CaptionLayer.qml` | 重型表单和列表在首次使用时装配。外壳、草稿编辑器、必须可定位的隐藏控件及其公共方法保持可用。已打开的面板保留实例以保存输入和滚动位置。0 至 1.5 px 范围中的正描边使用原生 Text.Outline，更粗的描边保留多方向字形。已逐档比对中英文字幕在暗、灰、亮背景上的实际渲染。本机对照图 `.cache/qa/performance/caption-visual/outline-baseline-left-optimized-right.png` 的左侧为基线，右侧为优化结果。 |

### 崩溃恢复与兼容性

既有空白及 `{}` 型 `.pending` 标记继续按旧协议恢复。新版标记记录临时发布身份和目标 JSON 摘要。字幕先写到暂存名，原子替换文档后再安装两份字幕。提交前崩溃会保留上次发布版本，也保留原本不存在的译轨；提交后崩溃则根据已提交 JSON 补齐字幕。手工修订、原字幕 ID、原文与声学时间沿用既有保护。SQLite NORMAL 保留 WAL 事务一致性，突然断电时最近提交的持久性低于 FULL；材料日志和队列关键状态仍使用显式 fsync。

默认下载、凭证处理、模型权限、人工确认路由和外部服务选择沿用原契约。依赖清单、锁文件、字体加载、旧 QWidget 目录以及两份翻译词典均与基线相同。截图与合成测试数据写在仓库内的忽略目录。

## 复现

在仓库根目录使用项目已有的 Windows venv 运行。基线数字是在任何运行代码变更前直接采集的。复测其他版本时，把同一套 `tests/benchmark_performance.py`、`perf_startup.py`、`perf_runtime.py`、`perf_persistence.py` 复制到对应检出的 `tests/`，并让 `PYTHONPATH` 指向该检出的 `src/`。两个版本均需完整的本地 QML、字体和原生 Qt 运行库。

```powershell
$env:PYTHONUTF8 = '1'
$env:PYTHONPATH = (Resolve-Path src).Path
.venv/Scripts/python.exe tests/benchmark_performance.py before
# 在待比较的源码版本与相同环境中运行
.venv/Scripts/python.exe tests/benchmark_performance.py after
.venv/Scripts/python.exe tests/perf_pipeline.py --expected-src $env:PYTHONPATH --output .cache/qa/performance/pipeline.json
```

独立微基准的预热、重复数与时间范围定义在对应函数中。微基准的合成素材在计时前生成。每次运行均使用新目录保存输出 JSON。锁探测和网络连接计数来自对应函数或服务端实际事件。单机中位数会随系统负载波动。本次 Before 原始数据位于 `.cache/qa/performance/before-oi8rltci/results.json`，After 位于 `.cache/qa/performance/after-qugi3zhe/results.json`。

## 验证与回归

Phase 1 的 SQLite、笔记、聊天、播放及收件箱专项共 103 项通过。随后加入流水线、HTTP、音频、位图字幕及按需装配优化，并对最终工作树执行全量离线回归。

| 新增回归文件 | 核查内容 |
| --- | --- |
| `tests/test_performance_runtime.py` | 收件箱文件或目录被替换、预检失效、监视器生命周期、连续播放通知与即时定位、领域状态的隔离。 |
| `tests/test_performance_persistence.py` | 旧库索引迁移、会话结束可移动数据库、笔记及 Markdown 复用、聊天兼容投影、持久化队列状态、字幕发布崩溃恢复、同大小同时间戳的外部修订。 |
| `tests/test_performance_io.py` | 关闭日志级别的快路径、日志缓存、音频重叠叠加顺序、位图按需编码及部分完成工作区的搬迁。 |
| `tests/test_provider_keepalive.py` | 连接并发上限、复用与空闲回收、NoRedirect、代理和 mock 回退、取消时关闭连接、截断响应和付费 POST 的发送次数。 |
| `tests/test_performance_qml.py` | 逐字符追加后与完整解析等价，前置 QTextDocument 和复制按钮保持同一实例，主题变化重新格式化，首次打开与再次打开时草稿编辑器保持可用。 |

Windows 原生窗口专项使用 `QT_QPA_PLATFORM=windows` 运行 `test_ui_polish_windows.py`、`test_interface_language.py` 和 `test_performance_qml.py`，结果为 **20 passed in 29.55s**。主题、语言与两档窗口尺寸共 24 种组合，QML 引擎警告均为空。既有 SQLite 便携目录、HTTP 安全、媒体数据契约及隐藏面板定位测试均纳入全量执行。本机专项输出位于 `.cache/qa/performance/verified-native/out.txt`。

最终全量运行启用 FFmpeg 必测、Qt 软件渲染及仅允许回环连接的网络检查。基线 994 项测试增加到 1,037 项，113 项子测试全部保留。唯一警告来自已有依赖 `wordseg` 导入弃用的 `pkg_resources`。与基线逐文件核对，既有测试文件均无差异，236 个字面量 QML 定位名称均保留，英繁词典键集合一致。本机完整输出位于 `.cache/qa/performance/review-offscreen-final/out.txt`。

```text
> .venv/Scripts/python.exe -m ruff check src tests
All checks passed!

> .venv/Scripts/python.exe -m pytest -q
1037 passed, 1 warning, 113 subtests passed in 122.83s (0:02:02)
```

### 提交前复验

本机 HTTP 服务验证了同一连接连续三次收到默认、自定义、默认 `User-Agent`。另一项测试令 `Retry-After` 解析抛错，随后通过同一个单槽池成功发送请求。Windows 实窗另行验证了相同正文从代码切回 Markdown 时重新格式化，以及工作区弹窗已打开、表单尚未装配时收到设置通知，两处均无 QML 警告。

指定四个测试文件在 Windows 原生窗口下运行，结果为 **68 passed in 15.77s**。输出位于 `.cache/qa/performance/review-targeted/out.txt`。上方全量通过记录使用仓库默认的离屏平台，启用 FFmpeg 必测，固定 `PYTHONHASHSEED=0`。

此前两次离屏全量进程分别在旧 QWidget 线程完成回调和创建 QML 工作区时的垃圾回收阶段原生中止，根因未定。第二次中止前下载进度用例出现过一次失败，后续完整执行通过。日志保存在 `.cache/qa/performance/review-full/out.txt` 和 `.cache/qa/performance/review-full-sequential/out.txt`。额外强制整个套件使用 Windows 原生窗口时，旧 QWidget 粤拼换行测试的宽度为 653 px，断言上限为 520 px。使用 `948446e` 源码独立运行该测试复现了相同宽度。对应输出位于 `.cache/qa/performance/review-native-full/out.txt` 和 `.cache/qa/performance/review-legacy-baseline/out.txt`。
