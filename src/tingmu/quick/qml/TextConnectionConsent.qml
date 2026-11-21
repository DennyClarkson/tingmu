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
    title: "在新工作区沿用文本模型？"
    contentItem: ColumnLayout {
        spacing: 12
        Text {
            Layout.fillWidth: true; textFormat: Text.PlainText; wrapMode: Text.Wrap; color: Theme.text
            font.family: Theme.font; font.pixelSize: 13
            text: "「" + (dialog.offer.workspace_name || "") + "」将通过以下连接处理字幕文本或对话。"
        }
        Repeater {
            model: dialog.offer.roles || []
            delegate: Text {
                required property var modelData
                Layout.fillWidth: true; textFormat: Text.PlainText; wrapMode: Text.Wrap; color: Theme.text
                font.family: Theme.font; font.pixelSize: 13
                text: modelData.role + "　" + modelData.model + " · " + modelData.host
            }
        }
        Text {
            Layout.fillWidth: true; textFormat: Text.PlainText; wrapMode: Text.Wrap
            color: dialog.offer.error ? Theme.error : Theme.muted; font.family: Theme.font; font.pixelSize: 12
            text: dialog.offer.error || "启用后，自动准备新视频时会把字幕文本发送给选中的复核和讲解服务。"
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            TButton { objectName: "skipTextReuse"; text: dialog.offer.status === "failed" ? "关闭" : "暂不沿用"
                onClicked: { if (dialog.needsDecision) workspaces.decideTextReuse(dialog.displayedId, false); dialog.close() } }
            TButton { objectName: "allowTextReuse"; visible: dialog.needsDecision; text: "沿用这些连接"; primary: true
                onClicked: { if (workspaces.decideTextReuse(dialog.displayedId, true)) dialog.close() } }
        }
    }
}
