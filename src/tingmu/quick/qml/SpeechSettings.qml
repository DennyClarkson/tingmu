import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ColumnLayout {
    id: root
    spacing: 6
    readonly property bool external: backend.settings.asr_backend === "external"
    readonly property bool localApi: backend.settings.asr_backend === "local_api"
    readonly property bool builtin: !external && !localApi
    readonly property string urlKey: localApi ? "local_asr_endpoint" : "asr_endpoint"
    readonly property string modelKey: localApi ? "local_asr_api_model" : "external_asr_model"
    readonly property string envKey: localApi ? "local_asr_key_env" : "asr_key_env"
    readonly property string formatKey: localApi ? "local_asr_response_format" : "asr_response_format"
    readonly property string serviceAddress: backend.settings[root.urlKey]
    onServiceAddressChanged: if (secret) secret.clear()
    component Caption: Text {
        Layout.fillWidth: true
        color: Theme.muted
        font.family: Theme.font
        font.pixelSize: 12
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
    }

    Caption { text: "字幕识别方式" }
    Choice {
        objectName: "asrBackendChoice"
        Layout.fillWidth: true
        model: ["内置模型 · 本机运行", "本机 API · 本机语音服务", "外部 API · 远程转写"]
        currentIndex: root.external ? 2 : root.localApi ? 1 : 0
        onActivated: backend.setting("asr_backend", ["local", "local_api", "external"][currentIndex])
    }
    Toggle {
        text: "识别前分离人声、减弱配乐"
        checked: backend.settings.suppress_bgm
        onToggled: backend.setting("suppress_bgm", checked)
    }
    Caption {
        visible: backend.settings.suppress_bgm
        text: backend.speechModels.bgm === "spleeter-2stems-fp16"
              ? "当前使用 Spleeter 在本机提取人声。"
              : "本机未安装人声分离模型，将用频谱分析减弱重复背景声。"
    }
    Caption { visible: root.builtin; text: "本机模型" }
    Choice {
        visible: root.builtin
        Layout.fillWidth: true
        model: ["small", "large-v3-turbo", "large-v3", "qwen3-asr-0.6b", "qwen3-asr-1.7b"]
        currentIndex: model.indexOf(backend.settings.asr_model)
        displayText: currentIndex < 0 ? backend.settings.asr_model.replace(/^local:/, "") : currentText
        onActivated: backend.setting("asr_model", currentText)
    }
    TButton {
        visible: root.builtin
        text: "选择已有的本机 Whisper 模型…"
        onClicked: backend.chooseWhisperFolder()
    }
    Choice {
        visible: root.builtin
        Layout.fillWidth: true
        model: ["auto", "cpu", "cuda"]
        currentIndex: model.indexOf(backend.settings.device)
        onActivated: backend.setting("device", currentText)
    }
    Toggle {
        visible: root.builtin
        text: "按检测到的说话人切换分句"
        checked: backend.settings.speaker_turns
        onToggled: backend.setting("speaker_turns", checked)
    }
    Caption {
        visible: root.builtin && backend.settings.speaker_turns
        text: backend.speechModels.speaker === "unavailable"
              ? "本机未安装换人检测模型，当前使用识别器原有的分句。"
              : "根据换人和词语时间切句。重叠对话或短插话可能漏检，说话人姓名需由字幕或外部服务提供。"
    }
    ColumnLayout {
        visible: !root.builtin
        Layout.fillWidth: true
        spacing: 5
        Caption { text: root.localApi ? "本机语音服务地址（支持 OpenAI 兼容转写的本地服务）" : "远程转写地址（OpenAI 兼容）" }
        Field {
            Layout.fillWidth: true
            text: backend.settings[root.urlKey]
            onEditingFinished: backend.setting(root.urlKey, text)
        }
        Caption { text: "语音模型 ID" }
        Field {
            Layout.fillWidth: true
            text: backend.settings[root.modelKey]
            onEditingFinished: backend.setting(root.modelKey, text)
        }
        TButton { text: "查询服务模型"; onClicked: backend.checkSpeechModels() }
        Choice {
            Layout.fillWidth: true
            visible: count > 0
            model: backend.state.speechModelChoices || []
            currentIndex: model.indexOf(backend.settings[root.modelKey])
            displayText: currentIndex < 0 ? "选择支持转写的模型" : currentText
            onActivated: backend.setting(root.modelKey, currentText)
        }
        Caption { text: "选择该模型支持的字幕格式" }
        Choice {
            Layout.fillWidth: true
            model: ["时间段字幕 · verbose_json", "带说话人 · diarized_json"]
            currentIndex: backend.settings[root.formatKey] === "diarized_json" ? 1 : 0
            onActivated: backend.setting(root.formatKey, currentIndex === 1 ? "diarized_json" : "verbose_json")
        }
        Caption { text: "密钥环境变量名（可选）" }
        Field {
            Layout.fillWidth: true
            text: backend.settings[root.envKey]
            onEditingFinished: backend.setting(root.envKey, text)
        }
        Caption { text: "或为此地址保存工作区密钥（Windows 加密）" }
        Field {
            id: secret
            objectName: "speechKeyInput"
            Layout.fillWidth: true
            echoMode: TextInput.Password
            placeholderText: "输入服务密钥"
        }
        RowLayout {
            TButton {
                text: "保存密钥"
                enabled: secret.text.length > 0
                onClicked: { if (backend.saveSpeechKey(secret.text)) secret.clear() }
            }
            TButton { text: "清除已存密钥"; quiet: true; onClicked: {backend.saveSpeechKey(""); secret.clear()} }
        }
        Caption { text: root.localApi ? "音轨仅发送到本机回环地址。请先启动支持转写的本机服务。"
                                    : "启用后，生成字幕会把所选音轨分段发送至 " + backend.settings.asr_endpoint }
        Toggle {
            visible: root.external
            objectName: "allowAudioUpload"
            text: "允许此工作区发送音轨"
            checked: backend.settings.allow_audio_upload
            onToggled: backend.setting("allow_audio_upload", checked)
        }
    }
}
