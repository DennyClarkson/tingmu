import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S

AppDialog {
    id: editor
    objectName: "subtitleCorrectionDialog"
    title: qsTranslate("Tingmu", "纠正此句")
    modal: true
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(540, parent ? parent.width - 20 : 540)
    height: Math.min(530, parent ? parent.height - 20 : 530)
    standardButtons: Dialog.NoButton
    property var captured: ({})
    property string messageId: ""
    property string localError: ""
    property var reply: null
    function refreshReply() {
        if (!opened || !messageId) { reply = null; return }
        var row = backend.messageModel.findById(messageId)
        reply = row.id ? row : null
    }
    onMessageIdChanged: refreshReply()
    onOpened: refreshReply()
    onClosed: { messageId = ""; reply = null }
    Connections {
        target: editor.opened && editor.messageId ? backend.messageModel : null
        function onDataChanged() { editor.refreshReply() }
        function onRowsInserted() { editor.refreshReply() }
        function onRowsRemoved() { editor.refreshReply() }
        function onModelReset() { editor.refreshReply() }
    }
    readonly property bool waiting: !!reply && ["pending", "streaming"].includes(reply.status)
    readonly property bool saved: !!reply && (reply.tool_events || []).some(function(e){
        return e.name === "revise_current_subtitle" && e.status === "success" && e.result && e.result.saved
    })
    function begin(cueId) {
        captured = backend.captureSubtitle(cueId)
        if (!captured.target) return
        messageId = ""
        localError = ""
        guidance.clear()
        open()
        guidance.forceActiveFocus()
    }
    contentItem: ColumnLayout {
        spacing: 10
        ScrollView {
            id: scroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: availableWidth
            ColumnLayout {
                width: scroll.availableWidth
                spacing: 10
                Text { color: Theme.accent; font.family: Theme.font; text: S.time((editor.captured.start || 0)*1000) + qsTranslate("Tingmu", "  /  已选字幕") }
                Text { Layout.fillWidth: true; text: editor.captured.text || ""; textFormat: Text.PlainText; wrapMode: Text.Wrap; color: Theme.text; font.family: Theme.font; font.pixelSize: 18 }
                Label { text: qsTranslate("Tingmu", "你确认的说法或意思"); font.family: Theme.font; color: Theme.text }
                TextArea {
                    id: guidance
                    objectName: "subtitleCorrectionGuidance"
                    Layout.fillWidth: true
                    Layout.preferredHeight: Math.max(88, implicitHeight)
                    placeholderText: qsTranslate("Tingmu", "例如：这里说的是「落車」，意思是下车，请一起改正字幕和讲解。")
                    wrapMode: TextEdit.Wrap
                    readOnly: editor.waiting || editor.saved
                    selectByMouse: true
                    color: Theme.text
                    placeholderTextColor: Theme.muted
                    font.family: Theme.font
                    background: Rectangle { color: Theme.bg; border.color: guidance.activeFocus ? Theme.accent : Theme.line; radius: 5 }
                }
                Text {
                    Layout.fillWidth: true
                    visible: !!editor.messageId || !!editor.localError
                    color: editor.saved ? Theme.accent : Theme.text
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    text: qsTranslate("TingmuStatus", editor.localError || "") || (editor.saved ? qsTranslate("Tingmu", "字幕和讲解已保存") :
                        !editor.reply ? qsTranslate("Tingmu", "请在本次对话中查看进度。") :
                        qsTranslate("TingmuStatus", editor.reply.error || "") || (["pending","streaming"].includes(editor.reply.status) ? qsTranslate("Tingmu", "伴侣正在复核…") :
                        qsTranslate("Tingmu", "本次未写入修改，请查看伴侣答复。")))
                }
                Text { Layout.fillWidth: true; visible: !!editor.reply && !!editor.reply.content; text: editor.reply ? editor.reply.content : ""; textFormat: Text.PlainText; wrapMode: Text.Wrap; color: Theme.text; font.family: Theme.font }
                TextEdit {
                    Layout.fillWidth: true
                    readOnly: true
                    selectByMouse: true
                    textFormat: TextEdit.PlainText
                    wrapMode: TextEdit.WrapAnywhere
                    text: qsTranslate("Tingmu", "工作区字幕\n") + (editor.captured.srtPath || "") + qsTranslate("Tingmu", "\n学习讲解\n") + (editor.captured.documentPath || "")
                    color: Theme.muted; font.family: Theme.font; font.pixelSize: 11
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Item { Layout.fillWidth: true }
            TButton { text: qsTranslate("Tingmu", "关闭"); quiet: true; onClicked: editor.close() }
            TButton {
                objectName: "submitSubtitleCorrection"
                text: editor.messageId && !editor.waiting && !editor.saved ? qsTranslate("Tingmu", "重新复核并保存") : qsTranslate("Tingmu", "复核并保存")
                primary: true
                enabled: !editor.saved && !editor.waiting && !!guidance.text.trim() && !backend.chatState.chatBusy
                onClicked: {
                    var result = backend.submitSubtitleCorrection(editor.captured, guidance.text)
                    editor.localError = result.error || ""
                    editor.messageId = result.messageId || ""
                }
            }
        }
    }
}
