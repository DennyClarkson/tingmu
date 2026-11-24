import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Icons.js" as Icons

ItemDelegate {
    id: row
    required property var modelData
    property bool currentWorkspace: false
    objectName: "workspaceRow_" + modelData.id
    height: 54; leftPadding: 12; rightPadding: 12
    background: Rectangle {
        color: row.currentWorkspace ? Theme.selection : row.hovered ? Theme.hover : "transparent"
        radius: Theme.radiusSm
        border.width: row.visualFocus ? 1 : 0
        border.color: Theme.accent
        Rectangle {
            visible: row.currentWorkspace
            anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
            width: 2; height: 22; radius: 1; color: Theme.accent
        }
    }
    HoverHandler { cursorShape: Qt.PointingHandCursor }
    contentItem: RowLayout {
        ColumnLayout {
            Layout.fillWidth: true; spacing: 0
            Text { Layout.fillWidth: true; text: (row.modelData.id === "default" ? qsTranslate("Tingmu", row.modelData.name) : row.modelData.name); color: Theme.text; font.family: Theme.font; font.pixelSize: 13; elide: Text.ElideRight; textFormat: Text.PlainText }
            Text { Layout.fillWidth: true; text: qsTranslate("TingmuLanguage", row.modelData.languages || ""); color: Theme.muted; font.family: Theme.font; font.pixelSize: 11; elide: Text.ElideRight; textFormat: Text.PlainText }
        }
        Image { visible: row.currentWorkspace; Layout.preferredWidth: 14; Layout.preferredHeight: 14; source: Icons.source("check", Theme.accent) }
    }
}
