import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S
ScrollView {
    id: root
    property var cue: backend.activeCue
    property int textSize: 23
    property bool showReading: true
    property bool showTranslation: true
    property bool showExplanation: true
    property bool compact: false
    property bool interactive:!compact || !backend.playback.playing
    readonly property bool mediaOpened:!!backend.state.mediaPath
    readonly property bool preparing:mediaOpened && backend.jobs.some(function(job){return (job.path===backend.state.mediaPath || job.media_path===backend.state.mediaPath) && job.status==="running"})
    readonly property string emptyTitle:!mediaOpened ? "开始观看" : preparing ? "正在准备学习材料" : backend.cues.length===0 ? "还没有学习字幕" : "当前是字幕空档"
    readonly property string emptyMessage:!mediaOpened ? "打开一个视频，开始观看与学习。" : preparing ? "正在处理这个视频，完成后会显示逐句内容。" : backend.cues.length===0 ? "视频已打开。可导入字幕，或在准备工作区生成学习材料。" : "这里没有当前字幕；播放到下一句时会自动显示。"
    signal learningRequested()
    clip: true
    contentWidth: availableWidth
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ColumnLayout {
        width: root.availableWidth
        spacing: root.compact ? 10 : 16
        Item { Layout.preferredHeight: 1 }
        Text { textFormat:Text.PlainText;
            Layout.fillWidth: true
            text: root.cue.text ? S.time(root.cue.start*1000) + "  /  当前句" : root.emptyTitle
            color: Theme.accent; font.family: Theme.font; font.pixelSize: 11
        }
        TextEdit {
            id:sourceText;objectName:"selectableCueText"
            Accessible.name:"当前字幕原文"
            Accessible.description:"用鼠标选择原文，再点击收藏选中文字；也可直接收藏整句。"
            Layout.fillWidth:true
            text:root.cue.text || root.emptyMessage
            textFormat:TextEdit.PlainText;readOnly:true;selectByMouse:root.interactive;persistentSelection:true
            color:Theme.text;font.family:Theme.font;font.pixelSize:root.textSize
            wrapMode:TextEdit.Wrap;selectionColor:Theme.selection;selectedTextColor:Theme.text
        }
        Flow {
            Layout.fillWidth:true;spacing:6;visible:root.interactive && !!root.cue.id
            TButton{objectName:"saveSelectionButton";focusPolicy:Qt.TabFocus;text:"收藏选中文字";enabled:sourceText.selectedText.length>0;quiet:true;implicitHeight:28;onClicked:backend.command("saveSelection",{cueId:root.cue.id,text:sourceText.selectedText})}
            TButton{objectName:"saveSentenceButton";text:"收藏整句";quiet:true;implicitHeight:28;onClicked:backend.command("saveNote",{cueId:root.cue.id,note:""})}
            TButton{objectName:"openLearningButton";text:"进入学习";quiet:true;implicitHeight:28;onClicked:root.learningRequested()}
        }
        Text { textFormat:Text.PlainText;
            Layout.fillWidth: true
            visible: root.showReading && !!root.cue.reading
            text: root.cue.reading || ""
            color: Theme.accent; font.family: Theme.font; font.pixelSize: Math.max(12,root.textSize-7); wrapMode: Text.Wrap
        }
        Flow {
            Layout.fillWidth: true; spacing: 6
            visible: root.interactive && !!root.cue.tokens && root.cue.tokens.length > 0
            Repeater {
                model: root.cue.tokens || []
                delegate: Rectangle {
                    id: tokenCard
                    required property var modelData
                    required property int index
                    width: Math.min(root.availableWidth,Math.max(48,wordMeasure.advanceWidth+18,root.showReading ? readingMeasure.advanceWidth+18 : 0))
                    height: tokenColumn.implicitHeight + 14
                    radius: 5; color: tokenMouse.containsMouse ? Theme.hover : Theme.raised
                    TextMetrics{id:wordMeasure;text:tokenCard.modelData.text || "";font.family:Theme.font;font.pixelSize:16}
                    TextMetrics{id:readingMeasure;text:tokenCard.modelData.reading || "";font.family:Theme.font;font.pixelSize:10}
                    Column {
                        id: tokenColumn; anchors.centerIn: parent; spacing: 3;width:parent.width-18
                        Text { textFormat:Text.PlainText; width:parent.width;horizontalAlignment:Text.AlignHCenter;wrapMode:Text.WrapAnywhere; text: tokenCard.modelData.text || ""; color: Theme.text; font.family: Theme.font; font.pixelSize: 16 }
                        Text { textFormat:Text.PlainText; visible: root.showReading && text.length>0; width:parent.width;horizontalAlignment:Text.AlignHCenter;wrapMode:Text.WrapAnywhere; text: tokenCard.modelData.reading || ""; color: Theme.accent; font.family: Theme.font; font.pixelSize: 10 }
                    }
                    MouseArea { id: tokenMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: backend.command("saveWord", {cueId:root.cue.id,tokenIndex:tokenCard.index}) }
                    ToolTip.visible: tokenMouse.containsMouse
                    ToolTip.delay: 600
                    ToolTip.text: (modelData.meaning || "") + "\n点击收藏词语"
                }
            }
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line; visible: root.showTranslation || root.showExplanation }
        Text { textFormat:Text.PlainText; visible: root.showTranslation && !!root.cue.translation; text: "释义"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 11 }
        Text { textFormat:Text.PlainText; Layout.fillWidth: true; visible: root.showTranslation && !!root.cue.translation; text: root.cue.translation || ""; color: Theme.text; font.family: Theme.font; font.pixelSize: root.compact ? Math.max(12, root.textSize-5) : 14; wrapMode: Text.Wrap; lineHeight: 1.25 }
        Text { textFormat:Text.PlainText; visible: root.showExplanation && !!root.cue.explanation; text: "句中笔记"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 11 }
        Text { textFormat:Text.PlainText; Layout.fillWidth: true; visible: root.showExplanation && !!root.cue.explanation; text: root.cue.explanation || ""; color: Theme.text; font.family: Theme.font; font.pixelSize: root.compact ? Math.max(12,root.textSize-6) : 13; wrapMode: Text.Wrap; lineHeight: 1.35 }
        Text { textFormat:Text.PlainText; visible: !!root.cue.uncertain && !root.compact; Layout.fillWidth: true; text: "文本复核仍有疑点，可对照原声确认。"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 10; wrapMode: Text.Wrap }
        Item { Layout.preferredHeight: 8 }
    }
}
