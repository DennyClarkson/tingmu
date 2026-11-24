import QtQuick
import QtQuick.Controls.Basic
import "Icons.js" as Icons

CheckBox {
    id: control
    implicitHeight: Theme.controlHeight
    font.family: Theme.font
    font.pixelSize: 12
    spacing: 8
    hoverEnabled: true
    opacity: enabled ? 1 : Theme.disabledOpacity
    HoverHandler { enabled: control.enabled; cursorShape: Qt.PointingHandCursor }
    background: Rectangle {
        radius: Theme.radiusSm
        color: control.down ? Theme.pressed : control.hovered ? Theme.hover : Theme.transparent
        border.width: control.visualFocus ? 1 : 0
        border.color: Theme.accent
    }
    indicator: Rectangle {
        implicitWidth: 16; implicitHeight: 16
        x: control.leftPadding; y: (control.height - height) / 2
        radius: 4
        color: control.checked ? Theme.accent : Theme.bg
        border.color: control.checked || control.hovered || control.visualFocus ? Theme.accent : Theme.muted
        Image {
            anchors.centerIn: parent
            width: 13; height: 13
            sourceSize.width: 20; sourceSize.height: 20
            source: Icons.source("check", Theme.onAccent)
            visible: control.checked
        }
    }
    contentItem: Text {
        textFormat: Text.PlainText; text: control.text; font: control.font; color: Theme.text
        leftPadding: control.indicator.width + control.spacing
        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
    }
}
