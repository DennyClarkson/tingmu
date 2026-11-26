# 本地媒体与语音识别

`tingmu.media` 负责递归媒体扫描、ffprobe 探测、文本字幕和本地语音识别。本地识别读取所选音轨，所需模型可以预置或按需下载。其他识别方式及其授权见[本机与远程语音 API](external-asr.md)。项目运行时权重在忽略目录 `.cache/models`，临时 WAV 随作业结束清除；可用 `TINGMU_CACHE_DIR` 指定缓存目录。

## 运行

```powershell
# Windows NVIDIA 依赖由项目 gpu extra 锁定；已安装后直接使用环境 Python。
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1 -Dev
.venv/Scripts/python.exe scripts/asr_probe.py D:\Videos\lesson.mkv --start 180 --duration 120 --device cuda --output reports/private/asr_cuda_turbo.json
```

CUDA 故障时可改 `--device cpu`。应用 `auto` 首先尝试 CUDA，失败后会明确报告原因，再用同一模型的 CPU int8 重试。显式 `cuda` 不会隐藏设备失败。CPU 可能较慢；整个识别进程都可取消。

音轨参数是 **音频轨序号**：`audio_track=0` 表示文件里的第一条音轨，实际语言/方言请在播放器中确认。ffmpeg 使用 `-map 0:a:N`，输出单声道 16kHz PCM；不会移动或更改原样片。

## 模型与粤语

### 可选 Qwen3-ASR 后端

设置中的 `qwen3-asr-1.7b` / `qwen3-asr-0.6b` 使用官方 Qwen3-ASR，加上官方 Qwen3-ForcedAligner-0.6B 产生声学时间对齐。安装隔离在项目 `.cache/qwen-venv`，不会改变播放器或 faster-whisper 的依赖。执行：

```powershell
pwsh -File scripts/setup_qwen.ps1 -DownloadModels
.venv/Scripts/python.exe scripts/asr_probe.py D:\Videos\lesson.mkv --model qwen3-asr-1.7b --start 180 --duration 60 --device cuda --output reports/private/asr_qwen_aligned.json
```

运行时自动选择隔离环境的 Python，也可通过 `TINGMU_QWEN_PYTHON` 指定已安装环境。权重在 `.cache/qwen-models`；完整缓存优先离线加载，检查索引列出的每个权重分片存在后才使用。若网络代理迟迟不返回大型下载，可使用 `scripts/asr_download.py Qwen/Qwen3-ASR-1.7B Qwen/Qwen3-ForcedAligner-0.6B`：它以有界 Range 请求下载官方 Hugging Face 文件并核验官方 LFS SHA-256。

