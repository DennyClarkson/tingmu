import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S
Item {
    id:root
    property bool follow:true
    property string query: ""
    property var cueIndex: ({rows: [], fields: null})
    function refreshCues() {
        cueIndex = {rows: backend.cueSnapshot(), fields: null}
    }
    function filteredRows(index, term) {
        if (!term) return index.rows
        if (!index.fields) {
            index.fields = index.rows.map(cue =>
                [cue.text, cue.translation, cue.reading].map(field => (field || "").toLowerCase()))
        }
        return index.rows.filter((cue, i) => index.fields[i].some(field => field.includes(term)))
    }
    Component.onCompleted: refreshCues()
    Timer {
        id: applySearch
        interval: 110
        onTriggered: root.query = search.text.trim().toLowerCase()
    }
    readonly property string activeId: backend.activeCue.id || ""
    function openSettings(){settingsPopup.open()}
    function followActiveCue() {
        if (follow && !query && backend.activeIndex >= 0)
            list.positionViewAtIndex(backend.activeIndex, ListView.Center)
    }
    onQueryChanged: if (!query) Qt.callLater(followActiveCue)
    onFollowChanged: if (follow) followActiveCue()
    Connections {
        target: backend
        function onActiveCueChanged() { root.followActiveCue() }
        function onCuesChanged() { root.refreshCues() }
    }
    ColumnLayout {
        anchors.fill:parent; anchors.margins:12; spacing:10
        GridLayout {
            Layout.fillWidth:true; columns:root.width<270 ? 2 : 3
            Field {
                id:search; objectName:"subtitleSearch"; clearable:true
                onTextChanged: {
                    if (text.trim()) applySearch.restart()
                    else { applySearch.stop(); root.query = "" }
                }
                Layout.fillWidth:true; Layout.columnSpan:root.width<270 ? 2 : 1
                placeholderText:root.width<400 ? qsTranslate("Tingmu", "搜索字幕")
                    : qsTranslate("Tingmu", "搜索字幕 · ") + backend.cueCount + qsTranslate("Tingmu", " 句")
            }
            TButton { text:qsTranslate("Tingmu", "跟随"); checked:root.follow; quiet:true; onClicked:root.follow=!root.follow }
            TButton {
                objectName:"importStudySubtitles"
                readonly property string actionLabel: backend.settings.preparation_workflow === "subtitle_agent"
                    ? qsTranslate("Tingmu", "字幕 Agent…") : backend.cueCount ? qsTranslate("Tingmu", "替换文档…") : qsTranslate("Tingmu", "从字幕建文档")
                text: root.width<400 ? "" : actionLabel
                iconSource: root.width<400 ? "folder" : ""
                Accessible.name: actionLabel
                tip: backend.settings.preparation_workflow === "subtitle_agent" ? qsTranslate("Tingmu", "选择字幕并加入 Agent 队列…") : qsTranslate("Tingmu", "使用字幕建立或替换此视频的学习文档")
                quiet:true; enabled:!!backend.mediaState.mediaPath
                onClicked:backend.command("importStudySubtitle",{})
            }
        }
        ListView {
            id:list; objectName:"subtitleList"
            Layout.fillWidth:true; Layout.fillHeight:true; clip:true; spacing:3
            model: root.filteredRows(root.cueIndex, root.query)
            cacheBuffer:500
            ScrollBar.vertical:AppScrollBar { id:scrollBar }
            delegate:Rectangle {
                id:cueRow
                required property var modelData
                required property int index
                readonly property bool active: modelData.id === root.activeId
                width:Math.max(0,list.width-scrollBar.width); height:Math.max(42,subtitleText.implicitHeight+20); radius:4
                color:active ? Theme.selection : subtitleMouse.containsMouse ? Theme.hover : Theme.transparent
                Rectangle { width:2; anchors.left:parent.left; anchors.top:parent.top; anchors.bottom:parent.bottom; anchors.margins:6; color:Theme.accent; visible:cueRow.active }
                RowLayout {
                    z:1
                    anchors.fill:parent; anchors.leftMargin:12; anchors.rightMargin:6; spacing:root.width<400 ? 7 : 14
                    Text { textFormat:Text.PlainText; text:S.time(cueRow.modelData.start*1000); color:cueRow.active ? Theme.accent : Theme.muted; font.family:Theme.mono; font.pixelSize:11; Layout.preferredWidth:root.width<400 ? implicitWidth : 58 }
                    Text { textFormat:Text.PlainText; id:subtitleText; Layout.fillWidth:true; text:(cueRow.modelData.speaker ? cueRow.modelData.speaker+"  " : "") + cueRow.modelData.text; color:Theme.text; font.family:Theme.font; font.pixelSize:14; wrapMode:Text.Wrap }
                    TButton { objectName:"correctSubtitleRow"; text:qsTranslate("Tingmu", "纠正"); quiet:true; visible:cueRow.active || rowHover.hovered; tip:qsTranslate("Tingmu", "纠正此句"); onClicked:backend.requestDialog("correction",root,{cueId:cueRow.modelData.id}) }
                }
                HoverHandler { id:rowHover }
                MouseArea { id:subtitleMouse; anchors.fill:parent; hoverEnabled:true; cursorShape:Qt.PointingHandCursor; onClicked:backend.command("seek",{position:cueRow.modelData.start*1000}); onDoubleClicked:backend.command("repeatCue",{}) }
            }
            ColumnLayout {
                anchors.centerIn: parent
                visible: list.count === 0
                width: Math.max(0, list.width - 12)
                Text {
                    objectName: "subtitleEmptyMessage"
                    Layout.fillWidth: true
                    wrapMode: Text.Wrap
                    horizontalAlignment: Text.AlignHCenter
                    text: root.query ? qsTranslate("Tingmu", "没有匹配的字幕") : qsTranslate("Tingmu", "字幕准备好后，会在这里跟随播放。")
                    color: Theme.muted
                    font.family: Theme.font
                    font.pixelSize: 12
                }
                TButton {
                    objectName: "clearSubtitleSearch"
                    Layout.alignment: Qt.AlignHCenter
                    visible: !!root.query
                    text: qsTranslate("Tingmu", "清除搜索")
                    quiet: true
                    onClicked: search.clear()
                }
            }
        }
    }
    SettingPopup {
        id:settingsPopup
        contentItem:ColumnLayout {
            spacing:10
            Text { textFormat:Text.PlainText; text:qsTranslate("Tingmu", "字幕时间轴"); color:Theme.text; font.family:Theme.font; font.pixelSize:16 }
            Text { textFormat:Text.PlainText; text:qsTranslate("Tingmu", "字幕偏移 · 毫秒"); color:Theme.muted; font.family:Theme.font; font.pixelSize:11 }
            Field { Layout.fillWidth:true; text:String(backend.settings.subtitle_offset_ms || 0); validator:IntValidator{bottom:-600000;top:600000} onEditingFinished:backend.setting("subtitle_offset_ms",Number(text)) }
            Text { textFormat:Text.PlainText; text:qsTranslate("Tingmu", "短句间隙衔接 · 毫秒"); color:Theme.muted; font.family:Theme.font; font.pixelSize:11 }
            Field { Layout.fillWidth:true; text:String(backend.settings.subtitle_gap_ms || 500); validator:IntValidator{bottom:0;top:1500} onEditingFinished:backend.setting("subtitle_gap_ms",Number(text)) }
            TButton { text:qsTranslate("Tingmu", "导出字幕"); onClicked:backend.command("exportSubtitle",{}) }
            TButton { text:qsTranslate("Tingmu", "导出")+qsTranslate("TingmuLanguage", backend.lessonModel.language || "")+qsTranslate("Tingmu", "译稿…");enabled:!!backend.mediaState.lessonCounts && backend.mediaState.lessonCounts.total>0 && (backend.mediaState.lessonCounts.verified + (backend.mediaState.lessonCounts.provided || 0))===backend.mediaState.lessonCounts.total;onClicked:backend.command("exportTranslation",{}) }
            Text { textFormat:Text.PlainText; Layout.fillWidth:true; text:qsTranslate("Tingmu", "显示衔接不会覆盖源字幕与原始声学时间。"); color:Theme.muted; font.family:Theme.font; font.pixelSize:11; wrapMode:Text.Wrap }
        }
    }
}
