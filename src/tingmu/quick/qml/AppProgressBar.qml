import QtQuick
import QtQuick.Controls.Basic

ProgressBar {
    id: control
    padding: 0
    implicitWidth: 140
    implicitHeight: 6
    background: Rectangle { color: Theme.line; radius: height / 2 }
    contentItem: Item {
        id: track
        clip: true
        Rectangle {
            id: fill
            height: parent.height
            width: parent.width * (control.indeterminate ? 0.30 : control.position)
            x: control.indeterminate ? (travel * (parent.width + width) - width)
                                     : control.mirrored ? parent.width - width : 0
            radius: height / 2
            color: Theme.accent
            property real travel: 0
            NumberAnimation on travel {
                from: 0; to: 1; duration: 1150; loops: Animation.Infinite
                running: control.indeterminate && control.visible && control.width > 0
            }
        }
    }
}
