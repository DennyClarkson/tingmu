import QtQuick
import QtQuick.Controls.Basic
import "Style.js" as S
CheckBox {
    id: control
    implicitHeight: 30
    font.family: Theme.font
    font.pixelSize: 12
    spacing: 8
    indicator: Rectangle {
        implicitWidth: 16; implicitHeight: 16
        x: control.leftPadding; y: (control.height-height)/2
        radius: 3
        color: control.checked ? Theme.accent : Theme.bg
        border.color: control.checked ? Theme.accent : Theme.muted
        Text { textFormat:Text.PlainText; anchors.centerIn: parent; text: "✓"; color: Theme.bg; visible: control.checked; font.pixelSize: 12 }
    }
    contentItem: Text { textFormat:Text.PlainText; text: control.text; font: control.font; color: Theme.text; leftPadding: control.indicator.width + control.spacing; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
}
