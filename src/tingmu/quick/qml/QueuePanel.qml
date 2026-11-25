import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window
import "Style.js" as S

Item {
    id: root
    property bool panelShown: true
    property var selectedIds: []
    property string resetMode: "all"
    readonly property bool wide: width >= 630
    readonly property bool hasPending: !!backend.queueState.hasPending
    readonly property int failedCount: backend.queueState.failedCount || 0
    signal watchRequested(string path)
    signal navigateRequested(string destination)
    function selected(id) { return selectedIds.indexOf(id) >= 0 }
    function toggle(id) { selectedIds = selected(id) ? selectedIds.filter(i => i !== id) : selectedIds.concat(id) }
    function confirmReset(mode) { resetMode = mode; resetConfirm.open() }
    function openSettings() { settingsPopup.open() }
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
        anchors.fill: parent; anchors.margins: root.wide ? 14 : 9; spacing: 7
        GridLayout {
            Layout.fillWidth: true
            columns: root.wide ? 2 : 1; columnSpacing: 16; rowSpacing: 4
            Text {
                textFormat: Text.PlainText; Layout.fillWidth: true
                text: qsTranslate("Tingmu", "准备工作室")
                font.family: Theme.serif; font.weight: Font.DemiBold; font.pixelSize: root.wide ? 24 : 18; color: Theme.text
            }
            AgentWorkflow { Layout.maximumWidth: root.wide ? 310 : -1 }
        }
        Flow {
            Layout.fillWidth: true; spacing: 4
            TButton {
                objectName: "queueStartButton"
                text: backend.queueState.queueRunning ? qsTranslate("Tingmu", "暂停")
                     : !root.hasPending && root.failedCount > 0 ? qsTranslate("Tingmu", "重试失败") : qsTranslate("Tingmu", "开始队列")
                iconSource: backend.queueState.queueRunning ? "pause" : "play"
                primary: !backend.queueState.queueRunning
                enabled: !!backend.queueState.queueRunning || root.hasPending || root.failedCount > 0
                onClicked: backend.command(backend.queueState.queueRunning ? "pauseQueue" : "startQueue", {})
            }
            TButton {
                objectName: "queueClearCompletedButton"; text: qsTranslate("Tingmu", "清除已完成")
                quiet: true; enabled: !backend.queueState.queueRunning
                onClicked: backend.command("clearCompleted", {})
            }
            TButton {
                text: root.selectedIds.length ? qsTranslate("Tingmu", "取消选择") : qsTranslate("Tingmu", "全选")
                quiet: true; visible: backend.jobModel.count > 0; onClicked: root.selectAll()
            }
            TButton {
                objectName: "resetAllJobsButton"; danger: true; text: qsTranslate("Tingmu", "清除识别与讲解")
                visible: root.selectedIds.length > 0; enabled: visible && !backend.queueState.queueRunning
                onClicked: root.confirmReset("all")
            }
            TButton {
                objectName: "resetTeachingJobsButton"; text: qsTranslate("Tingmu", "仅重做讲解")
                visible: root.selectedIds.length > 0; enabled: visible && !backend.queueState.queueRunning
                onClicked: root.confirmReset("teaching")
            }
        }
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.line }
        FirstUseLoader {
            Layout.fillWidth: true; Layout.fillHeight: true
            requested: root.panelShown
            sourceComponent: Item {
                id: body
                ListView {
                    id: jobs
                    objectName: "queueList"
                    visible: count > 0
                    anchors { top: parent.top; bottom: parent.bottom; left: parent.left }
                    width: root.wide ? Math.min(250, Math.max(180, root.width * 0.30)) : body.width
                    clip: true; spacing: 6
                    property bool initialized: false
                    function checkSize() {
                        if (!initialized && width > 0 && height > 0) initialized = true
                    }
                    onWidthChanged: checkSize()
                    onHeightChanged: checkSize()
                    model: initialized ? backend.jobModel : null
                    property string pickedId: ""
                    property bool narrowExpanded: true
                    function followQueue() {
                        if (!root.panelShown || !count) return
                        // Only run on collection/status changes. Progress updates bind
                        // directly to the current stable ListView delegate.
                        var preferred = -1
                        var pending = -1
                        for (var i = 0; i < count; ++i) {
                            const row = backend.jobModel.get(i)
                            if (pickedId && row.id === pickedId) { currentIndex = i; return }
                            if (row.status === "running") preferred = i
                            if (pending < 0 && row.status === "queued") pending = i
                        }
                        pickedId = ""
                        currentIndex = preferred >= 0 ? preferred : pending >= 0 ? pending : count - 1
                    }
                    function inspect(index, id) {
                        if (!root.wide && pickedId === id && currentIndex === index) narrowExpanded = !narrowExpanded
                        else narrowExpanded = true
                        pickedId = id
                        currentIndex = index
                    }
                    onCountChanged: Qt.callLater(followQueue)
                    Component.onCompleted: { checkSize(); Qt.callLater(followQueue) }
                    Connections {
                        target: backend
                        enabled: root.panelShown
                        function onQueueStateChanged() { jobs.followQueue() }
                    }
                    Connections {
                        target: root
                        function onPanelShownChanged() { if (root.panelShown) jobs.followQueue() }
                    }
                    ScrollBar.vertical: AppScrollBar {}
                    delegate: Rectangle {
                        id: jobCard
                        required property var modelData
                        required property int index
                        objectName: "queueJob:" + modelData.id
                        property bool evidenceOpen: false
                        readonly property bool current: ListView.isCurrentItem
                        readonly property bool running: modelData.status === "running"
                        width: Math.max(0, jobs.width - 10); height: jobContents.implicitHeight + 20
                        radius: Theme.radiusMd
                        color: current ? Theme.tint(Theme.panel, Theme.accent, 0.075) : Theme.surfaceElevated
                        border.color: current ? Theme.accent : Theme.line
                        MouseArea {
                            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                            onClicked: jobs.inspect(jobCard.index, jobCard.modelData.id)
                        }
                        ColumnLayout {
                            id: jobContents
                            anchors { top: parent.top; left: parent.left; right: parent.right; margins: 10 }
                            spacing: 5
                            RowLayout {
                                Layout.fillWidth: true; spacing: 3
                                Toggle {
                                    checked: root.selected(jobCard.modelData.id); enabled: !jobCard.running
                                    onToggled: root.toggle(jobCard.modelData.id)
                                }
                                Text {
                                    Layout.fillWidth: true; Layout.minimumWidth: 0; textFormat: Text.PlainText
                                    text: jobCard.modelData.name; color: Theme.text; font.family: Theme.font
                                    font.pixelSize: 13; font.weight: Font.DemiBold; elide: Text.ElideMiddle
                                }
                                TButton {
                                    objectName: "queueRemoveJobButton"; iconSource: "close"; quiet: true
                                    implicitWidth: 24; implicitHeight: 24
                                    enabled: !backend.queueState.queueRunning && !jobCard.running
                                    tip: qsTranslate("Tingmu", "移出队列")
                                    onClicked: backend.command("removeJob", {id: jobCard.modelData.id})
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true
                                StatusBadge {
                                    objectName: "queueStatusBadge"; text: S.status(jobCard.modelData.status)
                                    tone: jobCard.modelData.status === "failed" ? "danger" : jobCard.modelData.status === "paused" ? "warning"
                                         : jobCard.running || jobCard.modelData.status === "completed" ? "accent" : "neutral"
                                }
                                Item { Layout.fillWidth: true }
                                TButton {
                                    iconSource: "chevron-right"; quiet: true; implicitWidth: 24; implicitHeight: 24
                                    tip: qsTranslate("Tingmu", "查看任务详情")
                                    onClicked: jobs.inspect(jobCard.index, jobCard.modelData.id)
                                }
                            }
                            Text {
                                objectName: "queueJobMessage"; Layout.fillWidth: true; visible: !!text
                                text: qsTranslate("TingmuStatus", jobCard.modelData.message || "")
                                textFormat: Text.PlainText; color: Theme.muted; font.family: Theme.font; font.pixelSize: 11
                                wrapMode: jobCard.modelData.status === "failed" ? Text.Wrap : Text.NoWrap
                                elide: jobCard.modelData.status === "failed" ? Text.ElideNone : Text.ElideRight
                            }
                            AppProgressBar {
                                id: rowBar
                                objectName: "queueStageProgressBar"; Layout.fillWidth: true; implicitHeight: 3
                                value: jobCard.modelData.progress || 0
                                indeterminate: jobCard.running && !(jobCard.modelData.stage_total > 0)
                                animationEnabled: root.panelShown && root.visible && !!root.Window.window
                                    && root.Window.window.visible && root.Window.window.visibility !== Window.Minimized
                                    && jobCard.y + rowBar.mapToItem(jobCard, 0, 0).y >= jobs.contentY
                                    && jobCard.y + rowBar.mapToItem(jobCard, 0, 0).y < jobs.contentY + jobs.height
                            }
                            TextEdit {
                                objectName: "queueJobError"; Layout.fillWidth: true; visible: !!text
                                text: qsTranslate("TingmuStatus", jobCard.modelData.error || "")
                                textFormat: TextEdit.PlainText; color: Theme.error; font.family: Theme.font; font.pixelSize: 12
                                wrapMode: TextEdit.WrapAnywhere; readOnly: true; selectByMouse: true
                                selectionColor: Theme.selection; selectedTextColor: Theme.text
                            }
                            TButton {
                                objectName: "requeueJobButton"; text: qsTranslate("Tingmu", "重新排队"); iconSource: "repeat"
                                quiet: true; implicitHeight: 26
                                visible: ["cleared", "paused"].includes(jobCard.modelData.status)
                                enabled: !backend.queueState.queueRunning
                                onClicked: backend.command("retryJob", {id: jobCard.modelData.id})
                            }
                            Flow {
                                Layout.fillWidth: true; spacing: 4; visible: jobCard.modelData.status === "failed"
                                TButton {
                                    visible: !!jobCard.modelData.needs_teaching
                                    text: qsTranslate("Tingmu", "配置逐句讲解模型…"); onClicked: backend.command("openLessonSettings", {})
                                }
                                TButton {
                                    objectName: "queueRetryJobButton"; text: qsTranslate("Tingmu", "重试"); iconSource: "repeat"
                                    enabled: !backend.queueState.queueRunning; implicitHeight: 30
                                    tip: qsTranslate("Tingmu", "继续处理这个视频，已保存的步骤会复用")
                                    onClicked: backend.command("retryJob", {id: jobCard.modelData.id})
                                }
                            }
                            Loader {
                                Layout.fillWidth: true
                                active: !root.wide && jobCard.current && jobs.narrowExpanded
                                visible: active
                                sourceComponent: PreparationStudio {
                                    compact: true
                                    job: jobCard.modelData; viewport: jobs; panelShown: root.panelShown
                                    evidenceOpen: jobCard.evidenceOpen
                                    onEvidenceOpenChanged: jobCard.evidenceOpen = evidenceOpen
                                    onWatchRequested: path => root.watchRequested(path)
                                    onNavigateRequested: target => root.navigateRequested(target)
                                }
                            }
                        }
                    }
                }
                AppScrollView {
                    id: detailScroll
                    objectName: "studioDetailScroll"
                    anchors { top: parent.top; bottom: parent.bottom; right: parent.right }
                    width: Math.max(0, body.width - (root.wide && jobs.count > 0 ? jobs.width + 12 : 0))
                    visible: root.wide || !jobs.count
                    clip: true; contentWidth: availableWidth
                    Loader {
                        width: detailScroll.availableWidth
                        active: detailScroll.visible
                        sourceComponent: PreparationStudio {
                            property var currentCard: jobs.currentItem
                            job: currentCard ? currentCard.modelData : ({})
                            viewport: detailScroll.contentItem
                            panelShown: root.panelShown
                            evidenceOpen: currentCard ? currentCard.evidenceOpen : false
                            onEvidenceOpenChanged: if (currentCard) currentCard.evidenceOpen = evidenceOpen
                            onWatchRequested: path => root.watchRequested(path)
                            onNavigateRequested: target => root.navigateRequested(target)
                        }
                    }
                }
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
                    SegmentationSettings { Layout.fillWidth:true }
                    LlmConcurrencySettings { Layout.fillWidth:true }
                    Item { id: speechHost; Layout.fillWidth: true; implicitHeight: speech.implicitHeight }
                    Text { textFormat:Text.PlainText; text:qsTranslate("Tingmu", "处理音轨（从 0 开始）"); color:Theme.muted; font.family:Theme.font; font.pixelSize:11 }
                    Field { Layout.fillWidth:true; text:String(backend.settings.audio_track || 0); validator:IntValidator{bottom:0;top:20} onEditingFinished:backend.setting("audio_track",Number(text)) }
                    TButton{text:qsTranslate("Tingmu", "管理全局模型与任务分配");Layout.fillWidth:true;onClicked:backend.requestDialog("models",root,{})}
                    Toggle { visible:backend.settings.segmentation_mode !== "llm_regroup";text:qsTranslate("Tingmu", "全文文本复核"); checked:backend.settings.review_enabled; onToggled:backend.setting("review_enabled",checked) }
                    Field { Layout.fillWidth:true; visible:(backend.settings.review_enabled || backend.settings.segmentation_mode === "llm_regroup") && !backend.settings.review_profile_id; text:backend.settings.review_model || ""; placeholderText:qsTranslate("Tingmu", "全局默认复核模型 ID"); onEditingFinished:backend.setting("review_model",text) }
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
