import QtQuick
import QtQuick.Layouts

Rectangle {
    id: bar
    default property alias buttons: strip.data
    implicitWidth: strip.implicitWidth + 8
    implicitHeight: Theme.controlHeight + 6
    radius: Theme.radiusMd
    color: Theme.panel
    border.color: Theme.line

    RowLayout {
        id: strip
        anchors.fill: parent
        anchors.margins: 3
        spacing: 2
    }
}
