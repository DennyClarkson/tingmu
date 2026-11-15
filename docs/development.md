# Windows 开发入门（v0.5）

以下命令均在仓库根目录（含 `pyproject.toml`）的 PowerShell 执行。开发主要验证 Windows x64、Python 3.12；项目声明支持 `>=3.12,<3.14`，安装脚本固定选择 3.12。

## 1. 获取源码与依赖

从私有 GitHub 仓库页面复制克隆地址，用 `git clone` 克隆后进入项目目录。先准备 Git 和可在终端运行的 `uv`；可用 `git --version`、`uv --version` 检查。Python 3.12 可预装，也可由 uv 在同步时下载。仓库不包含 Python 环境、FFmpeg、模型权重或用户数据。

建议把仓库放在空间充足的磁盘。安装入口将下载、Python 分发和临时文件放在项目 `.cache/`，环境放在 `.venv/`，只临时设置当前进程的环境变量，不修改系统设置或迁移旧缓存。

```powershell
# 只显示计划，不创建目录或下载
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Dev -CpuOnly -Check

# 日常开发起点：含测试和 lint 工具，不安装可选 CUDA 依赖
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup.ps1 -Dev -CpuOnly
```

`-ExecutionPolicy Bypass` 仅用于此子进程；遵守组织的脚本执行策略。已有合适 NVIDIA 驱动、需要 Whisper GPU 识别时，去掉 `-CpuOnly` 重新运行。GPU 依赖体积较大；不带 `-CpuOnly` 的默认安装会安装 `gpu` extra。`-Dev` 必须显式提供，普通安装不含 pytest/Ruff。脚本使用 `uv sync --locked`，锁文件与依赖声明不一致时会报错，不会默默更新依赖；修改依赖时须由维护者同步更新锁文件。不要绕过脚本把大下载写入系统盘。

## 2. 本地 FFmpeg 与首次启动

准备含 `ffmpeg.exe` 和 `ffprobe.exe` 的 Windows FFmpeg 发行包，解压到项目 `.cache/ffmpeg/`，整理为 `.cache/ffmpeg/bin/`。安装脚本不会安装 FFmpeg。下面只为当前 PowerShell 添加路径，每次新开终端需重新执行：

```powershell
$env:PATH = (Join-Path (Get-Location).Path '.cache\ffmpeg\bin') + ';' + $env:PATH
Get-Command ffmpeg, ffprobe
& .\.venv\Scripts\python.exe -m tingmu doctor
& .\.venv\Scripts\python.exe -m tingmu --data-dir .\.cache\dev-data gui
```

`doctor` 检查可执行文件和不含密钥的服务状态；FFmpeg 路径为 null 时还不能做依赖它的媒体探测/抽音频。它不等于媒体解码、GPU 或云模型质量验收。已有本地 FFmpeg 时也可把其 `bin` 加入当前进程 PATH。

首次只验证界面时不需要 API 密钥或 ASR 权重。先关闭“云端文本”，再用自己的媒体和字幕尝试本地流程；默认配置允许云端文本，已有环境变量密钥可能被发现。模型管理中分别配置复核、讲解和对话，详见 [模型说明](providers.md)。根目录 `.env.example` 仅供变量名称参考，应用不会自动加载 `.env`。不要将密钥写入源码、命令示例、报告或明文配置；手工密钥仅由 Windows DPAPI 保存，换账号/电脑需重新输入。

开发默认数据目录是 `.data/`；示例显式改到 `.cache/dev-data/`，与个人资料隔离。音视频留在原位置，不复制进仓库，不在媒体旁写学习数据。仅打开空白 GUI 不会下载 ASR 权重；实际识别可能下载。

## 3. 可选的本地语音识别

有现成文本字幕时可以先导入字幕开发，无需下载语音模型。主环境包含 faster-whisper 库，但不含权重；选用 `large-v3-turbo` 等模型后首次识别可能联网下载到项目缓存。CPU 可用但速度取决于硬件，CUDA 还需要兼容驱动。

Qwen3-ASR 与 ForcedAligner 使用独立环境，不是运行界面或普通回归测试的前提：

```powershell
# 安装独立 ASR 环境；会下载 CUDA 版 PyTorch 等较大依赖
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_qwen.ps1

# 明确需要实际识别时再下载两个模型；可替代上一条一次完成
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_qwen.ps1 -DownloadModels
```

环境位于 `.cache/qwen-venv/`，权重位于 `.cache/qwen-models/`；依赖清单为 `scripts/qwen-requirements.lock.txt`。该脚本固定安装 PyTorch 2.7.1 CUDA 12.8 构建，即使仅用 CPU 也会下载此构建；CPU fallback 不代表性能验收通过。模型/运行时需要额外数 GiB 空间，完整历史便携包约 17.3 GiB，不能按源码大小估算磁盘需求。详见 [ASR 说明](asr.md)。

## 4. 检查与调试

