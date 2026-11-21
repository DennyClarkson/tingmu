import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S
Item {
    id:root
    property var selectedIds:[]
    property string resetMode:"all"
    readonly property bool hasPending:backend.jobs.some(function(job){return job.status==="queued" || job.status==="paused"})
    readonly property int failedCount:backend.jobs.filter(function(job){return job.status==="failed"}).length
    function selected(id){return selectedIds.indexOf(id)>=0}
    function toggle(id){var next=selectedIds.slice();var i=next.indexOf(id);if(i>=0)next.splice(i,1);else next.push(id);selectedIds=next}
    function confirmReset(mode){resetMode=mode;resetConfirm.open()}
    ModelManager{id:models}
    function openSettings(){settingsPopup.open()}
    ColumnLayout {
        anchors.fill:parent; anchors.margins:12; spacing:10
        Flow {
            Layout.fillWidth:true;spacing:6
            TButton { objectName:"queueStartButton";text:backend.state.queueRunning ? "暂停" : !root.hasPending && root.failedCount>0 ? "重试失败" : "开始队列"; primary:!backend.state.queueRunning; enabled:!!backend.state.queueRunning || root.hasPending || root.failedCount>0; onClicked:backend.command(backend.state.queueRunning ? "pauseQueue" : "startQueue",{}) }
            TButton { objectName:"queueClearCompletedButton";text:"清除已完成"; quiet:true;enabled:!backend.state.queueRunning; onClicked:backend.command("clearCompleted",{}) }
        }
        Flow {
            Layout.fillWidth:true;spacing:6
            TButton{text:root.selectedIds.length ? "取消选择" : "全选";quiet:true;implicitHeight:28;onClicked:root.selectedIds=root.selectedIds.length ? [] : backend.jobs.filter(function(j){return j.status!=="running"}).map(function(j){return j.id})}
            TButton{objectName:"resetAllJobsButton";text:"清除识别与讲解";enabled:root.selectedIds.length>0 && !backend.state.queueRunning;implicitHeight:28;onClicked:root.confirmReset("all")}
            TButton{objectName:"resetTeachingJobsButton";text:"仅重做讲解";enabled:root.selectedIds.length>0 && !backend.state.queueRunning;implicitHeight:28;onClicked:root.confirmReset("teaching")}
        }
        ListView {
            id:jobs; objectName:"queueList"
            Layout.fillWidth:true; Layout.fillHeight:true; clip:true; spacing:8; model:backend.jobs
            ScrollBar.vertical:ScrollBar{}
            delegate:Rectangle {
                required property var modelData
                width:jobs.width; height:jobContent.implicitHeight+18; radius:5; color:Theme.raised
                ColumnLayout {
                    id:jobContent
                    anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:9; spacing:7
                    RowLayout {
                        Layout.fillWidth:true
                        Toggle{checked:root.selected(modelData.id);enabled:modelData.status!=="running";onToggled:root.toggle(modelData.id)}
                        Text { textFormat:Text.PlainText; Layout.fillWidth:true; text:modelData.name; color:Theme.text; font.family:Theme.font; font.pixelSize:12; elide:Text.ElideMiddle }
                        TButton { objectName:"queueRemoveJobButton";iconSource:"close"; quiet:true; implicitWidth:24; implicitHeight:24; enabled:!backend.state.queueRunning && modelData.status!=="running"; tip:"移出队列"; onClicked:backend.command("removeJob",{id:modelData.id}) }
                    }
                    RowLayout {
                        Layout.fillWidth:true
                        Text { textFormat:Text.PlainText; text:S.status(modelData.status); color:modelData.status==="completed" ? Theme.accent : modelData.status==="failed" ? Theme.error : Theme.muted; font.family:Theme.font; font.pixelSize:10 }
                        Text { objectName:"queueJobMessage";textFormat:Text.PlainText; Layout.fillWidth:true; text:modelData.message || ""; color:Theme.muted; font.family:Theme.font; font.pixelSize:11;wrapMode:modelData.status==="failed" ? Text.Wrap : Text.NoWrap; elide:modelData.status==="failed" ? Text.ElideNone : Text.ElideRight }
                    }
                    TButton{objectName:"requeueJobButton";text:"重新排队";iconSource:"repeat";quiet:true;implicitHeight:26;visible:modelData.status==="cleared" || modelData.status==="paused";enabled:!backend.state.queueRunning;onClicked:backend.command("retryJob",{id:modelData.id})}
                    Rectangle {
                        Layout.fillWidth:true; height:3; radius:2; color:Theme.bg
                        Rectangle { width:parent.width * Math.max(0,Math.min(1,(modelData.progress || 0)>1 ? modelData.progress/100 : modelData.progress || 0)); height:3; radius:2; color:Theme.accent }
                    }
                    TextEdit { objectName:"queueJobError";textFormat:TextEdit.PlainText; Layout.fillWidth:true; visible:!!modelData.error; text:modelData.error || ""; color:Theme.error; font.family:Theme.font; font.pixelSize:12; wrapMode:TextEdit.Wrap;readOnly:true;selectByMouse:true;selectionColor:Theme.selection;selectedTextColor:Theme.text }
                    TButton { objectName:"queueRetryJobButton";text:"重试";iconSource:"repeat";visible:modelData.status==="failed";enabled:!backend.state.queueRunning;implicitHeight:30;tip:"继续处理这个视频，已保存的步骤会复用";onClicked:backend.command("retryJob",{id:modelData.id}) }
                }
            }
            Text { textFormat:Text.PlainText; anchors.centerIn:parent; width:parent.width-24; visible:jobs.count===0; text:"先在媒体库勾选视频。\n识别、复核、讲解会依次完成。"; color:Theme.muted; font.family:Theme.font; font.pixelSize:12; wrapMode:Text.Wrap; horizontalAlignment:Text.AlignHCenter; lineHeight:1.5 }
        }
    }
    SettingPopup {
        id:settingsPopup
        width:Math.min(360,parent.width)
        height:Math.min(570,parent.height-8)
        contentItem:ScrollView {
            id:settingsScroll
            clip:true; contentWidth:availableWidth
            ColumnLayout {
                width:settingsScroll.availableWidth; spacing:9
                Text { textFormat:Text.PlainText; text:"字幕处理"; color:Theme.text; font.family:Theme.font; font.pixelSize:16 }
                LlmConcurrencySettings { Layout.fillWidth:true }
                SpeechSettings { Layout.fillWidth:true }
                Text { textFormat:Text.PlainText; text:"处理音轨（从 0 开始）"; color:Theme.muted; font.family:Theme.font; font.pixelSize:11 }
                Field { Layout.fillWidth:true; text:String(backend.settings.audio_track || 0); validator:IntValidator{bottom:0;top:20} onEditingFinished:backend.setting("audio_track",Number(text)) }
                TButton{text:"管理模型与任务分配";Layout.fillWidth:true;onClicked:models.open()}
                Toggle { text:"全文文本复核"; checked:backend.settings.review_enabled; onToggled:backend.setting("review_enabled",checked) }
                Field { Layout.fillWidth:true; visible:backend.settings.review_enabled && !backend.settings.review_profile_id; text:backend.settings.review_model || ""; placeholderText:"默认复核模型 ID"; onEditingFinished:backend.setting("review_model",text) }
                Text { textFormat:Text.PlainText; visible:!backend.settings.annotation_profile_id; text:"默认批量讲解"; color:Theme.muted; font.family:Theme.font; font.pixelSize:11 }
                Choice { Layout.fillWidth:true; visible:!backend.settings.annotation_profile_id; property var providers:["openai","deepseek","local"]; model:["OpenAI","DeepSeek","仅使用本地词典"]; currentIndex:Math.max(0,providers.indexOf(backend.settings.provider)); onActivated:backend.setting("provider",providers[currentIndex]) }
                Field { Layout.fillWidth:true; visible:!backend.settings.annotation_profile_id && backend.settings.provider!=="local"; text:backend.settings.model || ""; placeholderText:"默认讲解模型 ID"; onEditingFinished:backend.setting("model",text) }
                TextModelPolicy { Layout.fillWidth:true }
                Text { textFormat:Text.PlainText; Layout.fillWidth:true; text:"新加入的任务使用当前设置。文本复核保留原始字幕。"; color:Theme.muted; font.family:Theme.font; font.pixelSize:11; wrapMode:Text.Wrap }
            }
        }
    }
    AppDialog {
        id:resetConfirm;objectName:"queueResetConfirmation";anchors.centerIn:parent
        width:Math.min(440,root.width-12);modal:true;title:root.resetMode==="all" ? "清除识别与讲解？" : "重新生成讲解？"
        background:Rectangle{color:Theme.panel;radius:9;border.color:Theme.line}
        contentItem:ColumnLayout{
            spacing:14
            Text{Layout.fillWidth:true;text:"已选 "+root.selectedIds.length+" 个任务。"+(root.resetMode==="all" ? "将清除生成的识别、复核与讲解，下次处理会重新开始。" : "保留识别与已复核正文，只清除讲解并重新生成。")+"\n\n视频文件、词语收藏和个人笔记全部保留。运行中的任务不能重置。";color:Theme.text;font.family:Theme.font;font.pixelSize:13;wrapMode:Text.Wrap;lineHeight:1.45}
            Toggle{id:requeue;objectName:"requeueAfterReset";text:"清除后重新加入队列";checked:true}
            RowLayout{Layout.alignment:Qt.AlignRight
                TButton{text:"取消";quiet:true;onClicked:resetConfirm.close()}
                TButton{objectName:"confirmResetJobsButton";text:requeue.checked ? "清除并重新排队" : "确认清除";primary:true;enabled:!backend.state.queueRunning;onClicked:{backend.command("resetJobs",{ids:root.selectedIds,mode:root.resetMode,requeue:requeue.checked});root.selectedIds=[];resetConfirm.close()}}
            }
        }
    }
}
