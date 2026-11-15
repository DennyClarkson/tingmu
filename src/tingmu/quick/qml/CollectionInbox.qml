import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
Item {
    id:root
    objectName:"collectionInbox"
    property var entries:[]
    property bool canOrganize:true
    property var selectedIds:[]
    property var editingEntry:({})
    property var visibleEntries:entries.filter(function(e){return (statusFilter.currentIndex===0 || (statusFilter.currentIndex===1 ? !e.organized : !!e.organized)) && (!search.text || [e.text,e.sentence,e.meaning,e.entry_note].join(" ").toLowerCase().indexOf(search.text.toLowerCase())>=0)})
    property int unorganizedCount:entries.filter(function(e){return !e.organized}).length
    signal openSource(string url)
    function select(id,on){var copy=selectedIds.slice();var at=copy.indexOf(id);if(on && at<0)copy.push(id);else if(!on && at>=0)copy.splice(at,1);selectedIds=copy}
    function editNote(entry){editingEntry=entry;entryNote.text=entry.entry_note || "";noteDialog.open()}
    function organize(){var payload={instructions:instructions.text.trim()};if(selectedIds.length)payload.ids=selectedIds;backend.command("organizeNotebook",payload)}
    onEntriesChanged:selectedIds=selectedIds.filter(function(id){return entries.some(function(e){return e.id===id})})
    Connections{target:backend;function onNotebooksChanged(){if(noteDialog.opened){var updated=(backend.notebooks.collections || []).find(function(e){return e.id===root.editingEntry.id});if(updated && updated.entry_note===entryNote.text && updated.note_revision!==root.editingEntry.note_revision)noteDialog.close()}}}
    ColumnLayout {
        anchors.fill:parent;spacing:12
        Text{text:"收藏收集箱";color:Theme.text;font.family:Theme.serif;font.pixelSize:25}
        Text{Layout.fillWidth:true;text:"观看时收藏的词句先保存在这里。选好材料后，再明确整理成学习笔记；整理后原收藏仍会保留。";color:Theme.muted;font.family:Theme.font;font.pixelSize:12;wrapMode:Text.Wrap}
        RowLayout{
            Layout.fillWidth:true
            Choice{id:statusFilter;objectName:"collectionStatusFilter";Layout.preferredWidth:150;model:["全部收藏","未整理","已整理"]}
            Field{id:search;Layout.fillWidth:true;placeholderText:"查找词句或备注"}
            TButton{text:root.selectedIds.length ? "取消选择" : "全选";quiet:true;onClicked:root.selectedIds=root.selectedIds.length ? [] : root.visibleEntries.map(function(e){return e.id})}
        }
        ListView{
            id:inbox;objectName:"collectionList"
            Layout.fillWidth:true;Layout.fillHeight:true;model:root.visibleEntries;clip:true;spacing:10
            ScrollBar.vertical:ScrollBar{}
            delegate:Rectangle{
                id:card
                required property var modelData
                width:inbox.width;height:body.implicitHeight+28;radius:8;color:Theme.panel;border.color:Theme.line
                ColumnLayout{
                    id:body;anchors.left:parent.left;anchors.right:parent.right;anchors.top:parent.top;anchors.margins:14;spacing:8
                    RowLayout{
                        Layout.fillWidth:true
                        Toggle{checked:root.selectedIds.indexOf(card.modelData.id)>=0;onToggled:root.select(card.modelData.id,checked)}
                        Text{Layout.fillWidth:true;text:card.modelData.text || card.modelData.sentence || "收藏句子";textFormat:Text.PlainText;color:Theme.text;font.family:Theme.font;font.pixelSize:18;wrapMode:Text.Wrap}
                        Text{objectName:"collectionOrganizedBadge";text:card.modelData.organized ? "已整理" : "未整理";color:card.modelData.organized ? Theme.accent : Theme.muted;font.family:Theme.font;font.pixelSize:11}
                    }
                    Text{Layout.fillWidth:true;visible:!!card.modelData.reading;text:card.modelData.reading || "";textFormat:Text.PlainText;color:Theme.accent;font.family:Theme.latin;font.pixelSize:14;wrapMode:Text.Wrap}
                    Text{Layout.fillWidth:true;visible:!!(card.modelData.meaning || card.modelData.note);text:card.modelData.meaning || card.modelData.note || "";textFormat:Text.PlainText;color:Theme.text;font.family:Theme.font;font.pixelSize:13;wrapMode:Text.Wrap}
                    Text{Layout.fillWidth:true;visible:!!card.modelData.sentence && card.modelData.sentence!==card.modelData.text;text:"原句 · "+(card.modelData.sentence || "");textFormat:Text.PlainText;color:Theme.muted;font.family:Theme.font;font.pixelSize:12;wrapMode:Text.Wrap}
                    Text{Layout.fillWidth:true;visible:!!card.modelData.entry_note;text:"我的备注 · "+(card.modelData.entry_note || "");textFormat:Text.PlainText;color:Theme.text;font.family:Theme.font;font.pixelSize:13;wrapMode:Text.Wrap}
                    Flow{Layout.fillWidth:true;spacing:8
                        TButton{text:"回到原句";quiet:true;implicitHeight:28;onClicked:root.openSource("tingmu://entry/"+card.modelData.id)}
                        TButton{objectName:"editCollectionNoteButton";text:card.modelData.entry_note ? "修改备注" : "添加备注";quiet:true;implicitHeight:28;onClicked:root.editNote(card.modelData)}
                    }
                }
            }
            Text{anchors.centerIn:parent;width:Math.max(100,parent.width-40);visible:inbox.count===0;text:statusFilter.currentIndex===1 ? "没有待整理的收藏。\n新收藏的词句会出现在这里。" : "这里还没有符合条件的收藏。\n观看时点击词语、选中原文或收藏整句。";color:Theme.muted;font.family:Theme.font;font.pixelSize:14;wrapMode:Text.Wrap;horizontalAlignment:Text.AlignHCenter;lineHeight:1.5}
        }
        Field{id:instructions;objectName:"collectionOrganizeInstructions";Layout.fillWidth:true;placeholderText:"本次整理要求（可选）：例如补充母语对比、生活例句、常见误用…"}
        RowLayout{
            Layout.fillWidth:true
            Text{Layout.fillWidth:true;text:root.canOrganize ? root.unorganizedCount+" 条未整理 · "+root.selectedIds.length+" 条已选择" : "笔记有未保存草稿，请先保存或放弃再整理。";color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
            TButton{objectName:"organizeCollectionsButton";text:backend.state.learningBusy ? "整理中…" : root.selectedIds.length ? "整理选中收藏为笔记" : "整理未整理收藏";primary:true;enabled:root.canOrganize && !backend.state.learningBusy && (root.selectedIds.length>0 || root.unorganizedCount>0);onClicked:root.organize()}
        }
        Text{Layout.fillWidth:true;visible:!!backend.state.error || !!backend.state.learningBusy;text:backend.state.error || "正在形成学习笔记，完成后可切换到“笔记”阅读。";color:backend.state.error ? Theme.error : Theme.muted;font.family:Theme.font;font.pixelSize:12;wrapMode:Text.Wrap}
    }
    AppDialog{
        id:noteDialog;objectName:"collectionNoteDialog";parent:Overlay.overlay;anchors.centerIn:parent;title:"词句备注";width:Math.min(520,parent ? parent.width-32 : 520);height:Math.min(390,parent ? parent.height-32 : 390);modal:true
        contentItem:ColumnLayout{
            spacing:12
            Text{Layout.fillWidth:true;text:root.editingEntry.text || root.editingEntry.sentence || "";textFormat:Text.PlainText;color:Theme.text;font.family:Theme.font;font.pixelSize:17;wrapMode:Text.Wrap}
            ScrollView{Layout.fillWidth:true;Layout.fillHeight:true;clip:true;TextArea{id:entryNote;objectName:"collectionEntryNote";placeholderText:"记下联想、疑问、母语对比或自己的例句…";color:Theme.text;placeholderTextColor:Theme.muted;font.family:Theme.font;font.pixelSize:14;wrapMode:TextArea.Wrap;selectByMouse:true;background:Rectangle{color:Theme.bg;border.color:Theme.line;radius:6}}}
            Text{Layout.fillWidth:true;visible:!!backend.state.error;text:backend.state.error || "";color:Theme.error;font.family:Theme.font;font.pixelSize:12;wrapMode:Text.Wrap}
            RowLayout{Layout.alignment:Qt.AlignRight;TButton{text:"取消";quiet:true;onClicked:noteDialog.close()}TButton{objectName:"saveCollectionEntryNote";text:"保存备注";primary:true;onClicked:backend.command("updateEntryNote",{id:root.editingEntry.id,note:entryNote.text,revision:root.editingEntry.note_revision || ""})}}
        }
    }
}
