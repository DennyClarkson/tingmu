import QtQuick
import QtQuick.Controls.Basic

ScrollBar {
    id: control
    padding: 0
    hoverEnabled: true
    minimumSize: length > 0 ? Math.min(1, 18 / length) : 0
    readonly property real length: horizontal ? width : height
    implicitWidth: horizontal ? 40 : 6
    implicitHeight: horizontal ? 6 : 40
    background: null
    contentItem: Rectangle {
        radius: 3
        color: control.hovered || control.pressed ? Theme.scrollThumbHover : Theme.scrollThumb
        property real interactionOpacity: control.hovered || control.pressed || control.active ? 1 : 0.55
        opacity: control.size > 0 && control.size < 0.999 ? interactionOpacity : 0
        Behavior on interactionOpacity { NumberAnimation { duration: 140 } }
    }
}
