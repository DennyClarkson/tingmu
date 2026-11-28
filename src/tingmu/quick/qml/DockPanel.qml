import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Icons.js" as Icons
Item {
    id: dock
    property string title: ""
    property string subtitle: ""
    property string moduleId: ""
    property bool detached: false
    property Component panelContent
    property var workspace
    property var dockHost:workspace ? workspace.dockHost(moduleId) : dock
    readonly property var studentHost: workspace && !workspace.developer ? workspace.studentHost(moduleId) : null
    property bool studentDetached: false
    readonly property bool floatingPanel: studentHost ? studentDetached : detached && !!workspace && workspace.developer
    property alias loadedItem: loader.item
    signal settingsRequested()
    function save() {
        if (workspace) workspace.saveModule(moduleId, {detached:detached,x:floating.x,y:floating.y,width:floating.width,height:floating.height})
    }
    function present(){if(detached){floating.raise();floating.requestActivate()}else if(workspace){workspace.raise();workspace.requestActivate()}}
    function detach() {
        if (studentHost) studentDetached = !studentDetached
        else { detached = !detached; save() }
    }
    Timer{id:videoSceneSettled;interval:120;onTriggered:backend.refreshVideo()}
    Component.onCompleted: {
        var saved = workspace ? workspace.moduleLayout(moduleId) : null
        if (saved) {
            floating.width = Math.max(330, saved.width || (dock.moduleId==="chat" ? 920 : 460))
            floating.height = Math.max(260, saved.height || (dock.moduleId==="chat" ? 760 : 580))
            if (saved.x !== undefined) floating.x = saved.x
            if (saved.y !== undefined) floating.y = saved.y
            detached = saved.detached || false
        }
    }
    Window {
        id: floating
        title: dock.title + qsTranslate("Tingmu", " · 听幕")
        width:dock.moduleId==="chat" ? 920 : 460; height:dock.moduleId==="chat" ? 760 : 580
        minimumWidth: 330; minimumHeight: 260
        color: Theme.bg
        visible: dock.floatingPanel && (!dock.studentHost || dock.studentHost.visible)
        onClosing: function(close) { close.accepted = false; dock.detach() }
        onXChanged: if (visible && !dock.studentHost) dock.save()
        onYChanged: if (visible && !dock.studentHost) dock.save()
        onWidthChanged: if (visible && !dock.studentHost) dock.save()
        onHeightChanged: if (visible && !dock.studentHost) dock.save()
    }
    TButton {
        parent: dock.studentHost || dock
        anchors.centerIn: parent
        visible: !!dock.studentHost && dock.studentDetached
        text: qsTranslate("Tingmu", "放回工作区")
        iconSource: "arrow-down-left"
        onClicked: dock.detach()
    }
    Rectangle {
        id: surface
        objectName: dock.moduleId + "Surface"
        parent: dock.floatingPanel ? floating.contentItem : (dock.studentHost || dock.dockHost)
        onParentChanged:if(dock.moduleId==="player"){Qt.callLater(function(){backend.refreshVideo()});videoSceneSettled.restart()}
        anchors.fill: parent
        color: Theme.panel
        radius: Theme.radiusMd
        clip: true
        ColumnLayout {
            anchors.fill: parent
            anchors.margins: 1
            spacing: 0
            Item {
                visible: !dock.studentHost || dock.moduleId !== "player"
                Layout.fillWidth: true; Layout.preferredHeight: 40
                MouseArea {
                    id: titleDrag
                    anchors.fill: parent
                    hoverEnabled: true
                    property real pressX: 0
                    property real pressY: 0
                    property bool moved:false
                    cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                    onPressed: function(mouse) { pressX = mouse.x; pressY = mouse.y;moved=false }
                    onPositionChanged:function(mouse){if(pressed && (Math.abs(mouse.x-pressX)>10 || Math.abs(mouse.y-pressY)>10)){moved=true;if(dock.workspace && !dock.studentHost)dock.workspace.previewDrop(mapToGlobal(mouse.x,mouse.y))}}
                    onReleased: function(mouse) {
                        if(moved && dock.workspace && !dock.studentHost && dock.workspace.finishDrop(dock.moduleId,mapToGlobal(mouse.x,mouse.y)))return
                        if (!dock.floatingPanel && moved) {
                            var p = mapToGlobal(mouse.x,mouse.y)
                            floating.x = p.x-120; floating.y = p.y-20
                            dock.detach()
                        }
                        if(dock.workspace)dock.workspace.dropTarget=""
                    }
                    onDoubleClicked: dock.detach()
                }
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 6
                    spacing: 5
                    Image {
                        Layout.preferredWidth: 12; Layout.preferredHeight: 16
                        source: Icons.source("grip", titleDrag.containsMouse ? Theme.accent : Theme.muted)
                        sourceSize.width: 18; sourceSize.height: 18; fillMode: Image.PreserveAspectFit
                    }
                    Text { Layout.minimumWidth: 0; elide: Text.ElideRight; textFormat:Text.PlainText; text: dock.title; color: Theme.text; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
                    StatusBadge {
                        visible: !!dock.subtitle
                        text: dock.subtitle
                        Layout.minimumWidth: 0
                        Layout.maximumWidth: Math.min(180, parent.width * 0.35)
                    }
                    Item { Layout.fillWidth: true }
                    TButton {
                        visible: !!dock.studentHost && (dock.moduleId === "companion" || dock.moduleId === "chat")
                        objectName: dock.moduleId === "companion" ? "studentChatTab" : "studentCueTab_" + dock.moduleId
                        text: dock.moduleId === "companion" ? qsTranslate("Tingmu", "对话") : qsTranslate("Tingmu", "当前句")
                        quiet: true
                        onClicked: dock.workspace.showChat = dock.moduleId === "companion"
                    }
                    TButton { iconSource: "settings"; quiet: true; implicitWidth: Theme.compactHeight; implicitHeight: Theme.compactHeight; tip: qsTranslate("Tingmu", "此模块设置"); objectName: dock.moduleId + "Settings"; onClicked: { if (loader.item && loader.item.openSettings) loader.item.openSettings(); dock.settingsRequested() } }
                    TButton { iconSource: dock.floatingPanel ? "arrow-down-left" : "arrow-up-right"; quiet: true; implicitWidth: Theme.compactHeight; implicitHeight: Theme.compactHeight; tip: dock.floatingPanel ? qsTranslate("Tingmu", "放回工作区") : qsTranslate("Tingmu", "浮动窗口 · 也可拖动标题"); objectName: dock.moduleId + "Detach"; onClicked: dock.detach() }
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
            Loader { id: loader; Layout.fillWidth: true; Layout.fillHeight: true; sourceComponent: dock.panelContent }
        }
        Rectangle {
            anchors.fill: parent
            z: 1
            radius: surface.radius
            color: Theme.transparent
            border.width: 1
            border.color: Theme.line
            enabled: false
        }
    }
    Rectangle {
        anchors.fill:parent;z:1000;radius:Theme.radiusMd
        visible:!!dock.workspace && dock.workspace.dropTarget===dock.moduleId
        color:Theme.dropTint;border.color:Theme.accent;border.width:2
        Text { textFormat:Text.PlainText;anchors.centerIn:parent;text:qsTranslate("Tingmu", "放到这里");color:Theme.text;font.family:Theme.font;font.pixelSize:16}
    }
}
