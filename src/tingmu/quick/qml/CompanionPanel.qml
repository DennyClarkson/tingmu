import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S
Item {
    id: root
    property var workspace
    property var drafts: ({})
    property string draftKey: ""
    function changeCue() {
        draftKey = backend.state.mediaPath + "|" + backend.settings.source_language + "|" + backend.settings.target_language + "|" + (backend.activeCue.id || "")
        var existing=backend.entries.find(function(entry){return entry.kind==="note" && entry.cue_id===backend.activeCue.id && entry.media_path===backend.state.mediaPath && entry.source_language===backend.settings.source_language && entry.target_language===backend.settings.target_language})
        note.text = drafts[draftKey] !== undefined ? drafts[draftKey] : existing ? existing.note || "" : ""
    }
    Component.onCompleted: { drafts=workspace ? workspace.preference("noteDrafts") || ({}) : ({});changeCue() }
    Connections { target:backend;function onActiveCueChanged(){root.changeCue()} }
    TeachingPreferences{id:teachingPrefs}
    property int page: 0
    property bool showReading: true
    property bool showTranslation: true
    property bool showExplanation: true
    function openSettings() { settingsPopup.open() }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 16; spacing: 12
        RowLayout {
            Layout.fillWidth: true
            TButton { text: "当前句"; checked: root.page===0; quiet: true; onClicked: root.page=0 }
            TButton { text: "收藏  " + backend.entries.length; checked: root.page===1; quiet: true; onClicked: root.page=1 }
            Item { Layout.fillWidth: true }
            TButton { text:"来源";quiet:true;enabled:!!backend.activeCue.id;onClicked:sourceDetails.open() }
            TButton { iconSource: "repeat"; tip: "重听此句"; quiet: true; enabled: !!backend.activeCue.id; onClicked: backend.command("repeatCue",{}) }
        }
        CompanionContent { onLearningRequested:if(root.workspace)root.workspace.changeView("learn"); Layout.fillWidth: true; Layout.fillHeight: true; visible: root.page===0; showReading:root.showReading; showTranslation:root.showTranslation; showExplanation:root.showExplanation }
        ListView {
            Layout.fillWidth: true; Layout.fillHeight: true; visible: root.page===1
            clip: true; spacing: 8; model: backend.entries
            ScrollBar.vertical: ScrollBar {}
            delegate: Rectangle {
                required property var modelData
                width: ListView.view.width; height: entryColumn.implicitHeight+20
                color: Theme.raised; radius: 5
                ColumnLayout {
                    id: entryColumn; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 10; spacing: 5
                    RowLayout {
                        Layout.fillWidth: true
                        Text { textFormat:Text.PlainText; Layout.fillWidth: true; text: modelData.text || modelData.sentence || "笔记"; color: Theme.text; font.family: Theme.font; font.pixelSize: 15; wrapMode: Text.Wrap }
                        TButton { iconSource: "close"; quiet: true; tip: "删除收藏"; onClicked: backend.command("deleteEntry",{id:modelData.id}) }
                    }
                    Text { textFormat:Text.PlainText; Layout.fillWidth: true; text: modelData.reading || ""; visible: text.length>0; color: Theme.accent; font.family: Theme.font; font.pixelSize: 11; wrapMode: Text.Wrap }
                    Text { textFormat:Text.PlainText; Layout.fillWidth: true; text: modelData.note || modelData.meaning || ""; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; wrapMode: Text.Wrap }
                    TButton { text: S.time((modelData.start || 0)*1000) + "  定位原句"; quiet: true; onClicked: backend.command("openEntry",{id:modelData.id}) }
                }
            }
            Text { textFormat:Text.PlainText; anchors.centerIn: parent; visible: parent.count===0; text: "点击句中的词语，留下第一条收藏。"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; width: parent.width-20; wrapMode: Text.Wrap; horizontalAlignment: Text.AlignHCenter }
        }
        RowLayout {
            Layout.fillWidth: true; visible: root.page===0
            Field { id: note; Layout.fillWidth: true; placeholderText: "记下这一句…"; enabled: !!backend.activeCue.id; onAccepted: saveNote.clicked();onTextEdited:{var copy=Object.assign({},root.drafts);copy[root.draftKey]=text;root.drafts=copy;if(root.workspace)root.workspace.savePreference("noteDrafts",copy)} }
            TButton { id: saveNote; text: "收藏"; enabled: !!backend.activeCue.id; onClicked: { backend.command("saveNote",{cueId:backend.activeCue.id,note:note.text}); note.clear();var copy=Object.assign({},root.drafts);delete copy[root.draftKey];root.drafts=copy;if(root.workspace)root.workspace.savePreference("noteDrafts",copy) } }
        }
        RowLayout { visible: root.page===1; TButton { text: "导出笔记"; onClicked: backend.command("exportNotes",{}) } TButton { text: "学习复盘"; enabled: backend.entries.length>0; onClicked: backend.command("summarizeNotes",{}) } }
    }
    SettingPopup {
        id: settingsPopup
        contentItem: ColumnLayout {
            spacing: 10
            Text { textFormat:Text.PlainText; text: "学习伴侣"; color: Theme.text; font.family: Theme.font; font.pixelSize: 16 }
            TButton{text:"我的讲解偏好";Layout.fillWidth:true;onClicked:teachingPrefs.open()}
            Toggle { text: "显示读音"; checked: root.showReading; onToggled: root.showReading=checked }
            Toggle { text: "显示释义"; checked: root.showTranslation; onToggled: root.showTranslation=checked }
            Toggle { text: "显示讲解"; checked: root.showExplanation; onToggled: root.showExplanation=checked }
            Text { textFormat:Text.PlainText; text: "已知语言 / 学习语言"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 11 }
            RowLayout {
                Choice { Layout.fillWidth: true; model: ["普通话","粤语","英语","日语","韩语"]; property var codes:["zh-CN","yue","en","ja","ko"]; currentIndex: codes.indexOf(backend.settings.source_language);displayText:currentIndex<0 ? backend.settings.source_language : currentText; onActivated: backend.setting("source_language",codes[currentIndex]) }
                Choice { Layout.fillWidth: true; model: ["粤语","普通话","英语","日语","韩语"]; property var codes:["yue","zh-CN","en","ja","ko"]; currentIndex: codes.indexOf(backend.settings.target_language);displayText:currentIndex<0 ? backend.settings.target_language : currentText; onActivated: backend.setting("target_language",codes[currentIndex]) }
            }
            Text { textFormat:Text.PlainText; text:"或填写语言代码（如 fr、de、es）";color:Theme.muted;font.family:Theme.font;font.pixelSize:10 }
            RowLayout {
                Field { Layout.fillWidth:true;Layout.preferredWidth:100;text:backend.settings.source_language || "";placeholderText:"已知语言";onEditingFinished:if(text.trim())backend.setting("source_language",text.trim()) }
                Field { Layout.fillWidth:true;Layout.preferredWidth:100;text:backend.settings.target_language || "";placeholderText:"学习语言";onEditingFinished:if(text.trim())backend.setting("target_language",text.trim()) }
            }
            Text { textFormat:Text.PlainText; text: "学习程度"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 11 }
            Choice { Layout.fillWidth: true; model:["入门","中级","进阶"]; property var codes:["beginner","intermediate","advanced"]; currentIndex: Math.max(0,codes.indexOf(backend.settings.level)); onActivated: backend.setting("level",codes[currentIndex]) }
        }
    }
    AppDialog {
        id:sourceDetails;title:"字幕来源";modal:false
        parent:Overlay.overlay
        anchors.centerIn:parent
        width:Math.min(480,Overlay.overlay ? Overlay.overlay.width-40 : 480)
        height:Math.min(480,Overlay.overlay ? Overlay.overlay.height-60 : 480)
        standardButtons:Dialog.Close
        background:Rectangle{color:Theme.panel;radius:8;border.color:Theme.line}
        contentItem:ScrollView {
            id:sourceScroll
            clip:true;contentWidth:availableWidth
            ColumnLayout {
                width:sourceScroll.availableWidth;spacing:12
                Text { textFormat:Text.PlainText;Layout.fillWidth:true;text:"原始识别 / 导入文本";color:Theme.accent;font.family:Theme.font;font.pixelSize:11}
                TextEdit{Layout.fillWidth:true;readOnly:true;selectByMouse:true;wrapMode:TextEdit.Wrap;textFormat:TextEdit.PlainText;text:backend.activeCue.original_text || backend.activeCue.text || "";color:Theme.text;font.family:Theme.font;font.pixelSize:15}
                Text { textFormat:Text.PlainText;Layout.fillWidth:true;text:"源时间  " + S.time((backend.activeCue.sourceStart===undefined ? backend.activeCue.start || 0 : backend.activeCue.sourceStart)*1000) + " — " + S.time((backend.activeCue.sourceEnd===undefined ? backend.activeCue.end || 0 : backend.activeCue.sourceEnd)*1000);color:Theme.muted;font.family:Theme.font;font.pixelSize:11}
                Text { textFormat:Text.PlainText;Layout.fillWidth:true;visible:backend.activeCue.timingBasis==="text_proportional";text:"分句展示  " + S.time((backend.activeCue.start || 0)*1000) + " — " + S.time((backend.activeCue.end || 0)*1000);color:Theme.muted;font.family:Theme.font;font.pixelSize:11}
                Text { textFormat:Text.PlainText;Layout.fillWidth:true;text:backend.activeCue.reviewReason || (backend.activeCue.uncertain ? "文本复核仍有疑点，请对照原声确认。" : "显示文本可能经过繁体规范与文本模型复核。文本复核不是听音或人工确认。");color:Theme.muted;font.family:Theme.font;font.pixelSize:12;wrapMode:Text.Wrap;lineHeight:1.4}
                Text { textFormat:Text.PlainText;Layout.fillWidth:true;text:backend.activeCue.timingBasis==="text_proportional" ? "此句由原长句按文本比例分句展示；细分时间不是重新听音对齐。" : "短间隙衔接与分句展示不覆盖源字幕及声学时间。";color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
            }
        }
    }
}
