import QtQuick
import QtQuick.Controls.Basic

ProgressBar {
    id: control
    property bool animationEnabled: true
    readonly property bool animate: animationEnabled && indeterminate && width > 0 && Theme.canAnimate(control)
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
            x: control.indeterminate ? (control.animate ? travel * (parent.width + width) - width : (parent.width - width) / 2)
                                     : control.mirrored ? parent.width - width : 0
            radius: height / 2
            color: Theme.accent
            property real travel: 0
            NumberAnimation on travel {
                from: 0; to: 1; duration: 1150; loops: Animation.Infinite
                running: control.animate
            }
        }
    }
}
