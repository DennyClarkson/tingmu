# 本机与远程语音 API

点顶部工作区名，打开「工作区设置 → 语音识别」，选择「本机 API」或「外部 API」。填写完整转写 URL、语音模型 ID、返回格式。可以查询服务的模型列表，也可以直接输入模型 ID。本机 API 仅接收回环地址，例如 `http://127.0.0.1:8000/v1/audio/transcriptions`，直接连接本机语音服务。外部 API 使用远程 HTTPS 地址。两类连接分别保存配置，密钥可选环境变量或 Windows DPAPI 加密存储，手工密钥与当前工作区及服务地址绑定。

使用远程服务时，先勾选「允许此工作区发送音轨」，再添加队列任务。任务保存创建时的服务设置；开始发送以及发送下一段前会重新核对当前工作区的授权。更换远程地址后需要为新地址重新开启。经典流程可导入已有文本字幕。字幕 Agent 根据语言/方言、音轨和时间覆盖决定是否需要转录，只有译文时仍需获取真实的原声转录。复核、讲解和对话的连接在「文件 → 全局文本模型」中分配，远程文本权限由工作区单独控制。[字幕处理规则](portable.md)

接口接受 multipart POST，字段包括 `file`（文件名固定为 `audio.wav` 的 16 kHz 单声道 PCM）、`model`、`response_format`，支持的双字母语种还会附 `language`。较长音轨切成最多十分钟一段，优先在分段末尾较安静处切开。各段时间写回视频原时间轴。

普通模型选择 `verbose_json`。接口返回的 JSON 须包括 `segments` 数组，每项须提供本段内以秒计的 `start`、`end` 和 `text`。说话人模型选择 `diarized_json`，每项还须提供 `speaker`，听幕同时发送 `chunking_strategy=auto`。多次独立请求返回的说话人编号按音频块区分，无法据此确定整部影片中的角色身份。如果服务仅返回整篇文本，请更换支持时间段的模型。[OpenAI 兼容协议参考](https://developers.openai.com/api/docs/guides/speech-to-text)

例如，可在 PowerShell 运行以下命令，在指定工作区中准备英文视频并导出字幕。把地址和模型 ID 改成实际服务配置，密钥保存在环境变量中。

```powershell
.\.venv\Scripts\python.exe -m tingmu --data-dir .\.data\demo prepare D:\Videos\lesson.mp4 --source zh-CN --target en --asr-backend external --asr-endpoint https://example.org/v1/audio/transcriptions --external-asr-model whisper-1 --asr-key-env TINGMU_ASR_API_KEY --asr-response-format verbose_json --allow-audio-upload --srt .\.data\demo\exports\lesson.srt
```

本机服务可用 `--asr-backend local_api --asr-endpoint http://127.0.0.1:8000/v1/audio/transcriptions`；需要沿用工作区的文本模型时加 `--provider workspace`。如需关闭本次人声预处理，可添加 `--no-suppress-bgm`。CLI 每次调用远程语音服务均需显式传入 `--allow-audio-upload`。外部模型的可用性、准确度、成本与音频保留期限由所选服务决定。
