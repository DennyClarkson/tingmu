import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

AppDialog {
    id: consent
    objectName: "remoteTextPolicyConfirmation"
    required property Item origin
    parent: origin.Overlay.overlay
    function review() {
        offer = workspaces.remotePermissionOffer()
        changedOffer = false
        open()
    }
    anchors.centerIn: parent
    width: Math.min(470, parent ? parent.width - 20 : 470)
    modal: true
    title: qsTranslate("Tingmu", "核对远程文本权限")
    property var offer: ({roles: []})
    property bool changedOffer: false
    contentItem: ColumnLayout {
        spacing: 10
        ConsentText {
            text: qsTranslate("Tingmu", "授权后，工作区「%1」可向分配的远程服务发送字幕和学习文本。自动准备视频也会使用此权限。音轨上传需在语音设置单独授权。")
                .arg(workspaces.info.id === "default" ? qsTranslate("Tingmu", workspaces.info.name) : workspaces.info.name)
        }
        Repeater {
            model: consent.offer.roles
            delegate: ConsentText {
                required property var modelData
                text: (modelData.role === "annotation" ? qsTranslate("Tingmu", "逐句讲解") :
                       modelData.role === "review" ? qsTranslate("Tingmu", "字幕文本复核") : qsTranslate("Tingmu", "学习对话"))
                    + "  ·  " + modelData.model + "  ·  " + modelData.host
            }
        }
        ConsentText {
            visible: !consent.offer.roles.length
            text: qsTranslate("Tingmu", "当前没有已配置的远程角色。以后分配的远程连接也将使用此权限。")
        }
        ConsentText {
            visible: consent.changedOffer; color: Theme.warning
            text: qsTranslate("Tingmu", "连接已变更，请重新核对后确认。")
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            TButton { objectName: "cancelRemotePolicy"; text: qsTranslate("Tingmu", "暂不允许"); onClicked: consent.close() }
            TButton {
                objectName: "confirmRemotePolicy"; primary: true
                text: qsTranslate("Tingmu", "允许远程文本处理")
                onClicked: {
                    if (workspaces.confirmRemotePermission(consent.offer.signature)) consent.close()
                    else { consent.offer = workspaces.remotePermissionOffer(); consent.changedOffer = true }
                }
            }
        }
    }
    component ConsentText: Text {
        Layout.fillWidth: true; color: Theme.text; font.family: Theme.font; font.pixelSize: 12
        wrapMode: Text.Wrap; textFormat: Text.PlainText
    }
}
