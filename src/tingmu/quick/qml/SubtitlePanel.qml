import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S
Item {
    id:root
    property bool follow:true
    SubtitleCorrection { id:correction }
    function openSettings(){settingsPopup.open()}
    Connections { target:backend; function onActiveIndexChanged(){ if(root.follow && !search.text && backend.activeIndex>=0) list.positionViewAtIndex(backend.activeIndex,ListView.Center) } }
    ColumnLayout {
        anchors.fill:parent; anchors.margins:12; spacing:10
        RowLayout {
            Layout.fillWidth:true
            Field { id:search; Layout.fillWidth:true; placeholderText:"搜索字幕 · " + backend.cues.length + " 句" }
            TButton { text:"跟随"; checked:root.follow; quiet:true; onClicked:root.follow=!root.follow }
            TButton { text:backend.cues.length ? "替换文档…" : "从字幕建文档"; tip:"使用字幕建立或替换此视频的学习文档"; quiet:true; enabled:!!backend.state.mediaPath; onClicked:backend.command("importStudySubtitle",{}) }
        }
        ListView {
            id:list; objectName:"subtitleList"
            Layout.fillWidth:true; Layout.fillHeight:true; clip:true; spacing:3
            model:backend.cues; cacheBuffer:500
            ScrollBar.vertical:ScrollBar{}
            delegate:Rectangle {
                id:cueRow
                required property var modelData
                required property int index
                property bool matches:!search.text || modelData.text.indexOf(search.text)>=0 || (modelData.translation || "").indexOf(search.text)>=0
                width:list.width; height:matches ? Math.max(42,subtitleText.implicitHeight+20) : 0; visible:matches; radius:4
                color:index===backend.activeIndex ? Theme.selection : subtitleMouse.containsMouse ? Theme.hover : "transparent"
                Rectangle { width:2; anchors.left:parent.left; anchors.top:parent.top; anchors.bottom:parent.bottom; anchors.margins:6; color:Theme.accent; visible:cueRow.index===backend.activeIndex }
                RowLayout {
                    z:1
                    anchors.fill:parent; anchors.leftMargin:12; anchors.rightMargin:10; spacing:14
                    Text { textFormat:Text.PlainText; text:S.time(cueRow.modelData.start*1000); color:cueRow.index===backend.activeIndex ? Theme.accent : Theme.muted; font.family:Theme.mono; font.pixelSize:11; Layout.preferredWidth:58 }
                    Text { textFormat:Text.PlainText; id:subtitleText; Layout.fillWidth:true; text:(cueRow.modelData.speaker ? cueRow.modelData.speaker+"  " : "") + cueRow.modelData.text; color:Theme.text; font.family:Theme.font; font.pixelSize:14; wrapMode:Text.Wrap }
                    TButton { objectName:"correctSubtitleRow"; text:"纠正"; quiet:true; visible:cueRow.index===backend.activeIndex || rowHover.hovered; tip:"纠正此句"; onClicked:correction.begin(cueRow.modelData.id) }
                }
                HoverHandler { id:rowHover }
                MouseArea { id:subtitleMouse; anchors.fill:parent; hoverEnabled:true; cursorShape:Qt.PointingHandCursor; onClicked:backend.command("seek",{position:cueRow.modelData.start*1000}); onDoubleClicked:backend.command("repeatCue",{}) }
            }
            Text { textFormat:Text.PlainText; anchors.centerIn:parent; visible:list.count===0; text:"字幕准备好后，会在这里跟随播放。"; color:Theme.muted; font.family:Theme.font; font.pixelSize:12 }
        }
    }
    SettingPopup {
        id:settingsPopup
        contentItem:ColumnLayout {
            spacing:10
            Text { textFormat:Text.PlainText; text:"字幕时间轴"; color:Theme.text; font.family:Theme.font; font.pixelSize:16 }
            Text { textFormat:Text.PlainText; text:"字幕偏移 · 毫秒"; color:Theme.muted; font.family:Theme.font; font.pixelSize:11 }
            Field { Layout.fillWidth:true; text:String(backend.settings.subtitle_offset_ms || 0); validator:IntValidator{bottom:-600000;top:600000} onEditingFinished:backend.setting("subtitle_offset_ms",Number(text)) }
            Text { textFormat:Text.PlainText; text:"短句间隙衔接 · 毫秒"; color:Theme.muted; font.family:Theme.font; font.pixelSize:11 }
            Field { Layout.fillWidth:true; text:String(backend.settings.subtitle_gap_ms || 500); validator:IntValidator{bottom:0;top:1500} onEditingFinished:backend.setting("subtitle_gap_ms",Number(text)) }
            TButton { text:"导出字幕"; onClicked:backend.command("exportSubtitle",{}) }
            TButton { text:"导出"+backend.lessonModel.language+"译稿…";enabled:!!backend.state.lessonCounts && backend.state.lessonCounts.total>0 && backend.state.lessonCounts.verified===backend.state.lessonCounts.total;onClicked:backend.command("exportTranslation",{}) }
            Text { textFormat:Text.PlainText; Layout.fillWidth:true; text:"显示衔接不会覆盖源字幕与原始声学时间。"; color:Theme.muted; font.family:Theme.font; font.pixelSize:11; wrapMode:Text.Wrap }
        }
    }
}
