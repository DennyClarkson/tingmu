import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S
Item {
    id:root
    property bool panelShown: true
    property var selectedIds:[]
    property string resetMode:"all"
    readonly property bool hasPending:!!backend.queueState.hasPending
    readonly property int failedCount:backend.queueState.failedCount || 0
    function selected(id){return selectedIds.indexOf(id)>=0}
    function toggle(id){var next=selectedIds.slice();var i=next.indexOf(id);if(i>=0)next.splice(i,1);else next.push(id);selectedIds=next}
    function confirmReset(mode){resetMode=mode;resetConfirm.open()}
    function openSettings(){settingsPopup.open()}
    function selectAll() {
        if (selectedIds.length) { selectedIds = []; return }
        var ids = []
        for (var i = 0; i < backend.jobModel.count; i++) {
            var job = backend.jobModel.get(i)
            if (job.status !== "running") ids.push(job.id)
        }
        selectedIds = ids
    }
    ColumnLayout {
        anchors.fill:parent; anchors.margins:12; spacing:10
        AgentWorkflow {}
        Flow {
            Layout.fillWidth:true;spacing:6
            TButton { objectName:"queueStartButton";text:backend.queueState.queueRunning ? qsTranslate("Tingmu", "暂停") : !root.hasPending && root.failedCount>0 ? qsTranslate("Tingmu", "重试失败") : qsTranslate("Tingmu", "开始队列"); primary:!backend.queueState.queueRunning; enabled:!!backend.queueState.queueRunning || root.hasPending || root.failedCount>0; onClicked:backend.command(backend.queueState.queueRunning ? "pauseQueue" : "startQueue",{}) }
            TButton { objectName:"queueClearCompletedButton";text:qsTranslate("Tingmu", "清除已完成"); quiet:true;enabled:!backend.queueState.queueRunning; onClicked:backend.command("clearCompleted",{}) }
        }
        Flow {
            Layout.fillWidth:true;spacing:6
            TButton{text:root.selectedIds.length ? qsTranslate("Tingmu", "取消选择") : qsTranslate("Tingmu", "全选");quiet:true;implicitHeight:28;onClicked:root.selectAll()}
            TButton{objectName:"resetAllJobsButton";danger:true;text:qsTranslate("Tingmu", "清除识别与讲解");enabled:root.selectedIds.length>0 && !backend.queueState.queueRunning;implicitHeight:28;onClicked:root.confirmReset("all")}
            TButton{objectName:"resetTeachingJobsButton";text:qsTranslate("Tingmu", "仅重做讲解");enabled:root.selectedIds.length>0 && !backend.queueState.queueRunning;implicitHeight:28;onClicked:root.confirmReset("teaching")}
        }
        FirstUseLoader {
            requested: root.panelShown
            Layout.fillWidth: true
            Layout.fillHeight: true
            sourceComponent: ListView {
                id:jobs; objectName:"queueList"
                clip:true; spacing:8; model:backend.jobModel
                ScrollBar.vertical:AppScrollBar {}
                delegate:Rectangle {
                    id: jobCard; objectName: "queueJob:" + modelData.id
                    required property var modelData
                    readonly property bool running: modelData.status === "running"
                    property bool evidenceOpen: false
                    readonly property var plan: modelData.plan || []
                    readonly property int step: plan.indexOf(modelData.stage_name)
                    width:Math.max(0, jobs.width - 10); height:jobContent.implicitHeight+22; radius:Theme.radiusMd; color:Theme.panel; border.color:Theme.line
                    ColumnLayout {
                        id:jobContent
                        anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:11; spacing:7
                        RowLayout {
                            Layout.fillWidth:true
                            Toggle{checked:root.selected(modelData.id);enabled:modelData.status!=="running";onToggled:root.toggle(modelData.id)}
                            Text {
                                text: modelData.name; textFormat: Text.PlainText
                                Layout.fillWidth: true; Layout.minimumWidth: 0
                                color: Theme.text; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold
                                elide: Text.ElideMiddle
                            }
                            StatusBadge {
                                objectName: "queueStatusBadge"
                                text: S.status(modelData.status)
                                tone: modelData.status === "failed" ? "danger" : modelData.status === "paused" ? "warning"
                                      : jobCard.running || modelData.status === "completed" ? "accent" : "neutral"
                            }
                            TButton { objectName:"queueRemoveJobButton";iconSource:"close"; quiet:true; implicitWidth:24; implicitHeight:24; enabled:!backend.queueState.queueRunning && modelData.status!=="running"; tip:qsTranslate("Tingmu", "移出队列"); onClicked:backend.command("removeJob",{id:modelData.id}) }
                        }
                        Text {
                            text: modelData.settings && modelData.settings.preparation_workflow === "subtitle_agent"
                                  ? qsTranslate("Tingmu", "字幕 Agent") : qsTranslate("Tingmu", "经典流程")
                            Layout.fillWidth: true; wrapMode: Text.Wrap
                            color: Theme.muted; font.family: Theme.font; font.pixelSize: 11
                        }
                        Text {
                            visible: !!modelData.decision
                            Layout.fillWidth: true; wrapMode: Text.Wrap
                            textFormat: Text.PlainText; font.family: Theme.font; font.pixelSize: 12; color: Theme.text
                            text: qsTranslate("TingmuStatus", modelData.decision || "")
                        }
                        Text {
                            visible: !!modelData.source_name || !!modelData.reference_name
                            Layout.fillWidth: true; wrapMode: Text.WrapAnywhere
                            textFormat: Text.PlainText; font.family: Theme.font; font.pixelSize: 11; color: Theme.muted
                            text: [modelData.source_name, modelData.reference_name === modelData.source_name
                                   ? "" : modelData.reference_name].filter(Boolean).map(name => /^内嵌字幕轨 \d+$/.test(name) ? qsTranslate("TingmuStatus", name) : name).join("  ·  ")
                        }
                        Text {
                            readonly property var counts: modelData.evidence_counts || []
                            visible: counts.length === 3 && (counts[0] > 0 || counts[2] > 0)
                            Layout.fillWidth: true; wrapMode: Text.Wrap
                            textFormat: Text.PlainText; font.family: Theme.font; font.pixelSize: 11; color: Theme.muted
                            text: counts[0] > 0 ? qsTranslate("Tingmu", "原文 %1 句 · 采用已有译文 %2 句").arg(counts[0]).arg(counts[1])
                                                : qsTranslate("Tingmu", "参考译文 %1 句").arg(counts[2] || 0)
                        }
                        TButton {
                            visible: !!(modelData.evidence_notes || []).length
                            quiet: true; implicitHeight: 26
                            text: jobCard.evidenceOpen ? qsTranslate("Tingmu", "收起字幕检查详情") : qsTranslate("Tingmu", "字幕检查详情")
                            onClicked: jobCard.evidenceOpen = !jobCard.evidenceOpen
                        }
                        Text {
                            visible: jobCard.evidenceOpen && !!(modelData.evidence_notes || []).length
                            Layout.fillWidth: true; wrapMode: Text.Wrap
                            textFormat: Text.PlainText; font.family: Theme.font; font.pixelSize: 11; color: Theme.muted
                            text: (modelData.evidence_notes || []).map(note => qsTranslate("TingmuEvidence", note)).join("\n")
                        }
                        Text {
                            visible: jobCard.plan.length > 0 && modelData.status !== "completed"
                            Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.muted
                            textFormat: Text.PlainText; font.family: Theme.font; font.pixelSize: 10
                            text: jobCard.plan.map(label => qsTranslate("TingmuStatus", label || "")).join("  ›  ")
                        }
                        Text {
                            visible: jobCard.step >= 0 && modelData.status !== "completed"
                            Layout.fillWidth: true; wrapMode: Text.Wrap; color: Theme.text
                            textFormat: Text.PlainText; font.family: Theme.font; font.pixelSize: 12
                            text: qsTranslate("Tingmu", "步骤 ") + (jobCard.step + 1) + "/" + jobCard.plan.length + "  ·  "
                                  + qsTranslate("TingmuStatus", modelData.activity || modelData.stage_name || "")
                                  + (modelData.stage_total > 0 ? "  ·  " + Math.floor(modelData.stage_done) + "/"
                                      + Math.ceil(modelData.stage_total) + " " + qsTranslate("TingmuStatus", modelData.unit || "")
                                      + " (" + Math.floor(100 * modelData.progress) + "%)" : "")
                        }
                        Text {
                            objectName: "queueJobMessage"
                            Layout.fillWidth: true
                            visible: text.length > 0
                            text: qsTranslate("TingmuStatus", modelData.message || "")
                            color: Theme.muted; font.family: Theme.font; font.pixelSize: 11
                            textFormat: Text.PlainText
                            wrapMode: modelData.status === "failed" ? Text.Wrap : Text.NoWrap
                            elide: modelData.status === "failed" ? Text.ElideNone : Text.ElideRight
                        }
                        TButton{objectName:"requeueJobButton";text:qsTranslate("Tingmu", "重新排队");iconSource:"repeat";quiet:true;implicitHeight:26;visible:modelData.status==="cleared" || modelData.status==="paused";enabled:!backend.queueState.queueRunning;onClicked:backend.command("retryJob",{id:modelData.id})}
                        AppProgressBar {
                            objectName: "queueStageProgressBar"; Layout.fillWidth: true
                            value: modelData.progress || 0
                            indeterminate: jobCard.running && !(modelData.stage_total > 0)
                        }
                        Repeater {
                            model: modelData.status === "completed" ? [] : (modelData.batches || [])
                            delegate: ColumnLayout {
                                id: taskRow
                                required property var modelData
                                Layout.fillWidth: true; spacing: 3
                                Text {
                                    Layout.fillWidth: true; wrapMode: Text.Wrap
                                    textFormat: Text.PlainText; font.family: Theme.font; font.pixelSize: 11; color: Theme.muted
                                    text: (taskRow.modelData.repair ? qsTranslate("Tingmu", "重试 · ") : "") + qsTranslate("TingmuStatus", taskRow.modelData.label || "") + "  ·  "
                                          + ({running:qsTranslate("Tingmu", "等待模型返回"),completed:qsTranslate("Tingmu", "已验证"),partial:qsTranslate("Tingmu", "部分通过"),failed:qsTranslate("Tingmu", "失败"),
                                              split:qsTranslate("Tingmu", "拆分重试"),cancelled:qsTranslate("Tingmu", "已取消"),interrupted:qsTranslate("Tingmu", "已中断")})[taskRow.modelData.status]
                                          + "  " + taskRow.modelData.done + "/" + taskRow.modelData.total
                                }
                                AppProgressBar {
                                    objectName: "queueBatchProgressBar"; Layout.fillWidth: true
                                    readonly property var batch: taskRow.modelData
                                    indeterminate: batch.status === "running"
                                    value: batch.total > 0 ? batch.done / batch.total : 0
                                }
                            }
                        }
                        TextEdit { objectName:"queueJobError";textFormat:TextEdit.PlainText; Layout.fillWidth:true; visible:!!modelData.error; text:qsTranslate("TingmuStatus", modelData.error || ""); color:Theme.error; font.family:Theme.font; font.pixelSize:12; wrapMode:TextEdit.Wrap;readOnly:true;selectByMouse:true;selectionColor:Theme.selection;selectedTextColor:Theme.text }
                        RowLayout {
                            visible: modelData.status === "failed"
                            TButton {
                                visible: !!modelData.needs_teaching
                                text: qsTranslate("Tingmu", "配置逐句讲解模型…")
                                onClicked: backend.command("openLessonSettings", {})
                            }
                            TButton { objectName:"queueRetryJobButton";text:qsTranslate("Tingmu", "重试");iconSource:"repeat";enabled:!backend.queueState.queueRunning;implicitHeight:30;tip:qsTranslate("Tingmu", "继续处理这个视频，已保存的步骤会复用");onClicked:backend.command("retryJob",{id:modelData.id}) }
                        }
                    }
                }
                Text { textFormat:Text.PlainText; anchors.centerIn:parent; width:parent.width-24; visible:jobs.count===0; text:qsTranslate("Tingmu", "先在媒体库勾选视频。\n识别、复核、讲解会依次完成。"); color:Theme.muted; font.family:Theme.font; font.pixelSize:12; wrapMode:Text.Wrap; horizontalAlignment:Text.AlignHCenter; lineHeight:1.5 }
            }
        }
    }
    // The shared speech controls also expose the active backend to language switching.
    SpeechSettings {
        id: speech
        formRequested: !!settingsContent.item
        parent: settingsContent.item ? settingsContent.item.speechHost : root
        width: parent.width
        visible: !!settingsContent.item
    }
    SettingPopup {
        id:settingsPopup
        width:Math.min(360,parent.width)
        height:Math.min(570,parent.height-8)
        contentItem: FirstUseLoader {
            id: settingsContent
            requested: settingsPopup.visible
            sourceComponent: AppScrollView {
                id:settingsScroll
                property alias speechHost: speechHost
                clip:true; contentWidth:availableWidth
                ColumnLayout {
                    width:settingsScroll.availableWidth; spacing:9
                    Text { textFormat:Text.PlainText; text:qsTranslate("Tingmu", "字幕处理"); color:Theme.text; font.family:Theme.font; font.pixelSize:16 }
                    LlmConcurrencySettings { Layout.fillWidth:true }
                    Item { id: speechHost; Layout.fillWidth: true; implicitHeight: speech.implicitHeight }
                    Text { textFormat:Text.PlainText; text:qsTranslate("Tingmu", "处理音轨（从 0 开始）"); color:Theme.muted; font.family:Theme.font; font.pixelSize:11 }
                    Field { Layout.fillWidth:true; text:String(backend.settings.audio_track || 0); validator:IntValidator{bottom:0;top:20} onEditingFinished:backend.setting("audio_track",Number(text)) }
                    TButton{text:qsTranslate("Tingmu", "管理全局模型与任务分配");Layout.fillWidth:true;onClicked:backend.requestDialog("models",root,{})}
                    Toggle { text:qsTranslate("Tingmu", "全文文本复核"); checked:backend.settings.review_enabled; onToggled:backend.setting("review_enabled",checked) }
                    Field { Layout.fillWidth:true; visible:backend.settings.review_enabled && !backend.settings.review_profile_id; text:backend.settings.review_model || ""; placeholderText:qsTranslate("Tingmu", "全局默认复核模型 ID"); onEditingFinished:backend.setting("review_model",text) }
                    Text { textFormat:Text.PlainText; visible:!backend.settings.annotation_profile_id; text:qsTranslate("Tingmu", "全局默认批量讲解"); color:Theme.muted; font.family:Theme.font; font.pixelSize:11 }
                    Choice { Layout.fillWidth:true; visible:!backend.settings.annotation_profile_id; property var providers:["openai","deepseek","local"]; translateOptions:true;model:["OpenAI","DeepSeek","仅使用本地词典"]; currentIndex:Math.max(0,providers.indexOf(backend.settings.provider)); onActivated:backend.setting("provider",providers[currentIndex]) }
                    Field { Layout.fillWidth:true; visible:!backend.settings.annotation_profile_id && backend.settings.provider!=="local"; text:backend.settings.model || ""; placeholderText:qsTranslate("Tingmu", "默认讲解模型 ID"); onEditingFinished:backend.setting("model",text) }
                    TextModelPolicy { Layout.fillWidth:true }
                    Text { textFormat:Text.PlainText; Layout.fillWidth:true; text:qsTranslate("Tingmu", "新加入的任务使用当前设置。文本复核保留原始字幕。"); color:Theme.muted; font.family:Theme.font; font.pixelSize:11; wrapMode:Text.Wrap }
                }
            }
        }
    }
    AppDialog {
        id:resetConfirm;objectName:"queueResetConfirmation";anchors.centerIn:parent
        width:Math.min(440,root.width-12);modal:true;title:root.resetMode==="all" ? qsTranslate("Tingmu", "清除识别与讲解？") : qsTranslate("Tingmu", "重新生成讲解？")

        contentItem:ColumnLayout{
            spacing:14
            Text{Layout.fillWidth:true;text:qsTranslate("Tingmu", "已选 ")+root.selectedIds.length+qsTranslate("Tingmu", " 个任务。")+(root.resetMode==="all" ? qsTranslate("Tingmu", "将清除生成的识别、复核与讲解，下次处理会重新开始。") : qsTranslate("Tingmu", "保留识别与已复核正文，只清除讲解并重新生成。"))+qsTranslate("Tingmu", "\n\n视频文件、词语收藏和个人笔记全部保留。运行中的任务不能重置。");color:Theme.text;font.family:Theme.font;font.pixelSize:13;wrapMode:Text.Wrap;lineHeight:1.45}
            Toggle{id:requeue;objectName:"requeueAfterReset";text:qsTranslate("Tingmu", "清除后重新加入队列");checked:true}
            RowLayout{Layout.alignment:Qt.AlignRight
                TButton{text:qsTranslate("Tingmu", "取消");quiet:true;onClicked:resetConfirm.close()}
                TButton{objectName:"confirmResetJobsButton";danger:true;text:requeue.checked ? qsTranslate("Tingmu", "清除并重新排队") : qsTranslate("Tingmu", "确认清除");enabled:!backend.queueState.queueRunning;onClicked:{backend.command("resetJobs",{ids:root.selectedIds,mode:root.resetMode,requeue:requeue.checked});root.selectedIds=[];resetConfirm.close()}}
            }
        }
    }
}
