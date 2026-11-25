import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S

Loader {
    id: root
    property var sample: backend.downloadState.downloadProgress || ({})
    property string message: backend.downloadState.downloadMessage || ""
    property string barObjectName: "downloadProgressBar"
    property bool compact: false
    active: visible
    signal detailsRequested()
    readonly property real total: Number(sample.total || 0)
    readonly property real done: Number(sample.completed || 0)
    readonly property string stage: sample.stage || (sample.component ? "download" : "checking")
    readonly property bool transferring: stage === "download"
    readonly property real fraction: total > 0 ? Math.min(1, done / total) : 0
    readonly property string estimateMark: sample.estimated ? "≈" : ""
    readonly property string percent: estimateMark + Math.floor(fraction * 100) + "%"
    readonly property string stageText: ({
        connecting: qsTranslate("Tingmu", "正在连接下载源"),
        checking: qsTranslate("Tingmu", "正在检查本地文件"),
        waiting: qsTranslate("Tingmu", "正在等待其他安装"),
        download: qsTranslate("Tingmu", "正在下载"),
        verifying: qsTranslate("Tingmu", "正在校验文件"),
        installing: qsTranslate("Tingmu", "正在安装文件"),
        testing: qsTranslate("Tingmu", "正在自检"),
        cancelling: qsTranslate("Tingmu", "正在取消…"),
        ready: qsTranslate("Tingmu", "已就绪")
    })[stage] || ""
    readonly property string componentName: ({
        "local-asr": qsTranslate("Tingmu", "本地 Whisper 引擎"),
        "whisper-cuda": "Whisper CUDA", ffmpeg: "FFmpeg",
        qwen: qsTranslate("Tingmu", "Qwen 识别环境")
    })[sample.component] || sample.component || ""
    readonly property string heading: sample.stage || !message
        ? [stageText, componentName].filter(Boolean).join(" · ")
        : qsTranslate("TingmuStatus", message)
    property bool stalled: false
    onSampleChanged: {
        stalled = false
        if (sample.rate > 0 && visible) idleDeadline.restart()
        else idleDeadline.stop()
    }
    Timer {
        id: idleDeadline
        interval: 2000
        onTriggered: root.stalled = true
    }
    readonly property string speed: sample.rate > 0 && !stalled ? S.bytes(sample.rate) + "/s"
        : sample.received > 0 ? "0 B/s" : qsTranslate("Tingmu", "测速中…")
    readonly property string eta: sample.eta > 0 && !stalled
        ? qsTranslate("Tingmu", "约剩 %1").arg(S.time(sample.eta * 1000)) : ""
    readonly property string summary: transferring
        ? [total > 0 ? percent : S.bytes(done), speed, eta, componentName].filter(Boolean).join(" · ")
        : heading

    sourceComponent: compact ? compactView : fullView
    Component {
        id: compactView
        TButton {
            objectName: "compactDownloadProgress"
            implicitWidth: Math.min(300, contentItem.implicitWidth + leftPadding + rightPadding)
            implicitHeight: 24; font.pixelSize: 11; quiet: true
            text: root.summary; tip: qsTranslate("Tingmu", "查看下载") + " · " + root.summary
            onClicked: root.detailsRequested()
        }
    }
    Component {
        id: fullView
        ColumnLayout {
            spacing: 5
            Text {
                Layout.fillWidth: true; text: root.heading
                wrapMode: Text.WrapAnywhere; textFormat: Text.PlainText
                color: Theme.text; font.family: Theme.font; font.pixelSize: 12
            }
            Text {
                Layout.fillWidth: true; visible: !!root.sample.packages
                text: qsTranslate("Tingmu", "安装包 %1/%2").arg(root.sample.package || 0).arg(root.sample.packages || 0)
                    + (root.sample.filename ? " · " + root.sample.filename : "")
                wrapMode: Text.WrapAnywhere; textFormat: Text.PlainText
                color: Theme.muted; font.family: Theme.font; font.pixelSize: 11
            }
            AppProgressBar {
                objectName: root.barObjectName; Layout.fillWidth: true
                indeterminate: !root.transferring || root.total <= 0
                value: root.fraction
            }
            Text {
                Layout.fillWidth: true; visible: root.transferring || root.done > 0
                text: qsTranslate("Tingmu", "文件 ") + S.bytes(root.done)
                    + (root.total > 0 ? " / " + (root.sample.estimated ? qsTranslate("Tingmu", "预计 ") : "")
                        + S.bytes(root.total) + " · " + root.percent : "")
                    + (root.transferring ? qsTranslate("Tingmu", "\n本次接收 ") + S.bytes(root.sample.received)
                        + " · " + root.speed + (root.eta ? " · " + root.eta : "") : "")
                wrapMode: Text.Wrap; textFormat: Text.PlainText
                color: Theme.muted; font.family: Theme.font; font.pixelSize: 11
            }
        }
    }
}
