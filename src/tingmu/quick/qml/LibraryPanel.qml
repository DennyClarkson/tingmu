import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S
Item {
    id:root
    property var selected: []
    readonly property bool compact: width < 260
    readonly property string query: search.text.trim().toLowerCase()
    readonly property var visibleLibrary: !query ? backend.library
        : backend.library.filter(item => item.name.toLowerCase().includes(query))
    readonly property bool allVisibleSelected: visibleLibrary.length > 0
        && visibleLibrary.every(item => selected.includes(item.path))
    onSelectedChanged:backend.command("setSelectedMedia",{paths:selected})
    function openSettings(){ settingsPopup.open() }
    function select(path,on) { var copy=selected.slice(); var i=copy.indexOf(path); if(on && i<0) copy.push(path); if(!on && i>=0) copy.splice(i,1); selected=copy }
    function selectVisible(checked) {
        const visiblePaths = new Set(visibleLibrary.map(item => item.path))
        selected = checked ? Array.from(new Set(selected.concat(Array.from(visiblePaths))))
                           : selected.filter(path => !visiblePaths.has(path))
    }
    Connections {
        target: backend
        function onLibraryChanged() {
            if (!root.selected.length) return
            const paths = new Set(backend.library.map(item => item.path))
            const remaining = root.selected.filter(path => paths.has(path))
            if (remaining.length !== root.selected.length) root.selected = remaining
        }
    }
    ColumnLayout {
        anchors.fill:parent; anchors.margins:12; spacing:10
        RowLayout {
            Layout.fillWidth:true
            TButton { text:qsTranslate("Tingmu", "＋ 文件"); Layout.fillWidth:true; onClicked:backend.command("addFiles",{}) }
            TButton { text:qsTranslate("Tingmu", "文件夹"); Layout.fillWidth:true; onClicked:backend.command("openFolder",{}) }
        }
        TButton {
            Layout.fillWidth:true
            text: root.compact ? qsTranslate("Tingmu", "复制到工作区…") : qsTranslate("Tingmu", "复制视频到工作区…")
            tip: root.compact ? qsTranslate("Tingmu", "复制视频到工作区…") : ""
            onClicked:backend.command("copyFiles",{})
        }
        Field { id:search; objectName:"librarySearch"; clearable:true; Layout.fillWidth:true; placeholderText:qsTranslate("Tingmu", "查找视频") }
        RowLayout {
            Layout.fillWidth:true
            Toggle { objectName:"selectVisibleMedia"; text:qsTranslate("Tingmu", "全选"); enabled:root.visibleLibrary.length>0; checked:root.allVisibleSelected; onToggled:root.selectVisible(checked) }
            Item { Layout.fillWidth:true }
            TButton {
                text: root.width<220 ? "" : qsTranslate("Tingmu", "清除选择")
                iconSource: root.width<220 ? "close" : ""
                tip: root.width<220 ? qsTranslate("Tingmu", "清除选择") : ""
                quiet:true; enabled:root.selected.length>0; onClicked:root.selected=[]
            }
        }
        ListView {
            id:files; objectName:"libraryList"
            Layout.fillWidth:true; Layout.fillHeight:true; clip:true; spacing:5
            model:root.visibleLibrary
            ScrollBar.vertical:AppScrollBar { id:scrollBar }
            delegate:Rectangle {
                id:mediaRow
                required property var modelData
                readonly property bool current: backend.mediaState.mediaPath === modelData.path
                width:Math.max(0,files.width-scrollBar.width); height:66
                radius:5; color:current ? Theme.selection : rowHover.hovered ? Theme.hover : Theme.transparent
                HoverHandler { id: rowHover }
                MouseArea { id:rowMouse; anchors.fill:parent; hoverEnabled:true; cursorShape:Qt.PointingHandCursor; onClicked:backend.command("openMedia",{path:mediaRow.modelData.path}) }
                Rectangle {
                    objectName: "currentMediaIndicator"
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.bottom: parent.bottom
                    anchors.topMargin: 6
                    anchors.bottomMargin: 6
                    width: 2
                    color: Theme.accent
                    visible: mediaRow.current
                }
                RowLayout {
                    anchors.fill:parent; anchors.margins:7; spacing:7
                    Toggle { objectName:"selectMediaRow"; checked:root.selected.indexOf(mediaRow.modelData.path)>=0; onToggled:root.select(mediaRow.modelData.path,checked) }
                    ColumnLayout {
                        Layout.fillWidth:true; spacing:6
                        Text { textFormat:Text.PlainText; Layout.fillWidth:true; text:mediaRow.modelData.name; color:Theme.text; font.family:Theme.font; font.pixelSize:12; elide:Text.ElideMiddle }
                        RowLayout {
                            Layout.fillWidth:true
                            Text { textFormat:Text.PlainText; Layout.fillWidth:true; text:mediaRow.modelData.parent || qsTranslate("Tingmu", "视频"); color:Theme.muted; font.family:Theme.font; font.pixelSize:10; elide:Text.ElideLeft }
                            StatusBadge {
                                objectName: "mediaStatusBadge"
                                text: S.status(mediaRow.modelData.status)
                                filledAccent: ["ready", "completed"].includes(mediaRow.modelData.status)
                                tone: filledAccent || mediaRow.modelData.status === "running" ? "accent"
                                    : mediaRow.modelData.status === "failed" ? "danger" : mediaRow.modelData.status === "missing" ? "warning" : "neutral"
                            }
                        }
                    }
                    Item {
                        Layout.preferredWidth: Theme.compactHeight
                        Layout.preferredHeight: Theme.compactHeight
                        TButton {
                            id: removeButton
                            objectName: "removeMediaRow"
                            anchors.fill: parent
                            visible: rowHover.hovered
                            quiet: true
                            danger: true
                            horizontalPadding: 2
                            iconSource: "trash"
                            tip: qsTranslate("Tingmu", "仅移出列表，不删除原文件")
                            onClicked: backend.command("removeMedia", {paths: [mediaRow.modelData.path]})
                        }
                    }
                }
                AppToolTip { visible:rowMouse.containsMouse && !removeButton.hovered; delay:1000; text:mediaRow.modelData.path }
            }
            Column {
                visible:files.count===0 && !root.query; anchors.centerIn:parent; width:parent.width-20; spacing:10
                Text { textFormat:Text.PlainText; text:qsTranslate("Tingmu", "你的放映室"); color:Theme.text; font.family:Theme.font; font.pixelSize:18 }
                Text { textFormat:Text.PlainText; width:parent.width; text:backend.settings.auto_prepare_media ? qsTranslate("Tingmu", "把视频放进当前工作区的 media 文件夹，复制完成后会自动准备字幕。") : qsTranslate("Tingmu", "可引用已有视频，或把视频放进当前工作区的 media 文件夹。"); color:Theme.muted; font.family:Theme.font; font.pixelSize:12; wrapMode:Text.Wrap; lineHeight:1.4 }
                TButton { text:qsTranslate("Tingmu", "打开视频目录"); onClicked:workspaces.action("mediaFolder",{}) }
            }
            ColumnLayout {
                visible: files.count === 0 && !!root.query
                anchors.centerIn: parent
                Text {
                    objectName: "libraryNoResults"
                    text: qsTranslate("Tingmu", "没有匹配的视频")
                    color: Theme.muted
                    font.family: Theme.font
                    font.pixelSize: 12
                }
                TButton {
                    objectName: "clearLibrarySearch"
                    Layout.alignment: Qt.AlignHCenter
                    text: qsTranslate("Tingmu", "清除搜索")
                    quiet: true
                    onClicked: search.clear()
                }
            }
        }
        Text {
            Layout.fillWidth:true; visible:!!backend.mediaInbox.blocked
            text:qsTranslate("TingmuStatus", backend.mediaInbox.blocked || ""); textFormat:Text.PlainText
            color:Theme.muted; font.family:Theme.font; font.pixelSize:12; wrapMode:Text.Wrap
        }
        TButton {
            objectName: "enqueueSelectedMedia"
            Layout.fillWidth:true
            text: {
                if (root.compact)
                    return root.selected.length ? qsTranslate("Tingmu", "加入队列（%1）").arg(root.selected.length) : qsTranslate("Tingmu", "加入队列")
                return root.selected.length ? qsTranslate("Tingmu", "加入处理队列 · ") + root.selected.length + qsTranslate("Tingmu", " 个") : qsTranslate("Tingmu", "勾选视频后加入队列")
            }
            primary:root.selected.length>0; enabled:root.selected.length>0
            onClicked:{backend.command("enqueue",{paths:root.selected});root.selected=[]}
        }
    }
    SettingPopup {
        id:settingsPopup
        contentItem:ColumnLayout {
            spacing:10
            Text { textFormat:Text.PlainText; text:qsTranslate("Tingmu", "媒体库"); color:Theme.text; font.family:Theme.font; font.pixelSize:16 }
            Text { textFormat:Text.PlainText; Layout.fillWidth:true; text:qsTranslate("Tingmu", "可同时选择多个视频；扫描文件夹仅建立索引，不复制媒体。"); color:Theme.muted; font.family:Theme.font; font.pixelSize:12; wrapMode:Text.Wrap }
            TButton { text:qsTranslate("Tingmu", "打开数据文件夹"); onClicked:backend.command("openDataFolder",{}) }
            TButton { text:qsTranslate("Tingmu", "扫描工作区 media 文件夹"); onClicked:backend.command("scanWorkspaceMedia",{}) }
            Text { textFormat:Text.PlainText; Layout.fillWidth:true; text:backend.applicationState.dataDir || ""; color:Theme.muted; font.family:Theme.font; font.pixelSize:10; wrapMode:Text.WrapAnywhere }
        }
    }
}
