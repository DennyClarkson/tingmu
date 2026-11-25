# Windows 极简便携版与冷启动

`perf/bundle-and-startup` 基于 `main@f39551e`。Windows x64 只有一种对外发行包。全新目录实测 **251.54 MiB（263,757,052 B），3,596 个文件，模型载荷 0**。相对上一版减少 96.85%。首次引导支持离线跳过，播放和外挂字幕可以直接使用。

## 交付体积

同一台机器测量完整解压目录的逻辑字节数。PySide6 算在主 Python 内。三个阶段的四套完整 OTF 字体通过逐文件 SHA-256 对比。

| 项目 | 初始基线 | 上一版 | 本次发行包 |
| --- | ---: | ---: | ---: |
| 总体积 | 9.29 GiB | 7.81 GiB | **251.54 MiB** |
| 文件数 | 35,876 | 17,262 | **3,596** |
| 主 Python | 2,923.52 MiB | 2,362.83 MiB | 208.79 MiB |
| 其中 PySide6 | 646.68 MiB | 118.83 MiB | 109.51 MiB |
| 应用及资源 | 41.21 MiB | 42.67 MiB | 42.74 MiB |
| FFmpeg / FFprobe 命令行工具 | 423.74 MiB | 423.74 MiB | 0 B |
| 独立 Qwen 运行时 | 6,124.08 MiB | 5,167.36 MiB | 0 B |

根目录包含 `Tingmu.exe`、`app/`、`runtime/python/` 和发布清单。构建阶段校验 3,594 个程序及运行时文件，另有根目录的启动器和清单。主 Python 包含 NumPy、字幕和语言处理库、证书、filelock、Hugging Face Hub 及 wordseg 所需的 `setuptools<81`。发布闭包排除了本地 Whisper、ONNX、PyAV、tokenizers、hf_xet、NVIDIA 包以及 PyTorch。源码开发环境继续提供推理依赖。

首发检查覆盖 13 个 QML 模块和 68 个 Windows 二进制文件。实机笔记渲染发现，Python 接收 QQuickTextDocument 需要 QtQuick.pyd 和随之导入的 QtOpenGL.pyd。修复后检查 70 个 PE，两个绑定合计 9,469,552 字节。搬迁烟雾同时检查 Markdown 的标题、表格、链接与代码。Qt Quick 保留 Qt6OpenGL.dll、软件渲染库和 FFmpeg 媒体插件。

## 本机检测与按需安装

引导在后台检查本机。两个媒体工具分别执行 `-version`；Whisper 使用当前 Python 验证实际导入、INT8 与 ONNX CPU provider；CUDA 探针创建并销毁 cuBLAS/cuDNN 句柄；Qwen 使用所选 Python 导入引擎并执行张量运算。Python 探针限制外部写入并阻断网络，可在跳过或取消时终止。已有路径按只读引用使用，移动或删除外部运行环境后重新检测。

| 组件 | 新安装锁定下载量 | 安装位置 |
| --- | ---: | --- |
| FFmpeg / FFprobe | 100.13 MiB | `bin/`，共享构建中选取两份 EXE、7 个 DLL 和许可证，合计 234.81 MiB |
| Whisper CPU / ONNX | 66.50 MiB | `.cache/runtime-deps/local-asr/` |
| Whisper NVIDIA CUDA | 1,267.88 MiB | `.cache/runtime-deps/whisper-cuda/bin/`，保留所需 DLL 和许可证 |
| Qwen3-ASR | 3,304.88 MiB | `runtime/asr/`，使用核心 CPython 3.12 和独立锁定的推理依赖 |
| uv | 20.74 MiB | 仅 Qwen 安装且本机缺少 uv 时获取，写入 `.cache/tools/uv.exe` |

使用 `app/tingmu/assets/runtime-deps/windows-cp312-x64.json` 锁定下载 URL、大小及 SHA-256。下载、验证和解包在安装内暂存，验证成功才发布。Qwen 的 uv 步骤只接受本地已验证的 wheels，安装后应用两项 Nagisa / ForcedAligner 补丁。刷新仅更新程序文件，学习数据和按需组件保存在原位。

从「文件」或「工作区设置 → 语音识别」可重访管理页。安装行为由人确认，取消后可重新安装。首次引导只选择工具和引擎；工作区模型计划和实际传输会检查引擎就绪状态。可单独选择 API 转写。首次选择和本机路径按安装保存到 `data/runtime-setup.json`，烟雾测试使用独立状态。

## 原生验收与测量

