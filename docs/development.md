# 听幕 0.12.0 Windows 开发入门

从源码启动、运行离线回归、定位改动，都可以从仓库根目录（含 `pyproject.toml`）完成。以下使用 Windows x64 与 PowerShell。项目声明支持 Python `>=3.12,<3.14`，安装脚本与 Windows 回归使用 3.12。

## 安装 CPU 开发环境

从仓库页复制克隆地址，用 Git 克隆后进入项目目录。先确认 `git --version` 和 `uv --version` 可用。选择空间充足的磁盘。安装脚本把虚拟环境放在 `.venv/`，把 uv 缓存、由 uv 下载的 Python 和临时文件放到项目 `.cache/`，只设置当前进程的环境变量。

```powershell
# 查看安装目录和命令
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Dev -CpuOnly -Check

# 按 uv.lock 安装主环境、pytest 和 Ruff
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Dev -CpuOnly
```

`-Dev` 选择开发工具，`-CpuOnly` 省略可选的 CUDA 依赖。需要开发 Whisper GPU 识别且已有兼容 NVIDIA 驱动时，去掉 `-CpuOnly` 重新同步。`-ExecutionPolicy Bypass` 仅对该 PowerShell 子进程生效。项目使用 `uv sync --locked`，修改依赖声明时同步更新 `uv.lock`。

## 诊断与首次启动

需要媒体探测、音轨提取或内嵌字幕解码时，准备同一 Windows FFmpeg 发行包中的 `ffmpeg.exe` 和 `ffprobe.exe`。已有 FFmpeg 可直接使用。以下假设解压到了项目的 `.cache/ffmpeg/bin/`。

```powershell
$env:PATH = (Join-Path $PWD.Path '.cache\ffmpeg\bin') + ';' + $env:PATH
Get-Command ffmpeg, ffprobe
& .\.venv\Scripts\python.exe -m tingmu doctor
& .\.venv\Scripts\python.exe -m tingmu --data-dir .\.cache\dev-data gui
```

`doctor` 打印 Python 版本、FFmpeg 命令路径和服务可用性信息。窗口开发可直接执行最后一行，空白启动无需 API 密钥或模型权重。开发默认数据根为 `.data/`，上述命令将工作区目录册与学习资料都写入独立的 `.cache/dev-data/`。便携版和源码版的数据目录各自独立。

**第一次跑纯本地流程**，打开「工作区设置 → 文本模型与 API」停用文本模型，在「准备 → 新任务流程」保留「经典流程」，用自备视频和对应文本字幕入队。源码环境的默认设置允许远程文本，可能找到环境变量中的凭据。带 `clean_install` 标记的空白便携包首次启动时会关闭文本 API。配置服务时，「文件 → 全局文本模型」管理整份安装共用的连接和角色，每个工作区独立控制远程文本与音轨发送许可。`.env.example` 只列变量名，程序不会自动加载 dotenv。手工密钥使用 Windows DPAPI，加密凭据换电脑或账号后需要重录。[文本模型说明](providers.md) · [语音接口](external-asr.md)

**调试字幕 Agent** 时，在「准备 → 新任务流程」选「字幕 Agent」，将视频作为新任务入队。队列会显示采用的原文、配对译文数量、阶段和排除原因。有讲解模型才能完成 Agent 任务，缺少模型时先保存已取得的字幕。CLI 默认 `--provider local`，使用已配置的工作区文本角色时选 `--provider workspace`。可用下面的命令查看完整选项。

```powershell
& .\.venv\Scripts\python.exe -m tingmu prepare --help
```

## 可选的本地语音识别

主环境包含 faster-whisper 库。做真实识别前，在工作区语音设置里选择兼容的已安装模型，或确认下载到共享缓存。准备任务默认只读取本地权重。Qwen3-ASR 与 ForcedAligner 使用独立环境，开发 UI 和运行离线测试均可跳过。

```powershell
# 安装专用环境并检查 PyTorch、CUDA 和 Qwen 导入
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_qwen.ps1

# 同时明确下载 Qwen3-ASR-1.7B 和 ForcedAligner-0.6B 时使用
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_qwen.ps1 -DownloadModels
```

专用环境位于 `.cache/qwen-venv/`，上述可选权重位于 `.cache/qwen-models/`。脚本安装 CUDA 12.8 构建的 PyTorch 2.7.1，再按 `scripts/qwen-requirements.lock.txt` 安装 Qwen 依赖。即使随后用 CPU 运行，仍需为这份运行时与权重预留磁盘空间。[本地语音处理](local-speech.md)

## 提交前在本机验证

另开一个 PowerShell 窗口，在仓库根目录执行。如果按上文将 FFmpeg 放入项目目录，请在新窗口中重复添加 PATH 的步骤。以下设置让 Qt 使用离屏软件渲染，并将测试数据和运行缓存写到忽略目录。

