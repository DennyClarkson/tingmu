import QtQuick
import QtQuick.Controls.Basic
import "Style.js" as S
Popup {
    id: root
    width: Math.min(330, parent ? parent.width : 330)
    x: parent ? Math.max(0, parent.width-width-8) : 0
    y: 4
    padding: 16
    modal: false
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { color: Theme.raised; radius: 8; border.color: Theme.line }
    palette.text: Theme.text
    palette.windowText: Theme.text
}
