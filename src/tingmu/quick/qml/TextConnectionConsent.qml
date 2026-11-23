import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

AppDialog {
    id: dialog
    objectName: "workspaceTextConsent"
    signal confirmationRequested()
    readonly property var setup: workspaces.onboarding || ({})
    readonly property var offer: setup.text_proposal || ({})
    readonly property bool needsDecision: offer.status === "pending" &&
        ["pending", "downloading"].indexOf((setup.proposal || {}).status) < 0
    property string displayedId: ""
    function updateOffer() {
        if (needsDecision && offer.id && offer.id !== displayedId) {
            displayedId = offer.id
            confirmationRequested()
            open()
        }
    }
    onSetupChanged: Qt.callLater(updateOffer)
    anchors.centerIn: parent
    width: Math.min(465, parent.width - 28)
    modal: true
    closePolicy: Popup.NoAutoClose
    title: qsTranslate("Tingmu", "允许此工作区使用远程文本模型？")
    contentItem: ColumnLayout {
        spacing: 12
        Text {
            Layout.fillWidth: true; textFormat: Text.PlainText; wrapMode: Text.Wrap; color: Theme.text
            font.family: Theme.font; font.pixelSize: 13
            text: qsTranslate("Tingmu", "「%1」将通过以下连接处理字幕文本或对话。").arg(dialog.offer.workspace_name || "")
        }
        Repeater {
            model: dialog.offer.roles || []
            delegate: Text {
                required property var modelData
                Layout.fillWidth: true; textFormat: Text.PlainText; wrapMode: Text.Wrap; color: Theme.text
                font.family: Theme.font; font.pixelSize: 13
                text: qsTranslate("TingmuStatus", modelData.role || "") + "　" + modelData.model + " · " + modelData.host
            }
        }
        Text {
            Layout.fillWidth: true; textFormat: Text.PlainText; wrapMode: Text.Wrap
            color: dialog.offer.error ? Theme.error : Theme.muted; font.family: Theme.font; font.pixelSize: 12
            text: qsTranslate("TingmuStatus", dialog.offer.error || "") || qsTranslate("Tingmu", "启用后，自动准备新视频时会把字幕文本发送给选中的复核和讲解服务。")
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            TButton { objectName: "skipTextReuse"; text: dialog.offer.status === "failed" ? qsTranslate("Tingmu", "关闭") : qsTranslate("Tingmu", "暂不允许")
                onClicked: { if (dialog.needsDecision) workspaces.decideTextReuse(dialog.displayedId, false); dialog.close() } }
            TButton { objectName: "allowTextReuse"; visible: dialog.needsDecision; text: qsTranslate("Tingmu", "允许远程文本处理"); primary: true
                onClicked: { if (workspaces.decideTextReuse(dialog.displayedId, true)) dialog.close() } }
        }
    }
}