搬迁到含中文和空格的路径后，用 `Tingmu.exe --portable-smoke --smoke-media` 打开仓库内的双音轨 `01.mkv`。启动进程使用隔离的 PATH 和主机工具查找目录。外部 FFmpeg/FFprobe 均未找到。引导出现、零下载跳过和持久化、四套字体、工作区设置及系统托盘均通过。Qt 实际解码 **53 帧**，时长 **180,017 ms**，切换第二音轨、定位、1.25 倍速、暂停均通过。结束时 `ready=true`、`errors=[]`、`qml_warnings=[]`。全部 214 份启动字节码的路径、摘要和时间戳在实窗启动前后一致。

一次中间构建的 Windows 视频烟雾超过外部 65 秒超时。终止隔离进程后，诊断复跑及三次空 QML 缓存复跑通过，未能复现该次阻塞。最终烟雾按顺序关闭引导、打开设置，并把视频帧通知排入 Qt 事件队列。

| 五次独立进程启动的中位数 | 初始基线 | 上一版 | 本次 |
| --- | ---: | ---: | ---: |
| 导入 `tingmu.quick.main` | 613.54 ms | 198.85 ms | 208.22 ms |
| 原生 EXE 启动至首个 `frameSwapped` | 1,682.97 ms | 1,141.26 ms | 1,177.29 ms |
| 首帧 GUI 子进程工作集 | 272.76 MiB | 269.21 MiB | 267.49 MiB |
| 首帧启动器工作集 | 18.75 MiB | 18.41 MiB | 18.40 MiB |

本次每次新进程都会展示首次引导。五次启动的首帧中位数较初始基线缩短 506 ms，较上一版增加 36 ms。用同一便携目录记录「已跳过」后另测五次，首帧为 **1,155.79 ms**，GUI 工作集 **265.72 MiB**。

测量工具为 `tests/perf_bundle.py`。Windows 使用软件绘制，每次启动使用新的学习数据和空 QML 缓存，操作系统管理文件缓存。工作集取自 Windows GetProcessMemoryInfo。启动器与子进程分别测量；前两组的启动器 RSS 来自三次补测。旧版同一构建预编译前后的对照记录在 `benchmarks.json`。本次保留源码与 214 份启动字节码，启动器使用 `-B`。

本机只读复用实测通过 WinGet FFmpeg 9.0、CTranslate2 4.9.0 和 ONNX；也通过 Qwen 环境的 Torch 动态库真正初始化 CUDA。独立 Qwen Python 报告 Torch 2.7.1+cu128，CPU 和 NVIDIA RTX 5070 Ti Laptop GPU 张量检查均通过。四项合计下载 **0 B**。另用本机已安装的同版本文件还原锁定运行库，验证了 7 个 CPU wheels、17 个 CUDA DLL 及迁移后的 54 个 Qwen wheels 的实际导入与运行。FFmpeg 在线发行物核对了上游哈希和 ZIP 目录元数据。此次未从公网全量下载这些大型依赖；传输、校验失败、取消、路径约束与发布流程用本机回环 HTTP 测试。

## 复验

在仓库根目录用新的 QA 子目录运行。首次引导专项覆盖简中、繁中、英语、跳过与重开、显式选择及安装取消、外部路径失效、缺少引擎时的模型下载拦截。保留既有 113 项子测试。

```powershell
$qa = '.cache/qa/bundle-opt'
.venv/Scripts/python.exe -B packaging/build_portable.py --destination "$qa/base"
.venv/Scripts/python.exe -B packaging/build_clean_install.py "$qa/新建 听幕" --source "$qa/base"
& "$qa/新建 听幕/Tingmu.exe" --portable-smoke --smoke-media (Resolve-Path dist/Tingmu/examples/01.mkv)
.venv/Scripts/python.exe -B tests/perf_bundle.py "$qa/新建 听幕" core-first-frame
```

```text
> .venv/Scripts/python.exe -m ruff check src tests packaging
All checks passed!

> .venv/Scripts/python.exe -m pytest -q
1121 passed, 1 warning, 113 subtests passed in 128.19s (0:02:08)

> uv lock --check --offline
Resolved 58 packages in 0.83ms
```

离线全量测试通过 Python 网络审计钩子限制为回环连接，启用 `--require-ffmpeg`。唯一警告来自第三方 wordseg 引用已弃用的 pkg_resources。QA 目录保留轻量 JSON，包含 `core-audit.json`、`core-relocated-video.json`、`core-startup.json`、`core-verification.json` 和 `optional-*.json`。正式发行目录与既有模型缓存保持原位。
