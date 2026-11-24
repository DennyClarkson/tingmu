import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S

ColumnLayout {
    id: root
    property var sample: backend.downloadState.downloadProgress || ({})
    readonly property real total: sample.total || 0
    spacing: 4
    Text {
        Layout.fillWidth: true
        text: qsTranslate("TingmuStatus", backend.downloadState.downloadMessage || "") || qsTranslate("Tingmu", "正在检查模型文件")
        wrapMode: Text.WrapAnywhere; textFormat: Text.PlainText
        color: Theme.text; font.family: Theme.font; font.pixelSize: 12
    }
    AppProgressBar {
        objectName: "downloadProgressBar"; Layout.fillWidth: true
        indeterminate: root.total <= 0; value: root.total > 0 ? root.sample.completed / root.total : 0
    }
    Text {
        Layout.fillWidth: true; visible: !!root.sample.component
        text: qsTranslate("Tingmu", "文件 ") + S.bytes(root.sample.completed) + (root.total > 0 ? " / " + S.bytes(root.total)
              + "  ·  " + Math.min(100, Math.floor(root.sample.completed * 100 / root.total)) + "%" : "")
              + qsTranslate("Tingmu", "\n本次接收 ") + S.bytes(root.sample.received) + "  ·  " + S.bytes(root.sample.rate) + "/s"
        wrapMode: Text.Wrap; textFormat: Text.PlainText
        color: Theme.muted; font.family: Theme.font; font.pixelSize: 11
    }
}
