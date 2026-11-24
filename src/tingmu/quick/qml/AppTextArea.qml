import QtQuick
import QtQuick.Controls.Basic

ScrollView {
    id: control
    property alias editor: input
    property alias text: input.text
    property alias placeholderText: input.placeholderText
    property alias placeholderTextColor: input.placeholderTextColor
    property alias color: input.color
    property alias selectionColor: input.selectionColor
    property alias selectedTextColor: input.selectedTextColor
    property alias readOnly: input.readOnly
    property alias wrapMode: input.wrapMode
    property alias selectByMouse: input.selectByMouse
    property alias textFormat: input.textFormat
    property alias cursorPosition: input.cursorPosition
    readonly property alias cursorRectangle: input.cursorRectangle
    readonly property alias selectedText: input.selectedText
    readonly property alias length: input.length

    function clear() { input.clear() }
    function selectAll() { input.selectAll() }
    function copy() { input.copy() }
    function cut() { input.cut() }
    function paste() { input.paste() }

    implicitHeight: 96
    implicitWidth: 180
    clip: true
    contentWidth: availableWidth
    font.family: Theme.font
    font.pixelSize: 13
    opacity: enabled ? 1 : Theme.disabledOpacity
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ScrollBar.vertical: AppScrollBar {
        parent: control
        x: control.width - width - 2
        y: 2
        height: control.height - 4
    }
    background: Rectangle {
        color: Theme.bg; radius: Theme.radiusSm
        border.color: control.activeFocus ? Theme.accent : Theme.line
    }
    Flickable {
        boundsBehavior: Flickable.StopAtBounds
        flickableDirection: Flickable.VerticalFlick
        TextArea.flickable: TextArea {
            id: input
            implicitWidth: 120
            focus: true
            font: control.font
            color: Theme.text
            placeholderTextColor: Theme.muted
            selectionColor: Theme.selection
            selectedTextColor: Theme.text
            padding: 8
            rightPadding: 13
            wrapMode: TextEdit.Wrap
            selectByMouse: true
            textFormat: TextEdit.PlainText
            background: null
            Keys.forwardTo: [control]
        }
    }
}
