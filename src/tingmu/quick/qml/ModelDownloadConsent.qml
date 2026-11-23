import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S

AppDialog {
    id: dialog
    objectName: "agentModelDownload"
    signal confirmationRequested()
    property var proposal: (workspaces.onboarding || {}).proposal || ({})
    property string displayedId: ""
    readonly property bool pending: proposal.status === "pending"
    readonly property bool downloading: proposal.status === "downloading"
    readonly property real bytes: proposal.download_bytes || 0
    anchors.centerIn: parent
    width: Math.min(430, parent.width - 28)
    modal: false
    focus: false
    closePolicy: Popup.NoAutoClose
    title: pending ? qsTranslate("Tingmu", "下载语音识别模型？") : qsTranslate("Tingmu", "模型下载")

    function syncProposal() {
        if (pending && proposal.id && displayedId !== proposal.id) {
            displayedId = proposal.id
            confirmationRequested()
            open()
        } else if (opened && ((!pending && !downloading) || !proposal.id || proposal.id !== displayedId)) {
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
            text: dialog.pending && workspaces.info.error ? qsTranslate("TingmuStatus", workspaces.info.error || "")
                : dialog.pending ? (qsTranslate("Tingmu", "模型包总量约 ") + S.bytes(dialog.proposal.estimated_download_bytes)
                      + qsTranslate("Tingmu", "，预计还需下载 ") + S.bytes(dialog.bytes) + "。"
                      + qsTranslate("Tingmu", "模型存入听幕的共享目录。你也可以在原对话里回复「同意下载」或「取消下载」。"))
                : qsTranslate("TingmuStatus", dialog.proposal.error || dialog.proposal.progress || "") || ({
                    downloading: qsTranslate("Tingmu", "正在下载到共享目录…"), ready: qsTranslate("Tingmu", "模型已就绪。"), declined: qsTranslate("Tingmu", "已取消这次下载。"),
                    cancelled: qsTranslate("Tingmu", "下载已停止。"), stale: qsTranslate("Tingmu", "工作区设置已变更，请重新推荐模型。")
                })[dialog.proposal.status] || ""
        }
        DownloadProgress { visible: dialog.downloading; Layout.fillWidth: true }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            TButton {
                objectName: "declineAgentDownload"; visible: dialog.pending
                text: qsTranslate("Tingmu", "暂不下载"); onClicked: { workspaces.decideProposal(dialog.displayedId, false); dialog.close() }
            }
            TButton {
                objectName: "approveAgentDownload"; visible: dialog.pending; primary: true
                text: qsTranslate("Tingmu", "同意下载"); onClicked: workspaces.decideProposal(dialog.displayedId, true)
            }
            TButton {
                visible: dialog.downloading; text: qsTranslate("Tingmu", "暂停下载"); onClicked: workspaces.action("stopDownload", {})
            }
            TButton {
                visible: !dialog.pending; text: dialog.downloading ? qsTranslate("Tingmu", "后台下载") : qsTranslate("Tingmu", "关闭")
                onClicked: dialog.close()
            }
        }
    }
}
