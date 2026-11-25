import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S

AppDialog {
    id: dialog
    objectName: "runtimeSetupDialog"
    signal guideRequested()
    anchors.centerIn: parent
    width: Math.min(710, parent ? parent.width - 24 : 710)
    height: Math.min(628, parent ? parent.height - 20 : 628)
    modal: true
    closePolicy: Popup.NoAutoClose
    title: qsTranslate("Tingmu", "运行环境与依赖管理")
    property var choices: ({})
    readonly property var snapshot: runtimeSetup.state
    readonly property var components: snapshot.components
    readonly property bool installing: snapshot.phase === "install" || snapshot.phase === "cancelling"
    readonly property bool idle: !snapshot.phase
    readonly property var options: [
        {kind: "ffmpeg", title: "FFmpeg",
         note: qsTranslate("Tingmu", "推荐。提取音轨、使用 API 转写和读取内嵌字幕时需要 FFmpeg。")},
        {kind: "local-asr", title: qsTranslate("Tingmu", "本地 Whisper 引擎"),
         note: qsTranslate("Tingmu", "仅使用本地 Whisper 识别时需要。支持 CPU，也可搭配 CUDA 加速。模型权重在工作区另选。")},
        {kind: "whisper-cuda", title: "Whisper CUDA",
         note: qsTranslate("Tingmu", "可选。使用兼容的 NVIDIA 显卡加速 Whisper。")},
        {kind: "qwen", title: qsTranslate("Tingmu", "Qwen 识别环境"),
         note: qsTranslate("Tingmu", "可选的大型下载。使用 Qwen 语音识别时才需要，模型权重在工作区另选。")}
    ]
    function canInstall(kind) {
        const row = components[kind]
        return !!row && !row.ready && (kind !== "whisper-cuda" || row.nvidia_present)
    }
    function selected(kind) {
        return canInstall(kind) && (choices[kind] === undefined ? kind === "ffmpeg" : choices[kind])
    }
    readonly property var selectedIds: options.map(o => o.kind).filter(k => selected(k))
    readonly property real selectedBytes: selectedIds.reduce((sum, k) => sum + components[k].bytes, 0)
    function sourceName(source) {
        return source === "portable" ? qsTranslate("Tingmu", "已使用包内组件")
                                     : qsTranslate("Tingmu", "已复用本机组件")
    }
    onAboutToShow: {
        if (idle) {
            choices = ({})
            runtimeSetup.detect()
        }
    }
    component LabelText: Text {
        Layout.fillWidth: true
        font.family: Theme.font; font.pixelSize: 12; color: Theme.muted
        textFormat: Text.PlainText; wrapMode: Text.Wrap
    }
    contentItem: ColumnLayout {
        spacing: 9
        LabelText {
            text: dialog.snapshot.playback
                ? qsTranslate("Tingmu", "基础播放器已就绪。现在就可以观看视频和使用外挂字幕。")
                : qsTranslate("Tingmu", "未检测到 Qt 播放后端。请检查基础便携包是否完整。")
            color: dialog.snapshot.playback ? Theme.text : Theme.error
        }
        RowLayout {
            Layout.fillWidth: true
            LabelText { text: qsTranslate("Tingmu", "检测仅在本机进行。已有组件会直接复用，勾选并确认后才会下载。") }
            TButton {
                objectName: "runtimeGuide"; quiet: true
                text: qsTranslate("Tingmu", "查看使用指南"); onClicked: dialog.guideRequested()
            }
        }
        AppScrollView {
            id: list
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; contentWidth: availableWidth
            ColumnLayout {
                width: list.availableWidth; spacing: 9
                Repeater {
                    model: dialog.options
                    delegate: ColumnLayout {
                        id: option
                        required property var modelData
                        readonly property string kind: modelData.kind
                        readonly property var row: dialog.components[kind] || ({})
                        Layout.fillWidth: true; spacing: 2
                        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
                        RowLayout {
                            Layout.fillWidth: true
                            Toggle {
                                objectName: "runtimeOption-" + option.kind
                                Layout.fillWidth: true
                                enabled: dialog.idle && dialog.canInstall(option.kind)
                                text: option.modelData.title
                                checked: dialog.selected(option.kind)
                                onToggled: {
                                    let updated = Object.assign({}, dialog.choices)
                                    updated[option.kind] = checked
                                    dialog.choices = updated
                                }
                            }
                            TButton {
                                objectName: "runtimePath-" + option.kind
                                enabled: dialog.idle; quiet: true
                                text: qsTranslate("Tingmu", "选择已有路径…")
                                onClicked: runtimeSetup.choosePath(option.kind)
                            }
                        }
                        LabelText { text: option.modelData.note }
                        LabelText {
                            text: option.row.ready ? dialog.sourceName(option.row.source) + " · 0 B"
                                + (option.row.path ? " · " + option.row.path : "")
                                : !dialog.components[option.kind] ? qsTranslate("Tingmu", "待检测")
                                : option.kind === "whisper-cuda" && !option.row.nvidia_present
                                ? qsTranslate("Tingmu", "未检测到 NVIDIA 显卡，无需安装。")
                                : qsTranslate("Tingmu", "未就绪") + " · " + S.bytes(option.row.bytes)
                            color: option.row.ready ? Theme.accent : Theme.muted
                            wrapMode: Text.WrapAnywhere
                        }
                    }
                }
            }
        }
        LabelText {
            objectName: "runtimeSetupStatus"
            text: dialog.installing ? ""
                : dialog.snapshot.phase ? qsTranslate("Tingmu", "正在本机检测组件…")
                : qsTranslate("TingmuStatus", dialog.snapshot.message || "")
            visible: !!text
        }
        DownloadProgress {
            Layout.fillWidth: true; visible: dialog.installing
            sample: runtimeSetup.transfer; message: ""
            barObjectName: "runtimeInstallProgress"
        }
        RowLayout {
            Layout.fillWidth: true
            TButton {
                text: qsTranslate("Tingmu", "重新检测"); visible: !dialog.installing; enabled: dialog.idle
                onClicked: runtimeSetup.detect()
            }
            Item { Layout.fillWidth: true }
            TButton {
                objectName: "skipRuntimeSetup"
                visible: !dialog.installing
                text: runtimeSetup.firstRun ? qsTranslate("Tingmu", "暂不下载，直接进入") : qsTranslate("Tingmu", "关闭")
                enabled: dialog.idle || dialog.snapshot.phase === "detect"
                onClicked: if (runtimeSetup.dismiss(true)) dialog.close()
            }
            TButton {
                objectName: "cancelRuntimeInstall"; visible: dialog.installing
                enabled: dialog.snapshot.phase === "install"
                text: qsTranslate("Tingmu", "取消安装"); onClicked: runtimeSetup.cancel()
            }
            TButton {
                objectName: "backgroundRuntimeInstall"; visible: dialog.installing
                text: qsTranslate("Tingmu", "后台安装"); onClicked: dialog.close()
            }
            TButton {
                objectName: "installSelectedRuntime"; primary: true
                visible: !dialog.installing && (runtimeSetup.firstRun || dialog.selectedIds.length > 0)
                enabled: dialog.idle && (dialog.selectedIds.length > 0 || !!dialog.components.ffmpeg)
                text: dialog.selectedIds.length ? qsTranslate("Tingmu", "安装所选组件（%1）").arg(S.bytes(dialog.selectedBytes))
                                                : qsTranslate("Tingmu", "完成")
                onClicked: {
                    if (dialog.selectedIds.length) runtimeSetup.installSelected(dialog.selectedIds)
                    else if (runtimeSetup.dismiss(false)) dialog.close()
                }
            }
        }
    }
}
