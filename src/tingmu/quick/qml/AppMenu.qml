import QtQuick
import QtQuick.Controls.Basic
import "Icons.js" as Icons

Menu {
    id: root
    implicitWidth: interfaceLanguage.code === "en" ? 290 : 248
    padding: 6
    font.family: Theme.font
    font.pixelSize: 12
    delegate: MenuItem {
        id: item
        implicitHeight: 38
        leftPadding: 32; rightPadding: subMenu ? 30 : 14
        opacity: enabled ? 1 : Theme.disabledOpacity
        contentItem: Text {
            text: item.text; textFormat: Text.PlainText
            color: item.highlighted ? Theme.accent : Theme.text
            font: item.font; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
        }
        indicator: Image {
            x: 9; anchors.verticalCenter: parent.verticalCenter
            visible: item.checked; width: 15; height: 15
            sourceSize.width: 20; sourceSize.height: 20
            source: Icons.source("check", Theme.accent)
        }
        arrow: Image {
            anchors.right: parent.right; anchors.rightMargin: 9
            anchors.verticalCenter: parent.verticalCenter
            visible: !!item.subMenu
            width: 13; height: 13; sourceSize.width: 20; sourceSize.height: 20
            source: Icons.source("chevron-right", item.highlighted ? Theme.accent : Theme.muted)
        }
        background: Rectangle { radius: Theme.radiusSm; color: item.highlighted ? Theme.accentSoft : Theme.transparent }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
    }
    background: Rectangle { color: Theme.surfaceElevated; radius: Theme.radiusMd; border.color: Theme.line }
}
