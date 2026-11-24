import QtQuick
import QtQuick.Controls.Basic

TextField {
    id: control
    property bool clearable: false
    readonly property bool canClear: clearable && !readOnly && length > 0
    implicitHeight: Theme.controlHeight
    color: Theme.text
    placeholderTextColor: Theme.muted
    selectionColor: Theme.selection
    selectedTextColor: Theme.text
    font.family: Theme.font
    font.pixelSize: 12
    padding: 8
    leftPadding: 10
    rightPadding: canClear ? clearButton.width + 5 : 10
    selectByMouse: true
    hoverEnabled: true
    opacity: enabled ? 1 : Theme.disabledOpacity
    Keys.onEscapePressed: function(event) {
        event.accepted = canClear
        if (canClear) clear()
    }
    background: Rectangle {
        color: Theme.bg
        radius: Theme.radiusSm
        border.color: control.activeFocus ? Theme.accent : control.hovered ? Theme.muted : Theme.line
    }
    TButton {
        id: clearButton
        anchors.right: parent.right
        anchors.rightMargin: 3
        anchors.verticalCenter: parent.verticalCenter
        width: Theme.compactHeight
        height: Math.min(Theme.compactHeight, control.height)
        visible: control.canClear
        quiet: true
        horizontalPadding: 2
        iconSource: "close"
        tip: qsTranslate("Tingmu", "清空输入")
        onClicked: { control.clear(); control.forceActiveFocus() }
    }
}
