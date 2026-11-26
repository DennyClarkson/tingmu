# 听幕 0.13.0 Windows 开发入门

从源码启动、运行离线回归、定位改动，都可以从仓库根目录（含 `pyproject.toml`）完成。以下使用 Windows x64 与 PowerShell。项目声明支持 Python `>=3.12,<3.14`，安装脚本与 Windows 回归使用 3.12。

架构重构的阶段证据见 [Phase 1/2](refactor-phase1-2.md) 与 [Step 0 / Phase 3/4](refactor-phase3-4.md)。新增 UI 命令在 `quick/commands.py` 声明处理器和错误域，Agent 工具仍走独立白名单；后台任务通过 `BackgroundTaskManager` 启动。数据库变更统一加入 `database.py` 的版本化迁移，勿在管理器构造或 UI 中补建表。测试历史数据库时显式构造旧 schema 和 `user_version`；Store 初始化现在会创建完整现行 schema。

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

推送到 `main`、向 `main` 提交 PR 或手动运行 Windows 工作流时，CI 使用锁定的 CPU/开发依赖执行 Ruff、离线测试和便携包验证。引擎测试用本机回环服务，媒体测试用校验过的 FFmpeg 合成样片。`--require-ffmpeg` 会在工具缺失时让字幕集成测试失败。包体验收另外在含中文和空格的目录运行真实启动器，上传的预览 ZIP 来自原始干净目录。[Windows CI](../.github/workflows/ci.yml) · [离线测试入口](../.github/run_offline_tests.py)

开发字幕来源决策、分句、准备工作室或界面语言时，可在同一窗口选择相应的专项测试。

```powershell
& .\.venv\Scripts\python.exe .github/run_offline_tests.py -q --require-ffmpeg tests/test_subtitle_sources.py tests/test_agent_preparation.py tests/test_workflow_queue.py tests/test_interface_language.py
& .\.venv\Scripts\python.exe .github/run_offline_tests.py -q tests/test_acoustic_periods.py tests/test_pre_llm.py tests/test_regroup_preparation.py tests/test_studio_stream_pipeline.py
```

改动影响普通窗口、全屏、焦点、语言菜单或可见播放时，另开使用正常 Qt 显示设置的终端，用自己的测试工作区和可分享素材检查实际操作。「帮助 → 开发者模式」可查看运行事件。所选工作区的 `logs/` 保存日志，`diagnostics/` 保存手动导出。界面快照、诊断与真实服务输出都留在本地审阅。

## 在本地演练 GitHub Release

每次发布前，在 `.github/release-changes/v<版本>.toml` 中为每项面向用户的改动填写中文 `zh` 和英文 `en`。将 `previous_tag` 设为上一版标签，首发留空。发布说明从这里读取变更和贡献者信息，并从当次构建的 `release-summary.json` 读取大小、文件数与 SHA-256。[首发示例](../.github/release-changes/v0.12.0.toml)

**Maintaining bilingual release notes.** For each version, add a file named `.github/release-changes/v<version>.toml`. Write each user-facing change in both Chinese (`zh`) and English (`en`). Set `previous_tag` to the preceding release tag, or leave it empty for the first release. The notes generator combines these entries and contributor credits with the measurements and SHA-256 for that build. [First release example](../.github/release-changes/v0.12.0.toml)

在 Windows x64 的干净检出中用 `uv sync --locked --extra dev` 准备 Python 3.12 环境。下面的第一条命令会下载并校验锁表指定的 FFmpeg，现场合成五秒双音轨视频。发布构建随后在中文空格路径启动离屏播放器，断网验证解码、定位、切轨、倍速和暂停。

```powershell
& ./.venv/Scripts/python.exe .github/prepare_smoke_media.py --output .cache/qa/release-fixtures/dual-audio.mkv
$version = & ./.venv/Scripts/python.exe -c "import tomllib; print(tomllib.load(open('pyproject.toml', 'rb'))['project']['version'])"
& ./.venv/Scripts/python.exe packaging/build_release.py --expect-version $version --smoke-media .cache/qa/release-fixtures/dual-audio.mkv --output .cache/qa/release-dry-run
& ./.venv/Scripts/python.exe .github/release_metadata.py --summary .cache/qa/release-dry-run/release-summary.json
```