固定运行栈为 PyTorch 2.7.1 CUDA 12.8、qwen-asr 0.0.6（其官方依赖固定 Transformers 4.57.6 / Accelerate 1.12.0），使用 batch 1、bfloat16、SDPA；Windows 不安装 FlashAttention 或 vLLM。官方 PyTorch 2.7 引入 Blackwell 支持。1.7B ASR 实际权重约 4.70 GB，ForcedAligner 约 1.84 GB，另需运行时和临时空间。[Qwen 官方安装与语言说明](https://github.com/QwenLM/Qwen3-ASR)、[PyTorch Blackwell 支持](https://pytorch.org/blog/pytorch-2-7/)。

`scripts/qwen-requirements.lock.txt` 固定本次实际通过 GPU 验证的隔离环境包版本；安装器先从 PyTorch 官方 cu128 索引安装对应 Torch，再安装这些固定版本。大型下载缓存设在项目 `.cache/uv-qwen`，Python 下载、Hugging Face、Torch、pip 与临时目录也都在项目 `.cache` 内，不修改系统环境变量或主环境。早期安装遗留 C 盘缓存已迁移，详见 `disk-space.md`。

Qwen 在约 24–30 秒的窗口尾部寻找持续静音；连续语音取平滑能量谷。启用人声分离时，以人声轨识别、原轨对齐。句末标点优先，其次为有停顿或换人支持的子句标点。坍缩单元只在可信边界内重分配，估算句标为 uncertain。审计保留原始文本、对齐单位、实际切窗和修复依据。ForcedAligner 支持 11 种语言。

`.alignment.json` 保存每个原始识别窗口及其完整对齐单位，供审计对比。`original_text` 保留模型原始字形；繁体显示转换是后续显示层操作，**不能恢复模型漏掉的语气词，也不能证明听写正确**。不通过提示词硬塞“喎、啫、㗎”等字来伪造识别改善。

实测 Qwen 无 context 已恢复明显粤语口语，但原始字形偏简体。粤语专用默认 context 为 `請以繁體中文記錄香港粵語對白，保留原聲字詞。`，30 秒实测能使模型原始输出采用繁体及“係”。它没有列出或要求插入任何语气词；仍可能改变个别错误识别的字，不能当成准确率保证。其他语言/方言不附加此 context，传入 `initial_prompt=''` 可关闭它。运行时审计记录实际 context。

ForceAligner 会产生零时长的字；实测出现的零时长单位均为实际字词，非标点。受影响字幕保留 `uncertain`，不为掩盖问题伪造字级时长。极短的重采样尾帧并入前一输入窗口；指定片段时长时限制样本数，避免多出几个采样点被单独送入模型。

### Whisper 后端

内置型号和自选 CTranslate2 模型均请求词级时间戳，包括关闭换人检测的任务。换人和静音分析使用原始音轨；无 VAD 时，只有重复或低置信度同时得到原音静音支持，才从学习字幕中过滤，原始段和判据写入审计。

默认 `large-v3-turbo`。OpenAI 的 large-v3 新增粤语 token；turbo 在 large-v3 基础上减少解码层数。官方 turbo 配置确实包含 `<|yue|>`，因此默认指定 `language='yue'`，普通话则用 `zh`。`zh-CN` / `zh-HK` 会分别规范为 `zh` / `yue`。应用执行 `task='transcribe'`，不使用语音翻译模式。

faster-whisper 将 `large-v3` 映射到 `Systran/faster-whisper-large-v3`，将 `large-v3-turbo` / `turbo` 映射到 `mobiuslabsgmbh/faster-whisper-large-v3-turbo`。权重格式是 CTranslate2，不能直接传入 PyTorch Whisper 权重目录。

来源：[OpenAI large-v3 模型卡](https://huggingface.co/openai/whisper-large-v3)、[OpenAI turbo 模型卡](https://huggingface.co/openai/whisper-large-v3-turbo)、[turbo 语言配置](https://huggingface.co/openai/whisper-large-v3-turbo/blob/main/generation_config.json)、[faster-whisper 模型映射](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/utils.py)。

## Windows GPU

当前 faster-whisper 文档要求 CUDA 12 的 cuBLAS 与 cuDNN 9。CTranslate2 的一般安装页曾保留 cuDNN 8 描述，实际以 faster-whisper 当前版本和运行时错误为准。应用会注册虚拟环境 `site-packages/nvidia/*/bin` 中的 DLL 搜索路径，避免必须改全局 PATH。驱动本身不包含上述全部推理库。

ASR 在独立 Python 子进程中运行；缺失或不兼容的 CUDA DLL 即使导致原生进程终止，也不会结束主界面。父进程检查退出码、结果文件与完成事件，只有三者符合才接受结果。取消会终止子进程，不需要等一个完整视频结束。进度回调只在调用线程执行。

实测 faster-whisper 1.2.1 与 PyAV 19.0.2 的 `metadata_errors` 参数不兼容。本模块直接将已由 ffmpeg 解码的 PCM WAV 读取为 numpy float32，再传入 faster-whisper，避开重复解码及这项 API 差异。无窗口启动器 `pythonw.exe` 会用同目录 `python.exe` 启动隐藏的识别进程，以便可靠接收 JSON 进度。

来源：[faster-whisper GPU requirements](https://github.com/SYSTRAN/faster-whisper#gpu)、[CTranslate2 安装](https://opennmt.net/CTranslate2/installation.html)。

## 时间轴与原文

`start` / `duration` 单位为秒。ASR 输出会加回片段开始时间，字幕始终对应原视频绝对时间。每句 `original_text` 保存模型的原始字符串，`text` 只去掉首尾空白。ID 由媒体路径、音轨、模型、语言、序号与时间生成。后续 AI 不应改动 ID、时间和原文。

启用 VAD 与禁用上一段文本条件，降低静音、音乐和重复字幕造成的幻觉，但不能消除识别错误。低平均 log probability、高 no-speech probability、高 compression ratio 或超过 15 秒的句子会标记 `uncertain`。这些阈值只是复核提示，**不是正确率或校准后的可信概率**。没有人工参考逐字稿就不报告 WER/CER。

## 字幕文件

通过 pysubs2 导入/导出 SRT、WebVTT、ASS、SSA。读取 UTF-8（含 BOM）、UTF-16（BOM）并尝试 GB18030/Big5；建议使用 UTF-8 避免旧编码歧义。忽略 ASS 注释和零/负时长事件；显示文本去除样式标签、统一换行，原始事件文本另存于 `original_text`。原字幕文件保持不动。

`save_srt` 导出当前显示文本；`save_subtitles` 还支持 VTT/ASS/SSA。SRT/VTT 保留毫秒，ASS/SSA 受格式限制只能保留百分之一秒。导出使用临时文件后替换，避免失败时截断已有字幕。图片字幕（PGS/VobSub）不在本模块 OCR 范围内。

## 学习字幕分句

工作区文本模型页和准备队列齿轮可以为新任务选择分句方式。CLI 参数为 `--segmentation-mode local` 或 `--segmentation-mode llm_regroup`。队列在入队时保存选择。默认 `local` 依据声学证据、字幕事件、时长和标点处理碎行，工作区启用复核时再逐句校对。

智能分句适合混用多种语言/方言或结构复杂的对白。它使用工作区已有的文本访问权限，优先使用分配给全文复核的连接，未分配时使用讲解模型。模型接收学习语言/方言、局部索引、源文字、时间及停顿线索。音频留在所选识别流程。已有配对译文和手工修订保留分段。通过校验的重组结果直接进入逐句讲解与译稿核验。关闭文本模型时，经典准备可使用本地规则完成字幕；字幕 Agent 会保存转写，并提示启用讲解模型。

## 验证

`tests/test_media.py` 覆盖递归扫描、四种字幕格式、编码、ASS 原文、音频轨序号、片段绝对时间、原始 ASR 文本、实际子进程取消和 CUDA→CPU 失败恢复。真实样片原文结果另存于忽略目录 `reports/private/*.json`，包含版本、设备、逐条进度、耗时与未校对转写；可提交的摘要为 `reports/asr-validation.md`。不能把接口通过视作识别准确性验证。
