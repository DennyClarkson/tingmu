import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S
Item {
    id:root
    property var selected: []
    onSelectedChanged:backend.command("setSelectedMedia",{paths:selected})
    function openSettings(){ settingsPopup.open() }
    function select(path,on) { var copy=selected.slice(); var i=copy.indexOf(path); if(on && i<0) copy.push(path); if(!on && i>=0) copy.splice(i,1); selected=copy }
    ColumnLayout {
        anchors.fill:parent; anchors.margins:12; spacing:10
        RowLayout {
            Layout.fillWidth:true
            TButton { text:"＋ 文件"; Layout.fillWidth:true; onClicked:backend.command("addFiles",{}) }
            TButton { text:"文件夹"; Layout.fillWidth:true; onClicked:backend.command("openFolder",{}) }
        }
        TButton { Layout.fillWidth:true; text:"复制视频到工作区…"; onClicked:backend.command("copyFiles",{}) }
        Field { id:search; Layout.fillWidth:true; placeholderText:"查找视频" }
        RowLayout {
            Layout.fillWidth:true
            Toggle { text:"全选"; checked:backend.library.length>0 && root.selected.length===backend.library.length; onToggled:root.selected=checked ? backend.library.map(function(item){return item.path}) : [] }
            Item { Layout.fillWidth:true }
            TButton { text:"清除选择"; quiet:true; enabled:root.selected.length>0; onClicked:root.selected=[] }
        }
        ListView {
            id:files; objectName:"libraryList"
            Layout.fillWidth:true; Layout.fillHeight:true; clip:true; spacing:5
            model:backend.library
            ScrollBar.vertical:ScrollBar{}
            delegate:Rectangle {
                id:mediaRow
                required property var modelData
                property bool matches: !search.text || modelData.name.toLowerCase().indexOf(search.text.toLowerCase())>=0
                width:files.width; height:matches ? 66 : 0; visible:matches
                radius:5; color:backend.state.mediaPath===modelData.path ? Theme.selection : rowMouse.containsMouse ? Theme.hover : "transparent"
                MouseArea { id:rowMouse; anchors.fill:parent; hoverEnabled:true; onClicked:backend.command("openMedia",{path:mediaRow.modelData.path}) }
                RowLayout {
                    anchors.fill:parent; anchors.margins:7; spacing:7
                    Toggle { checked:root.selected.indexOf(mediaRow.modelData.path)>=0; onToggled:root.select(mediaRow.modelData.path,checked) }
                    ColumnLayout {
                        Layout.fillWidth:true; spacing:6
                        Text { textFormat:Text.PlainText; Layout.fillWidth:true; text:mediaRow.modelData.name; color:Theme.text; font.family:Theme.font; font.pixelSize:12; elide:Text.ElideMiddle }
                        RowLayout {
                            Layout.fillWidth:true
                            Text { textFormat:Text.PlainText; Layout.fillWidth:true; text:mediaRow.modelData.parent || "视频"; color:Theme.muted; font.family:Theme.font; font.pixelSize:10; elide:Text.ElideLeft }
                            Text { textFormat:Text.PlainText; text:S.status(mediaRow.modelData.status); color:mediaRow.modelData.status==="completed" || mediaRow.modelData.status==="ready" ? Theme.accent : Theme.muted; font.family:Theme.font; font.pixelSize:10 }
                        }
                    }
                }
                ToolTip.visible:rowMouse.containsMouse
                ToolTip.delay:1000
                ToolTip.text:modelData.path
            }
            Column {
                visible:files.count===0; anchors.centerIn:parent; width:parent.width-20; spacing:10
                Text { textFormat:Text.PlainText; text:"你的放映室"; color:Theme.text; font.family:Theme.font; font.pixelSize:18 }
                Text { textFormat:Text.PlainText; width:parent.width; text:backend.settings.auto_prepare_media ? "把视频放进当前工作区的 media 文件夹，复制完成后会自动准备字幕。" : "可引用已有视频，或把视频放进当前工作区的 media 文件夹。"; color:Theme.muted; font.family:Theme.font; font.pixelSize:12; wrapMode:Text.Wrap; lineHeight:1.4 }
                TButton { text:"打开视频目录"; onClicked:workspaces.action("mediaFolder",{}) }
            }
        }
        Text {
            Layout.fillWidth:true; visible:!!(backend.state.mediaInbox || {}).blocked
            text:(backend.state.mediaInbox || {}).blocked || ""; textFormat:Text.PlainText
            color:Theme.muted; font.family:Theme.font; font.pixelSize:12; wrapMode:Text.Wrap
        }
        TButton { Layout.fillWidth:true; text:root.selected.length ? "加入处理队列 · " + root.selected.length + " 个" : "勾选视频后加入队列"; primary:root.selected.length>0; enabled:root.selected.length>0; onClicked:{backend.command("enqueue",{paths:root.selected});root.selected=[]} }
    }
    SettingPopup {
        id:settingsPopup
        contentItem:ColumnLayout {
            spacing:10
            Text { textFormat:Text.PlainText; text:"媒体库"; color:Theme.text; font.family:Theme.font; font.pixelSize:16 }
            Text { textFormat:Text.PlainText; Layout.fillWidth:true; text:"可同时选择多个视频；扫描文件夹仅建立索引，不复制媒体。"; color:Theme.muted; font.family:Theme.font; font.pixelSize:12; wrapMode:Text.Wrap }
            TButton { text:"打开数据文件夹"; onClicked:backend.command("openDataFolder",{}) }
            TButton { text:"扫描工作区 media 文件夹"; onClicked:backend.command("scanWorkspaceMedia",{}) }
            Text { textFormat:Text.PlainText; Layout.fillWidth:true; text:backend.state.dataDir || ""; color:Theme.muted; font.family:Theme.font; font.pixelSize:10; wrapMode:Text.WrapAnywhere }
        }
    }
}
