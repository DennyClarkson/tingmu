# 本地语音前处理与换人分句

在语音设置中开启「抑制背景音乐」后，听幕先解码选定音轨和时间范围，识别器接收处理过的单声道 16 kHz PCM。播放器继续播放源媒体。临时音频位于应用缓存，完成、失败和取消均清理。本地识别和明确选用的外部语音服务共用这一步。

## 人声和伴奏

安装语音模型时，听幕在本地 CPU 上运行 Spleeter 2-stems，以 44.1 kHz 立体声估计人声和伴奏的频谱掩码，只将估计的人声送入识别。长片分块处理，块两边留出上下文并保留原时间轴。没有模型时，听幕显示「频谱背景抑制」，使用局部频谱底噪估计削弱持续、重复的声音。文档元数据 `bgm_method` 记录实际路径。

音乐中的歌声也可能进入人声轨，影视混音和弱对白亦可能造成漏字或声学失真。可关闭抑制后重新识别同一区间，听取原声检查字幕。Spleeter 的训练任务、模型说明和 ONNX 转换参见 [Deezer 的模型说明](https://github.com/deezer/spleeter/wiki/3.-Models) 和 [sherpa-onnx 来源](https://k2-fsa.github.io/sherpa/onnx/source-separation/models.html)。

## 声学换人候选

本地 pyannote segmentation 3.0 在重叠的十秒窗口内估计静音、单人和重叠说话。检测器选取连续单人片段之间的换人候选，用相邻识别词或对齐单元的中心决定切点。Qwen 使用 ForcedAligner，Whisper 在有候选时请求词级时间戳。没有可靠逐词匹配时保留原段。多个说话人同时讲话时，单一路径的语音识别仍可能把词归错。长静音后的相邻句子继续由识别器自身分段。只作窗内检测，所以本地模式不给全片人物命名。外部服务的说话人标签和 ASS 角色名、WebVTT 单一 voice 标记来自各自的源。

`speaker_method`、`speaker_change_count` 和 `speaker_changes`（原片绝对秒数）记录本次本地检测。若未安装模型，进度提示会说明，`speaker_method` 记录 `unavailable`。原始 Qwen 结果和声学对齐、Whisper 的原段及其生成的字幕 ID 保存在工作区的 ASR 审计文件。模型定义与类别见 [pyannote 模型卡](https://huggingface.co/pyannote/segmentation-3.0)。

ASS/SSA 导出保留每句的角色栏，WebVTT 导出单一角色 voice span 并转义标签字符。ASS/SSA 的角色栏遇到半角逗号时会提示改用 WebVTT；SRT 导出正文和时间。

## 安装与模型来源

工作区的「识别与模型」提供人声分离和换人检测模型下载，也可在项目目录执行以下命令。安装器校验归档 SHA256，只把指定的 ONNX 和许可文件写到项目缓存。音频处理阶段使用本地文件，不下载这两组权重。

同一安装目录的工作区共用 `.cache/models` 中的 Whisper、`.cache/qwen-models` 中的 Qwen 与 ForcedAligner，以及 `.cache/speech-models` 中的分离/换人模型。两个工作区同时安装分离模型时，后启动的进程等待同一把文件锁并复用已校验的文件。工作区各自保存转写审计。源码只接受项目缓存下的缓存覆盖值；便携版使用随安装目录移动的缓存。HF、Torch、FFmpeg 临时文件和 QML 编译缓存都使用应用内位置；Qt Quick 自动图形磁盘缓存关闭。

```powershell
.\.venv\Scripts\python.exe scripts/setup_speech.py
```

两个官方转换归档共约 42 MB。便携构建会复制已安装的 `.cache/speech-models` 和随附许可。Spleeter 作者在论文 Distribution 段明确写明预训练模型与源代码采用 MIT 许可，许可文件随权重提供。pyannote 官方模型卡列为 MIT，转换归档随附 CNRS 的 MIT 文本。原始 Hugging Face 模型下载需登录并接受信息共享说明，此安装器使用 sherpa-onnx 公开发布的 ONNX 版本。参见 [Spleeter 作者论文](https://github.com/deezer/spleeter/blob/master/paper.md)、[Spleeter 许可](../src/tingmu/Spleeter-LICENSE.txt) 和 [sherpa-onnx 模型来源](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/models.html)。

## 本次验证

- 将两种 Windows 普通话合成语音拼接后，混入按半秒换音高的谐波伴奏。11.92 秒测试中，参照原净语音的 SI-SDR 从 −6.30 dB 提高到 +2.34 dB。处理前后都得到 190735 个 16 kHz 采样点。换人检测漏掉了这个合成声音切换。
- sherpa-onnx 公开四人中文录音的 56.86 秒样例得到 6.823、12.739、13.659 秒三个候选。首尾两处接近上游完整聚类方案示例的两个短间隔换人，中间一处在该示例里属于同一人。该对照使用的是上游模型输出。[样例及其输出](https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/models.html)
- 现有粤语音轨的第 180–198 秒实际走 Spleeter、pyannote、Qwen3-ASR 1.7B CUDA 和 ForcedAligner，含加载耗时约 62.5 秒。生成 6 条字幕及原始审计，195.321 秒有一处声学换人候选。以上验证没有人工逐字参考，不提供整片准确率。
- 公开中文录音的前 17 秒也实际走 faster-whisper large-v3-turbo CUDA 和本地换人检测。识别器的 6 段输出经词级时间戳切为 7 条字幕，约 11.0 秒完成，逐段拼回后与原始 ASR 字符串完全相同。
