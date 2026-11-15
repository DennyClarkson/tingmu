import QtQuick
import QtQuick.Controls.Basic
import "Style.js" as S
TextField {
    id: control
    implicitHeight: 34
    color: Theme.text
    placeholderTextColor: Theme.muted
    selectionColor: Theme.selection
    selectedTextColor: Theme.text
    font.family: Theme.font
    font.pixelSize: 12
    padding: 10
    selectByMouse: true
    background: Rectangle {
        color: Theme.bg
        radius: 5
        border.color: control.activeFocus ? Theme.accent : Theme.line
    }
}