```powershell
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe -m ruff check src tests scripts packaging
git diff --check
```

直接使用已安装的 `.venv`，避免一次普通测试又触发环境同步。测试包含离屏 Qt/QML 回归；不需要个人样片、生产数据库或真实云端密钥。涉及真实播放、浮窗、选词、全屏或焦点的变化，还需用隔离数据在 Windows 可见窗口中验证；离屏通过不能替代目视验收。`scripts/verify_quick_ui.py` 等专项脚本先读参数与说明，不能直接对生产目录运行。

开启应用“帮助 → 开发者模式”查看日志。源码模式日志在选定数据目录的 `logs/`；诊断导出在其 `diagnostics/`。默认日志不记录字幕、对话、笔记或请求正文，提交前仍需人工核查。

历史 v0.5 验收记录为 505 passed、84 subtests，Ruff 通过，详见 [handoff.md](../handoff.md)。这是交接记录，不代表新电脑或当前改动已通过；记录自己实际执行的结果。曾有一次 Qt 原生访问异常，后续复跑及最终完整回归通过，见 [UI 验证](ui-v5.md)。

## 5. 源码导航

| 位置 | 职责 |
| --- | --- |
| `src/tingmu/cli.py`、`runtime.py` | 命令入口、数据与缓存路径、便携环境识别 |
| `quick/main.py`、`quick/bridge.py`、`quick/features.py`、`quick/qml/` | 默认 Qt Quick 窗口、状态/命令桥、功能与界面 |
| `media.py`、`language.py`、`timeline.py` | 媒体/字幕/本地 ASR、语言处理、源句与显示时间轴 |
| `pipeline.py`、`batch.py`、`review.py`、`agent.py` | 准备流程、持久队列、文本复核、教学生成 |
| `providers.py`、`credentials.py`、`companion.py`、`app_actions.py` | 模型连接、凭据保护、聊天、受限应用操作 |
| `storage.py`、`conversations.py`、`notebooks.py`、`learning_notes.py` | 数据持久化、会话、笔记与收藏 |
| `models.py`、`library.py`、`portable_paths.py`、`diagnostics.py` | 共享数据结构、媒体库、搬迁路径、诊断 |
| `tests/`、`scripts/`、`packaging/` | 回归测试、专项工具、便携构建 |

表中未带前缀的 Python 模块均在 `src/tingmu/`。旧 `ui/app.py`、`ui/workspace.py` 仅保留历史回归参考，默认启动由 `ui/__init__.py` 转入 Qt Quick，不要把新功能接回旧界面。

`scripts/render_companion.py` 是旧 QWidget 界面截图工具，不代表当前界面；新界面验收使用 `scripts/verify_quick_ui.py`。`scripts/segment_existing.py` 是四个原始验收样片专用的历史分句迁移工具，会核对样片名，不是通用视频处理入口。

## 6. 源码与便携版

Git 克隆得到开发源码，不会得到 `dist/Tingmu/Tingmu.exe`。日常用户交付物是完整便携文件夹，双击 EXE 启动；运行时、模型、`.cache/`、`data/` 随文件夹保存，不能只复制 EXE。不要把自己含学习资料的便携目录作为团队初始数据。

构建入口是 `scripts/build_portable.ps1`，从本机已有依赖与模型组装，不是新电脑的一键下载器。`-RefreshApp` 用于已有完整包的应用刷新；`-ImportUserData` 会涉及个人资料导入，不作为默认协作步骤。构建前阅读 [便携说明](portable.md) 与构建脚本，准备相应资源并备份目标；源码入门无需构建。

## 7. 当前已知边界

- 文本复核没有听音，不能保证人名、漏字、粤语语气词准确。简繁转换不能修复识别遗漏；原始记录与疑点需要保留。
- 拆分显示时间按文本估算，不是声学重新对齐；拆分句的翻译/讲解仍来自完整源句。短空隙填补不能修正原 ASR 时间，部分短片段仍会很快切换。
- ASR 没有片内断点，中断后当前视频需重新识别；复核/讲解可按检查点恢复。ASS/SSA 只处理文本，不重现复杂特效；图片字幕无 OCR。
- 模型列表不保证调用权限、支持的思考强度或公开摘要。DeepSeek 已有真实验证，GPT 软件内部分调用未返回摘要；其他厂商仍主要是协议回归，没有逐个账号实测。
- 连接建立/等待响应头阶段的停止受传输超时约束。聊天上下文有界，不含联网搜索、图片上传或语音通话；Markdown 不执行脚本或加载远程图片。
- DPAPI 凭据不能跨 Windows 账号/电脑直接使用。GPU 需要兼容驱动；完整便携包尚未在第二台干净电脑验收，首次安装成功仍需本机检查。

后续限制与证据以 [交接记录](../handoff.md) 的最新版本为准；协作和提交规范见 [CONTRIBUTING.md](../CONTRIBUTING.md)。
