import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Button {
    id: picker
    objectName: "workspacePicker"
    signal createRequested()
    signal manageRequested()
    property var info: workspaces.info
    implicitWidth: 196; implicitHeight: 46
    padding: 9
    Accessible.name: qsTranslate("Tingmu", "切换学习工作区：") + (info.id === "default" ? qsTranslate("Tingmu", info.name) : info.name) + " · " + qsTranslate("TingmuLanguage", info.languages || "")
    onClicked: {
        if (menu.opened) menu.close()
        else { workspaces.refresh(); menu.open() }
    }
    background: Rectangle {
        radius: 6; color: picker.hovered || menu.opened ? Theme.hover : Theme.panel
        border.color: menu.opened || picker.activeFocus ? Theme.accent : Theme.line
    }
    contentItem: RowLayout {
        spacing: 7
        ColumnLayout {
            Layout.fillWidth: true; spacing: 0
            Text { Layout.fillWidth: true; text: (picker.info.id === "default" ? qsTranslate("Tingmu", picker.info.name) : picker.info.name); color: Theme.text; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold; elide: Text.ElideRight; textFormat: Text.PlainText }
            Text { Layout.fillWidth: true; text: qsTranslate("TingmuLanguage", picker.info.languages || ""); color: Theme.accent; font.family: Theme.font; font.pixelSize: 10; elide: Text.ElideRight; textFormat: Text.PlainText }
        }
        Canvas {
            Layout.preferredWidth: 10; Layout.preferredHeight: 6
            property color stroke: Theme.muted
            onStrokeChanged: requestPaint()
            onPaint: {
                var ctx = getContext("2d")
                ctx.clearRect(0,0,width,height); ctx.strokeStyle = stroke; ctx.lineWidth = 1.5
                ctx.beginPath(); ctx.moveTo(1,1); ctx.lineTo(5,5); ctx.lineTo(9,1); ctx.stroke()
            }
        }
    }
    Popup {
        id: menu; objectName: "workspacePickerPopup"
        y: picker.height + 5; width: 292; padding: 10
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color: Theme.panel; border.color: Theme.line; radius: 8 }
        contentItem: ColumnLayout {
            spacing: 6
            Text { text: qsTranslate("Tingmu", "切换学习工作区"); color: Theme.muted; font.family: Theme.font; font.pixelSize: 12 }
            ListView {
                id: list; objectName: "workspacePickerList"
                model: picker.info.items; Layout.fillWidth: true
                Layout.preferredHeight: Math.min(contentHeight, 230); clip: true
                ScrollBar.vertical: AppScrollBar {}
                delegate: WorkspaceRow {
                    width: Math.max(0,list.width-10); currentWorkspace: modelData.id === picker.info.id
                    onClicked: {
                        if (modelData.id === picker.info.id || workspaces.action("switch", {id: modelData.id})) menu.close()
                    }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
            RowLayout {
                Layout.fillWidth: true
                TButton { objectName: "workspacePickerCreate"; Layout.fillWidth: true; text: qsTranslate("Tingmu", "＋ 新建工作区"); onClicked: {menu.close(); picker.createRequested()} }
                TButton { objectName: "workspacePickerManage"; Layout.fillWidth: true; text: qsTranslate("Tingmu", "工作区设置"); onClicked: {menu.close(); picker.manageRequested()} }
            }
            Text { visible: !!text; Layout.fillWidth: true; text: qsTranslate("TingmuStatus", picker.info.error || ""); color: Theme.error; wrapMode: Text.Wrap; textFormat: Text.PlainText; font.family: Theme.font; font.pixelSize: 11 }
        }
    }
}
