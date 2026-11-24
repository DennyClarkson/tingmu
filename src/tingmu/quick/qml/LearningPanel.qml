import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S
Item {
    id:root
    objectName:"learningWorkspace"
    property var workspace
    property var book:backend.notebooks || ({})
    property string topic:""
    property string videoFilter:""
    property bool editing:false
    property bool dirty:false
    property string editRevision:""
    property string editLanguage:""
    property string activeTab:"collections"
    readonly property bool practiceMode:activeTab==="review"
    property string selectedNoteId:""
    property var selectedNote:(book.notes || []).find(function(note){return note.id===root.selectedNoteId}) || ({})
    property var collections:book.collections || book.entries || []
    readonly property var noteSources:book.note_sources || []
    property bool showAnswer:false
    property var cards:book.practice || []
    property var card:cards.length ? cards[0] : ({})
    property var filteredEntries:collections.filter(function(e){var selected=(book.topics || []).find(function(t){return t.title===root.topic});return (!root.topic || selected && selected.entry_ids.indexOf(e.id)>=0) && (!root.videoFilter || e.media_path===root.videoFilter)})
    property var videos:{var result=[{path:"",label:"所有视频"}];collections.concat(noteSources).forEach(function(e){if(!result.some(function(v){return v.path===e.media_path}))result.push({path:e.media_path,label:(e.media_path || "").split(/[\\/]/).pop()})});return result}
    property var drafts:({})
    property string pendingAction:""
    Component.onCompleted:{drafts=backend.loadDrafts("notebookDrafts")}
    Timer{id:draftTimer;interval:600;onTriggered:root.flushDrafts()}
    function removeDraft(language){
        if(!language)return true
        if(!backend.deleteDraft("notebookDrafts",language))return false
        var copy=Object.assign({},drafts);delete copy[language];drafts=copy
        return true
    }
    function cacheDraft(){
        if(!editing || !editLanguage)return true
        if(!dirty)return removeDraft(editLanguage)
        var value={text:editor.text,revision:editRevision}
        var copy=Object.assign({},drafts);copy[editLanguage]=value;drafts=copy
        return backend.writeDraft("notebookDrafts",editLanguage,value)
    }
    function flushDrafts(){draftTimer.stop();return cacheDraft()}
    function startEdit(){activeTab="notes";selectedNoteId="";var d=drafts[book.language];editor.text=d ? d.text : book.markdown || "";editRevision=d ? d.revision : book.revision || "";editLanguage=book.language || "";editing=true;dirty=editor.text!==(book.markdown || "")}
    function selectLanguage(language){cacheDraft();editing=false;dirty=false;topic="";videoFilter="";selectedNoteId="";backend.command("selectNotebook",{language:language})}
    function previewBlocks(){var blocks=root.selectedNoteId ? root.selectedNote.blocks || [] : root.book.blocks || [];if(!root.selectedNoteId || !blocks.length || blocks[0].kind!=="markdown")return blocks;var text=blocks[0].text || "";var heading=text.match(/^#{1,6} +([^\n]+)\n+/);if(!heading || heading[1].trim()!==root.selectedNote.title)return blocks;var copy=blocks.slice();copy[0]=Object.assign({},blocks[0],{text:text.slice(heading[0].length)});return copy}
    function selectNote(id){cacheDraft();editing=false;dirty=false;selectedNoteId=id;activeTab="notes"}
    function fileAction(name){if(dirty){pendingAction=name;fileDialog.open()}else backend.command(name,{})}
    onBookChanged:{
        if(editing && book.language!==editLanguage){cacheDraft();editing=false;dirty=false}
        if(editing && book.language===editLanguage && book.markdown===editor.text){dirty=false;editRevision=book.revision || "";removeDraft(editLanguage)}
        showAnswer=false
        if (practiceScroll && practiceScroll.contentItem) practiceScroll.contentItem.contentY = 0
    }
    RowLayout{
        anchors.top:parent.top;anchors.left:parent.left;anchors.right:parent.right;anchors.margins:20;height:36;spacing:6
        SegmentBar {
            TButton{objectName:"collectionsTab";text:qsTranslate("Tingmu", "我的收藏");checked:root.activeTab==="collections";quiet:true;onClicked:root.activeTab="collections"}
            TButton{objectName:"notesTab";text:qsTranslate("Tingmu", "笔记");checked:root.activeTab==="notes";quiet:true;onClicked:root.activeTab="notes"}
            TButton{objectName:"reviewTab";text:qsTranslate("Tingmu", "复习");checked:root.activeTab==="review";quiet:true;onClicked:{root.activeTab="review";backend.command("practiceNotebook",{})}}
        }
        Item{Layout.fillWidth:true}
        TButton{objectName:"learningTeachingPreferencesButton";text:qsTranslate("Tingmu", "我的讲解偏好");quiet:true;onClicked:backend.requestDialog("teaching",root,{})}
    }
    RowLayout {
        anchors.fill:parent;anchors.margins:20;anchors.topMargin:72;spacing:20
        ColumnLayout {
            id: sidebar
            objectName: "learningSidebar"
            readonly property int columnWidth: interfaceLanguage.code === "en" ? 200 : 170
            Layout.preferredWidth: columnWidth; Layout.maximumWidth: columnWidth; Layout.minimumWidth: columnWidth
            Layout.fillHeight: true; spacing: root.height < 540 ? 7 : 12
            Text{Layout.fillWidth:true;Layout.minimumWidth:0;wrapMode:Text.Wrap;text:qsTranslate("Tingmu", "学习资料");color:Theme.text;font.family:Theme.serif;font.pixelSize:26}
            Text{text:qsTranslate("Tingmu", "所学语言");color:Theme.muted;font.family:Theme.font;font.pixelSize:11}
            Choice{objectName:"notebookLanguageChoice";Layout.fillWidth:true;optionLabel:(i,value)=>interfaceLanguage.code === "zh-CN" || !model[i] ? value : qsTranslate("Tingmu", "%1学习笔记").arg(qsTranslate("TingmuLanguage", model[i].language));model:root.book.books || [];textRole:"title";currentIndex:Math.max(0,model.findIndex(function(b){return b.language===root.book.language}));onActivated:root.selectLanguage(model[currentIndex].language)}
            Choice{objectName:"notebookVideoFilter";Layout.fillWidth:true;optionLabel:(i,value)=>i===0 ? qsTranslate("Tingmu",value) : value;model:root.videos;textRole:"label";onActivated:root.videoFilter=model[currentIndex].path}
            Text{Layout.fillWidth:true;text:((root.book.stats || {}).total || 0)+qsTranslate("Tingmu", " 条收藏 · ")+((root.book.stats || {}).due || 0)+qsTranslate("Tingmu", " 条待复习");color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
            Rectangle{Layout.fillWidth:true;height:1;color:Theme.line}
            TButton{visible:root.activeTab==="collections";text:qsTranslate("Tingmu", "全部主题");checked:!root.topic;quiet:true;Layout.fillWidth:true;onClicked:{root.topic="";root.activeTab="collections"}}
            ListView {
                visible:root.activeTab==="collections";Layout.fillWidth:true;Layout.fillHeight:true;spacing:4;clip:true;model:root.book.topics || []
                ScrollBar.vertical: AppScrollBar {}
                delegate:TButton{required property var modelData;width:Math.max(0,ListView.view.width-10);text:modelData.title;checked:root.topic===modelData.title;quiet:true;onClicked:{root.topic=modelData.title;root.activeTab="collections"}}
            }
            TButton{visible:root.activeTab==="notes";text:qsTranslate("Tingmu", "整本笔记");checked:!root.selectedNoteId;quiet:true;Layout.fillWidth:true;onClicked:root.selectNote("")}
            ListView{objectName:"organizedNotesList";visible:root.activeTab==="notes";Layout.fillWidth:true;Layout.fillHeight:true;clip:true;spacing:6;model:root.book.notes || []
                ScrollBar.vertical:AppScrollBar {}
                delegate:TButton{required property var modelData;width:Math.max(0,ListView.view.width-10);text:modelData.title || qsTranslate("Tingmu", "学习笔记");checked:root.selectedNoteId===modelData.id;quiet:true;tip:text;onClicked:root.selectNote(modelData.id)}
            }
            Item{Layout.fillHeight:true;visible:root.practiceMode}
            TButton{objectName:"practiceNotebookButton";text:qsTranslate("Tingmu", "间隔复习");Layout.fillWidth:true;checked:root.practiceMode;onClicked:{root.activeTab="review";root.showAnswer=false;backend.command("practiceNotebook",{})}}
            TButton{text:qsTranslate("Tingmu", "打开笔记文件");quiet:true;Layout.fillWidth:true;onClicked:root.fileAction("openNotebook")}
            TButton{text:qsTranslate("Tingmu", "导出 Markdown");quiet:true;Layout.fillWidth:true;onClicked:root.fileAction("exportNotebook")}
        }
        Rectangle{objectName:"learningSidebarDivider";implicitWidth:1;Layout.fillHeight:true;color:Theme.line}
        CollectionInbox{Layout.fillWidth:true;Layout.fillHeight:true;visible:root.activeTab==="collections";entries:root.filteredEntries;canOrganize:!root.dirty;onOpenSource:function(url){backend.command("openLink",{url:url});if(root.workspace)root.workspace.changeView("watch")}}
        ColumnLayout {
            visible:root.activeTab!=="collections"
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.maximumWidth: Number.POSITIVE_INFINITY
            spacing: 14
            RowLayout {
                Layout.fillWidth:true
                Layout.maximumWidth: root.practiceMode ? 760 : Number.POSITIVE_INFINITY
                Layout.alignment: Qt.AlignHCenter
                Text{Layout.fillWidth:true;text:root.practiceMode ? qsTranslate("Tingmu", "把表达变成记忆") : root.selectedNote.title || qsTranslate("Tingmu", "学习笔记");color:Theme.text;font.family:Theme.serif;font.pixelSize:25;elide:Text.ElideRight}
                TButton{text:root.editing ? qsTranslate("Tingmu", "预览") : root.drafts[root.book.language] ? qsTranslate("Tingmu", "恢复草稿") : root.selectedNoteId ? qsTranslate("Tingmu", "编辑整本") : qsTranslate("Tingmu", "编辑");visible:!root.practiceMode;onClicked:{if(root.editing){if(root.dirty){discardDialog.open()}else root.editing=false}else root.startEdit()}}
                TButton{objectName:"saveNotebookButton";text:root.dirty ? qsTranslate("Tingmu", "保存修改") : qsTranslate("Tingmu", "已保存");visible:root.editing && !root.practiceMode;enabled:root.dirty && !backend.learningState.learningBusy;primary:true;onClicked:backend.command("saveNotebook",{language:root.editLanguage,markdown:editor.text,revision:root.editRevision})}
                TButton{text:qsTranslate("Tingmu", "从收藏整理");visible:!root.practiceMode;onClicked:root.activeTab="collections"}
            }
            Text{Layout.fillWidth:true;visible:root.editing && !root.practiceMode;text:qsTranslate("Tingmu", "Markdown 编辑 · 手写内容会保留；只有显式整理的收藏才会加入笔记。");color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
            Rectangle {
                id: studyCard
                objectName: "learningStudyCard"
                Layout.fillWidth: true; Layout.fillHeight: true
                Layout.maximumWidth: root.practiceMode ? 760 : Number.POSITIVE_INFINITY
                Layout.alignment: Qt.AlignHCenter
                color: Theme.panel; radius: Theme.radiusLg; border.color: Theme.line
                AppScrollView {
                    id:notebookScroll
                    objectName:"notebookScroll"
                    anchors.fill:parent;anchors.margins:24;clip:true;contentWidth:availableWidth;visible:!root.practiceMode
                    function revealCursor(){
                        if(!root.editing || !editor.activeFocus || !editor.visible)return
                        var view=contentItem
                        if(!view || view.contentY===undefined || !view.contentItem)return
                        var rect=editor.cursorRectangle
                        var point=editor.mapToItem(view.contentItem,rect.x,rect.y)
                        var top=point.y, bottom=top+rect.height
                        if(top<view.contentY+12)view.contentY=Math.max(view.originY,top-12)
                        else if(bottom>view.contentY+view.height-12)view.contentY=Math.max(view.originY,bottom-view.height+12)
                        view.returnToBounds()
                    }
                    Timer{id:cursorFollowTimer;interval:0;onTriggered:notebookScroll.revealCursor()}
                    ColumnLayout {
                        width:notebookScroll.availableWidth;spacing:18
                        MarkdownView{objectName:"notebookPreview";visible:!root.editing;Layout.fillWidth:true;blocks:root.previewBlocks();textSize:15;onSourceOpened:if(root.workspace)root.workspace.changeView("watch")}
                        TextArea{id:editor;objectName:"notebookEditor";visible:root.editing;Layout.fillWidth:true;Layout.minimumHeight:notebookScroll.availableHeight;textFormat:TextEdit.PlainText;color:Theme.text;font.family:Theme.mono;font.pixelSize:13;wrapMode:TextArea.Wrap;selectByMouse:true;background:null
                            onCursorRectangleChanged:if(root.editing && activeFocus)cursorFollowTimer.restart()
                            onTextChanged:if(root.editing){root.dirty=text!==(root.book.markdown || "");draftTimer.restart();if(activeFocus)cursorFollowTimer.restart()}
                        }
                        Text{visible:!root.editing && !root.book.markdown;Layout.fillWidth:true;text:qsTranslate("Tingmu", "把收藏变成理解\n\n在“收藏”中选好词句并明确整理后，会生成包含用法、例句与语言对比的笔记。你也可以直接开始写。");color:Theme.muted;font.family:Theme.font;font.pixelSize:15;wrapMode:Text.Wrap;lineHeight:1.6}
                    }
                }
                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: studyCard.height < 360 ? 16 : 24
                    spacing: studyCard.height < 360 ? 8 : 12
                    visible: root.practiceMode
                    StatusBadge {
                        tone: "accent"
                        text: root.cards.length ? qsTranslate("Tingmu", "待复习 · ") + root.cards.length : qsTranslate("Tingmu", "本轮复习已完成")
                    }
                    AppScrollView {
                        id: practiceScroll
                        objectName: "reviewContentScroll"
                        Layout.fillWidth: true; Layout.fillHeight: true
                        clip: true
                        contentWidth: availableWidth
                        rightPadding: 10
                        ColumnLayout {
                            width: practiceScroll.availableWidth
                            spacing: 14
                            Text {
                                Layout.fillWidth: true
                                text: root.card.question || qsTranslate("Tingmu", "收藏新表达后，它们会出现在这里。")
                                color: Theme.text; font.family: Theme.serif; font.pixelSize: 24
                                textFormat: Text.PlainText; wrapMode: Text.Wrap
                            }
                            Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: answer.implicitHeight + 24
                                color: Theme.raised; radius: Theme.radiusMd
                                visible: root.showAnswer && root.cards.length > 0
                                TextEdit {
                                    id: answer
                                    objectName: "reviewAnswer"
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                    anchors.margins: 12
                                    readOnly: true; selectByMouse: true
                                    text: root.card.answer || ""; textFormat: TextEdit.PlainText; wrapMode: TextEdit.Wrap
                                    font.family: Theme.font; font.pixelSize: 15; color: Theme.text
                                    selectionColor: Theme.selection; selectedTextColor: Theme.text
                                }
                            }
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: root.cards.length > 0
                        TButton {
                            objectName: "revealReviewAnswer"
                            text: root.showAnswer ? qsTranslate("Tingmu", "收起答案") : qsTranslate("Tingmu", "显示答案")
                            primary: !root.showAnswer
                            onClicked: root.showAnswer = !root.showAnswer
                        }
                        Item { Layout.fillWidth: true }
                        TButton {
                            text: qsTranslate("Tingmu", "回到视频原句"); quiet: true
                            onClicked: {
                                backend.command("openLink", {url: root.card.source_url || "tingmu://entry/" + root.card.id})
                                if (root.workspace) root.workspace.changeView("watch")
                            }
                        }
                    }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 8
                        visible: root.showAnswer && root.cards.length > 0
                        Repeater {
                            model: [{rating: "again", label: qsTranslate("Tingmu", "再学一次")}, {rating: "hard", label: qsTranslate("Tingmu", "有点难")},
                                    {rating: "good", label: qsTranslate("Tingmu", "记住了")}, {rating: "easy", label: qsTranslate("Tingmu", "很熟悉")}]
                            delegate: TButton {
                                required property var modelData
                                objectName: "reviewRating_" + modelData.rating
                                text: modelData.label
                                implicitHeight: studyCard.height < 360 ? Theme.compactHeight : Theme.controlHeight
                                onClicked: {
                                    backend.command("reviewEntry", {id: root.card.entry_id || root.card.id, rating: modelData.rating})
                                    root.showAnswer = false
                                }
                            }
                        }
                    }
                }
            }
            Text{Layout.fillWidth:true;visible:!!backend.learningState.error || !!backend.learningState.learningBusy;text:qsTranslate("TingmuStatus", backend.learningState.error || "") || qsTranslate("Tingmu", "正在整理现有收藏，完成后会更新笔记…");color:backend.learningState.error ? Theme.error : Theme.muted;font.family:Theme.font;font.pixelSize:12;wrapMode:Text.Wrap}
        }
        ColumnLayout {
            visible:root.activeTab==="notes" && root.width>=1050;Layout.preferredWidth:root.width<1200 ? 230 : 290;Layout.maximumWidth:root.width<1200 ? 230 : 290;Layout.minimumWidth:210;Layout.fillHeight:true;spacing:12
            Text{text:root.selectedNoteId ? qsTranslate("Tingmu", "本条笔记来源") : qsTranslate("Tingmu", "笔记来源");color:Theme.text;font.family:Theme.serif;font.pixelSize:22}
            ListView {
                id:sources;objectName:"notebookSources"
                Layout.fillWidth:true;Layout.fillHeight:true;clip:true;spacing:10
                model: {
                    const ids = root.selectedNoteId ? new Set(root.selectedNote.entry_ids || []) : null
                    return root.noteSources.filter(e => (!root.videoFilter || e.media_path === root.videoFilter)
                        && (!ids || ids.has(e.id)))
                }
                ScrollBar.vertical:AppScrollBar {}
                delegate:Rectangle {
                    required property var modelData
                    width:Math.max(0,sources.width-10);height:sourceBody.implicitHeight+24;radius:8;color:Theme.panel;border.color:Theme.line
                    ColumnLayout{id:sourceBody;anchors.left:parent.left;anchors.right:parent.right;anchors.top:parent.top;anchors.margins:12;spacing:8
                        Text{Layout.fillWidth:true;text:modelData.text || modelData.sentence || qsTranslate("Tingmu", "笔记");textFormat:Text.PlainText;color:Theme.text;font.family:Theme.font;font.pixelSize:15;wrapMode:Text.Wrap}
                        Text{Layout.fillWidth:true;text:modelData.reading || "";visible:text.length>0;textFormat:Text.PlainText;color:Theme.accent;font.family:Theme.latin;font.pixelSize:12;wrapMode:Text.Wrap}
                        Text{Layout.fillWidth:true;text:modelData.note || modelData.meaning || "";visible:text.length>0;textFormat:Text.PlainText;color:Theme.muted;font.family:Theme.font;font.pixelSize:12;wrapMode:Text.Wrap;maximumLineCount:4;elide:Text.ElideRight}
                        Text{Layout.fillWidth:true;text:modelData.sentence || "";visible:!!text && text!==modelData.text;textFormat:Text.PlainText;color:Theme.text;font.family:Theme.font;font.pixelSize:13;wrapMode:Text.Wrap}
                        Text{Layout.fillWidth:true;text:(modelData.media_path || "").split(/[\\/]/).pop();textFormat:Text.PlainText;color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.WrapAnywhere}
                        TButton{objectName:"openNoteSource"+modelData.id;text:qsTranslate("Tingmu", "回到原句 · ")+S.time(modelData.start*1000);quiet:true;onClicked:{backend.command("openLink",{url:"tingmu://entry/"+modelData.id});if(root.workspace)root.workspace.changeView("watch")}}
                    }
                }
                Text{anchors.centerIn:parent;width:parent.width-20;visible:sources.count===0;text:qsTranslate("Tingmu", "这个筛选下还没有收藏。\n观看时选词或收藏整句即可。");color:Theme.muted;font.family:Theme.font;font.pixelSize:12;wrapMode:Text.Wrap;lineHeight:1.5}
            }
            TButton{text:qsTranslate("Tingmu", "用这些收藏练习对话");Layout.fillWidth:true;enabled:root.collections.length>0;onClicked:{backend.command("askNotebook",{text:qsTranslate("Tingmu", "请结合我的收藏，给我一道适合当前水平的练习。")});if(root.workspace)root.workspace.openModule("chat")}}
        }
    }
    AppDialog{width:380;id:fileDialog;objectName:"savedNotebookDialog";title:qsTranslate("Tingmu", "当前有未保存的修改");anchors.centerIn:parent;modal:true;standardButtons:Dialog.Cancel
        contentItem:ColumnLayout{spacing:14;Text{Layout.fillWidth:true;text:qsTranslate("Tingmu", "打开或导出的文件只包含已保存内容。请先保存修改，或继续使用上次保存的版本。");color:Theme.text;font.family:Theme.font;wrapMode:Text.Wrap}TButton{text:qsTranslate("Tingmu", "继续使用已保存版本");onClicked:{fileDialog.close();backend.command(root.pendingAction,{})}}}
    }
    AppDialog{width:380;id:discardDialog;objectName:"discardNotebookDialog";title:qsTranslate("Tingmu", "保留当前草稿？");anchors.centerIn:parent;modal:true;standardButtons:Dialog.Discard | Dialog.Cancel;contentItem:Text{wrapMode:Text.Wrap;text:qsTranslate("Tingmu", "当前修改尚未保存。取消返回编辑，或放弃修改继续。");color:Theme.text;font.family:Theme.font}onDiscarded:{root.dirty=false;root.editing=false;root.removeDraft(root.editLanguage)}}
}
