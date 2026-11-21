import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ItemDelegate {
    id: row
    required property var modelData
    property bool currentWorkspace: false
    objectName: "workspaceRow_" + modelData.id
    height: 50; leftPadding: 10; rightPadding: 12
    background: Rectangle {
        color: row.currentWorkspace ? Theme.selection : row.hovered ? Theme.hover : "transparent"
        radius: 5
    }
    contentItem: RowLayout {
        ColumnLayout {
            Layout.fillWidth: true; spacing: 0
            Text { Layout.fillWidth: true; text: row.modelData.name; color: Theme.text; font.family: Theme.font; font.pixelSize: 13; elide: Text.ElideRight; textFormat: Text.PlainText }
            Text { Layout.fillWidth: true; text: row.modelData.languages; color: Theme.muted; font.family: Theme.font; font.pixelSize: 11; elide: Text.ElideRight; textFormat: Text.PlainText }
        }
        Text { visible: row.currentWorkspace; text: "✓"; color: Theme.accent }
    }
}
