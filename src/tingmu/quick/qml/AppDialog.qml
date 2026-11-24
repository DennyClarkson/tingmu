import QtQuick
import QtQuick.Controls.Basic

Dialog {
    id: root
    property bool destructiveAccept: false
    property string acceptText: ""
    padding: 20
    font.family: Theme.font
    font.pixelSize: 13
    palette.window: Theme.panel
    palette.base: Theme.bg
    palette.text: Theme.text
    palette.windowText: Theme.text
    palette.button: Theme.raised
    palette.buttonText: Theme.text
    palette.highlight: Theme.accent
    palette.highlightedText: Theme.onAccent
    function translateButtons() {
        const labels = [[Dialog.Ok, acceptText || qsTranslate("Tingmu", "确定")], [Dialog.Cancel, qsTranslate("Tingmu", "取消")],
                        [Dialog.Close, qsTranslate("Tingmu", "关闭")], [Dialog.Discard, qsTranslate("Tingmu", "放弃")]]
        for (const [kind, label] of labels) {
            if (!(standardButtons & kind)) continue
            const button = standardButton(kind)
            if (button) button.text = label
        }
    }
    Component.onCompleted: Qt.callLater(translateButtons)
    onStandardButtonsChanged: Qt.callLater(translateButtons)
    onAcceptTextChanged: Qt.callLater(translateButtons)
    Connections { target: interfaceLanguage; function onChanged() { root.translateButtons() } }
    Overlay.modal: Rectangle { color: Theme.modalScrim }
    background: Rectangle { color: Theme.surfaceElevated; radius: Theme.radiusLg; border.color: Theme.line }
    header: Text {
        text: root.title; textFormat: Text.PlainText
        leftPadding: 20; rightPadding: 20; topPadding: 20; bottomPadding: 14
        color: Theme.text; font.family: Theme.serif; font.pixelSize: 20
        elide: Text.ElideRight
    }
    footer: DialogButtonBox {
        visible: root.standardButtons !== Dialog.NoButton
        standardButtons: root.standardButtons
        padding: 16; spacing: 8
        background: null
        delegate: TButton {
            primary: DialogButtonBox.buttonRole === DialogButtonBox.AcceptRole
            danger: DialogButtonBox.buttonRole === DialogButtonBox.DestructiveRole || (primary && root.destructiveAccept)
            leftPadding: 16; rightPadding: 16
        }
    }
}