成功后，输出目录包含 ZIP、`SHA256SUMS.txt` 和记录实际体积与文件数的 `release-summary.json`。烟雾报告与构建日志在同目录的 `diagnostics/`。ZIP 收录首次运行前的干净目录。归档条目排序和时间戳固定，每个构建的清单会记录当次构建时间，请使用同次生成的校验和。

工作流位于默认分支后，可在 Actions → Release → Run workflow 保留默认 `dry_run=true` 做全流程演练，下载保留七天的 Artifact。变更版本时同步更新 `pyproject.toml`、`src/tingmu/__init__.py` 和锁文件，本地烟雾测试会核对运行中的版本。正式发布时，从已核验的提交推送与 `project.version` 完全匹配的 `v<version>` 标签。带连字符的预发布版本自动标记为 prerelease。手动关闭 `dry_run` 时，可用 `draft` 创建草稿；同名 Release 已存在时任务会停止。若标签已推送、构建通过但发布失败，可从包含工作流修复的分支手动运行 Release，将 `existing_tag` 填为原标签。工作流会重新检出、测试并打包标签指向的提交，再核实提交一致后发布。[Release 工作流](../.github/workflows/release.yml)

## 按问题找入口

- **界面与播放**从 `src/tingmu/quick/main.py`、`quick/bridge.py`、`quick/qml/` 进入。界面语言由 `quick/localization.py` 和 `quick/i18n/` 管理。学习字幕、导入播放字幕与内嵌轨各有来源，代码与测试对照见[架构与扩展点](architecture.md)。
- **准备决策**从 `subtitle_sources.py` 和 `pipeline.py` 进入，任务快照与重试见 `batch.py`，媒体收件箱见 `media_inbox.py`。区分已有配对译文与模型生成译文，保留源句 ID、原文、时码和来源。[集成契约](contracts.md)
- **工作区和模型**从 `workspaces.py`、`storage.py`、`global_models.py` 和 `providers.py` 进入。聊天操作见 `app_actions.py`，笔记出处与手写内容由学习工作区管理。[架构与存储边界](architecture.md)
- **专项脚本**先查看参数和输入范围。`scripts/verify_quick_ui.py` 使用 Qt Quick，参数包括 `--data`、`--media`、`--output`，会产生截图与记录。`scripts/render_companion.py` 用于历史 QWidget 截图，`scripts/segment_existing.py` 仅针对旧样片迁移。

## 便携构建与验收范围

源码克隆不附带运行时、模型或成品 EXE。日常交付单位是整个可写的 Windows 便携目录。`scripts/build_portable.ps1` 使用已备好的运行时与依赖组装 `dist/Tingmu/`，`-RefreshApp` 用于刷新已存在的完整包。在已有安装上同时指定 `-RefreshApp -ImportUserData` 可显式导入个人资料。已有完整便携运行时、要验证全新首次启动时，可用 `packaging/build_clean_install.py` 在新目录创建无模型和用户资料的独立包。具体构建、刷新与 EXE 冒烟步骤见[便携版说明](portable.md)。

0.12.0 发布交接记录了 744 项测试与 113 项子测试通过，两份 EXE 各通过 16 项检查，也记录了 Windows 三语界面、字幕来源复用和短片原声转录的实测范围。这里引用该次发布记录。当前分支的结果取决于对应提交实际运行的检查。[当前交接](../handoff.md) · [验收索引](../reports/README.md)

文本模型复核只使用文字。按文字估算的分段时间与原声学时间分别保留。ASS/SSA 学习导入提取文字，图像轨可供播放，学习准备仍无 OCR。真实识别质量取决于语种、音轨与模型，服务的模型列表也只能证明该端点列出了模型。修改这些环节时，在 PR 中写明使用了哪些语种、素材、模型和设备，以及尚未验证的范围。[协作规范](../CONTRIBUTING.md)
