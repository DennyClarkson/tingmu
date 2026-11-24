import QtQuick
import QtQuick.Controls.Basic

Slider {
    id: control
    implicitHeight: 20
    padding: 0
    hoverEnabled: true
    opacity: enabled ? 1 : Theme.disabledOpacity
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        implicitWidth: 180; implicitHeight: 3
        width: control.availableWidth; height: 3; radius: height / 2
        color: Theme.line
        Rectangle {
            width: control.position * parent.width
            x: control.mirrored ? parent.width - width : 0
            height: parent.height; radius: height / 2
            color: Theme.accent
        }
    }
    handle: Rectangle {
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + (control.availableHeight - height) / 2
        width: control.pressed ? 15 : control.hovered || control.visualFocus ? 13 : 9
        height: width
        radius: width / 2
        color: control.pressed ? Theme.accentPressed : control.hovered ? Theme.accentHover : Theme.accent
        border.width: control.visualFocus ? 2 : 0
        border.color: Theme.onAccent
        Behavior on width { NumberAnimation { duration: 100 } }
    }
}
