import QtQuick

TButton {
    id: control
    quiet: true
    Rectangle {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        width: 2
        height: Math.min(18, parent.height)
        radius: 1
        color: Theme.accent
        visible: control.checked
    }
}
