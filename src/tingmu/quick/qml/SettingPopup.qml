import QtQuick
import QtQuick.Controls.Basic

Popup {
    id: root
    objectName: "moduleSettingsPopup"
    width: Math.min(330, parent ? parent.width : 330)
    x: parent ? Math.max(0, parent.width - width - 8) : 0
    y: 4
    padding: 16
    modal: false
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    background: Rectangle { color: Theme.surfaceElevated; radius: Theme.radiusMd; border.color: Theme.line }
    palette.text: Theme.text
    palette.windowText: Theme.text
}