```powershell
$env:PYTHONUTF8 = '1'
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QT_QUICK_BACKEND = 'software'
$env:HF_HUB_OFFLINE = '1'
$env:HF_DATASETS_OFFLINE = '1'
$env:TINGMU_CACHE_DIR = Join-Path $PWD.Path '.cache\test-runtime'
$env:TINGMU_DATA_DIR = Join-Path $PWD.Path '.cache\test-data'

uv lock --check --offline
& .\.venv\Scripts\python.exe -m ruff check .
& .\.venv\Scripts\python.exe .github/run_offline_tests.py -q --require-ffmpeg
git diff --check
```

日常提交和 PR 在本地运行以上检查，将结果写进 PR。仓库已关闭 GitHub 托管验证，工作流文件仅保留手动触发入口。`--require-ffmpeg` 让字幕集成测试在缺少工具时失败。本地暂时无法安装 FFmpeg 时，可去掉此选项运行其余用例，测试摘要会显示跳过项。测试运行器限制当前 Python 进程的外部 DNS/TCP，允许本机回环模拟服务。克隆源码就可以运行通用回归，无需开发机私人样片、便携包或服务密钥。[手动 Windows 工作流](../.github/workflows/ci.yml) · [离线测试入口](../.github/run_offline_tests.py)

集中开发 0.12 的字幕决策、队列或语言切换时，可在同一窗口运行以下四个测试文件。

```powershell
& .\.venv\Scripts\python.exe .github/run_offline_tests.py -q --require-ffmpeg tests/test_subtitle_sources.py tests/test_agent_preparation.py tests/test_workflow_queue.py tests/test_interface_language.py
```

改动影响普通窗口、全屏、焦点、语言菜单或可见播放时，另开使用正常 Qt 显示设置的终端，用自己的测试工作区和可分享素材检查实际操作。「帮助 → 开发者模式」可查看运行事件。所选工作区的 `logs/` 保存日志，`diagnostics/` 保存手动导出。界面快照、诊断与真实服务输出都留在本地审阅。

## 按问题找入口

- **界面与播放**从 `src/tingmu/quick/main.py`、`quick/bridge.py`、`quick/qml/` 进入。界面语言由 `quick/localization.py` 和 `quick/i18n/` 管理。学习字幕、导入播放字幕与内嵌轨各有来源，代码与测试对照见[架构与扩展点](architecture.md)。
- **准备决策**从 `subtitle_sources.py` 和 `pipeline.py` 进入，任务快照与重试见 `batch.py`，媒体收件箱见 `media_inbox.py`。区分已有配对译文与模型生成译文，保留源句 ID、原文、时码和来源。[集成契约](contracts.md)
- **工作区和模型**从 `workspaces.py`、`storage.py`、`global_models.py` 和 `providers.py` 进入。聊天操作见 `app_actions.py`，笔记出处与手写内容由学习工作区管理。[架构与存储边界](architecture.md)
- **专项脚本**先查看参数和输入范围。`scripts/verify_quick_ui.py` 使用 Qt Quick，参数包括 `--data`、`--media`、`--output`，会产生截图与记录。`scripts/render_companion.py` 用于历史 QWidget 截图，`scripts/segment_existing.py` 仅针对旧样片迁移。

## 便携构建与验收范围

源码克隆不附带运行时、模型或成品 EXE。日常交付单位是整个可写的 Windows 便携目录。`scripts/build_portable.ps1` 使用已备好的运行时与依赖组装 `dist/Tingmu/`，`-RefreshApp` 用于刷新已存在的完整包。`-ImportUserData` 会导入个人资料。已有完整便携运行时、要验证全新首次启动时，可用 `packaging/build_clean_install.py` 在新目录创建无模型和用户资料的独立包。具体构建、刷新与 EXE 冒烟步骤见[便携版说明](portable.md)。

0.12.0 发布交接记录了 744 项测试与 113 项子测试通过，两份 EXE 各通过 16 项检查，也记录了 Windows 三语界面、字幕来源复用和短片原声转录的实测范围。这里引用该次发布记录。当前分支的结果取决于对应提交实际运行的检查。[当前交接](../handoff.md) · [验收索引](../reports/README.md)

文本模型复核只使用文字。按文字估算的分段时间与原声学时间分别保留。ASS/SSA 学习导入提取文字，图像轨可供播放，学习准备仍无 OCR。真实识别质量取决于语种、音轨与模型，服务的模型列表也只能证明该端点列出了模型。修改这些环节时，在 PR 中写明使用了哪些语种、素材、模型和设备，以及尚未验证的范围。[协作规范](../CONTRIBUTING.md)
