import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

AppDialog {
    id: dialog
    objectName: "agentModelDownload"
    signal confirmationRequested()
    property var proposal: (workspaces.onboarding || {}).proposal || ({})
    property string displayedId: ""
    readonly property bool pending: proposal.status === "pending"
    readonly property bool downloading: proposal.status === "downloading"
    readonly property real bytes: proposal.download_bytes || proposal.estimated_download_bytes || 0
    anchors.centerIn: parent
    width: Math.min(430, parent.width - 28)
    modal: false
    focus: false
    closePolicy: Popup.NoAutoClose
    title: pending ? "下载语音识别模型？" : "模型下载"

    function syncProposal() {
        if (pending && proposal.id && displayedId !== proposal.id) {
            displayedId = proposal.id
            confirmationRequested()
            open()
        } else if (opened && (!pending || !proposal.id || proposal.id !== displayedId)) {
            close()
        }
    }
    onProposalChanged: Qt.callLater(syncProposal)
    contentItem: ColumnLayout {
        spacing: 12
        Text {
            Layout.fillWidth: true; textFormat: Text.PlainText; wrapMode: Text.Wrap
            color: Theme.text; font.family: Theme.font; font.pixelSize: 15
            text: (dialog.proposal.workspace_name || "") + "  ·  " + (dialog.proposal.model_name || dialog.proposal.model || "")
        }
        Text {
            Layout.fillWidth: true; textFormat: Text.PlainText; wrapMode: Text.Wrap
            color: Theme.muted; font.family: Theme.font; font.pixelSize: 13
            text: dialog.pending && workspaces.info.error ? workspaces.info.error
                : dialog.pending ? ((dialog.bytes > 0 ? "预计下载 " + (dialog.bytes >= 1000000000
                      ? (dialog.bytes / 1000000000).toFixed(1) + " GB" : Math.ceil(dialog.bytes / 1000000) + " MB") + "。" : "")
                      + "模型存入听幕的共享目录。你也可以在原对话里回复「同意下载」或「取消下载」。")
                : dialog.proposal.error || dialog.proposal.progress || ({
                    downloading: "正在下载到共享目录…", ready: "模型已就绪。", declined: "已取消这次下载。",
                    cancelled: "下载已停止。", stale: "工作区设置已变更，请重新推荐模型。"
                })[dialog.proposal.status] || ""
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            TButton {
                objectName: "declineAgentDownload"; visible: dialog.pending
                text: "暂不下载"; onClicked: { workspaces.decideProposal(dialog.displayedId, false); dialog.close() }
            }
            TButton {
                objectName: "approveAgentDownload"; visible: dialog.pending; primary: true
                text: "同意下载"; onClicked: {
                    if (workspaces.decideProposal(dialog.displayedId, true)) dialog.close()
                }
            }
            TButton {
                visible: !dialog.pending; text: dialog.downloading ? "后台下载" : "关闭"
                onClicked: dialog.close()
            }
        }
    }
}
