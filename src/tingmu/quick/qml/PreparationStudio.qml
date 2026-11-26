import QtQuick
import QtQuick.Layouts
import QtQuick.Window
import "Style.js" as S

ColumnLayout {
    id: studio
    objectName: "preparationStudio"
    readonly property var studioRoot: studio
    property var job: ({})
    property bool panelShown: true
    property Flickable viewport
    property bool evidenceOpen: false
    property bool compact: false
    signal watchRequested(string path)
    signal navigateRequested(string destination)
    readonly property bool hasJob: !!job.id
    readonly property bool running: job.status === "running"
    readonly property bool complete: job.status === "completed"
    readonly property var plan: job.plan || []
    readonly property var audit: job.quality || ({})
    readonly property var counts: job.evidence_counts || []
    readonly property var samples: job.verified_examples || []
    readonly property var slots: job.batch_slots || job.batches || []
    readonly property int capacity: Math.min(16, Math.max(1, job.worker_limit || slots.length || 1))
    readonly property int activeCount: slots.filter(row => row.status === "running").length
    readonly property bool measured: complete || job.stage_total > 0
    readonly property int stageIndex: plan.indexOf(job.stage_name)
    readonly property real stageFraction: job.stage_total > 0 ? Math.max(0, Math.min(1, job.stage_done / job.stage_total)) : job.progress || 0
    readonly property real overall: complete ? 1 : stageIndex >= 0 && plan.length
        ? (stageIndex + (measured ? stageFraction : 0)) / plan.length : stageFraction
    readonly property bool adopted: ["source", "bilingual"].includes(job.adoption)
    property double clockMs: Date.now()
    onJobChanged: if (running) clockMs = Date.now()
    spacing: compact ? 9 : 14

    function onScreen(item, collapseAnchor) {
        if (!panelShown || !visible || !viewport || !Theme.canAnimate(collapseAnchor || item))
            return false
        // Reading ancestor positions keeps bindings current while a layout moves or resizes.
        let top = 0, left = 0
        let bottom = Math.max(item.height, collapseAnchor ? 1 : 0), right = item.width
        let node = item
        while (node && node !== viewport.contentItem) {
            top += node.y; bottom += node.y
            left += node.x; right += node.x
            node = node.parent
            if (node && node.clip && node !== viewport.contentItem) {
                top = Math.max(0, top); bottom = Math.min(node.height, bottom)
                left = Math.max(0, left); right = Math.min(node.width, right)
            }
        }
        return !!node && right > left && bottom > top && top < viewport.contentY + viewport.height
            && bottom > viewport.contentY && left < viewport.contentX + viewport.width && right > viewport.contentX
    }
    readonly property string streamIdentity: JSON.stringify([job.id, job.matrix_stage, job.stage_name,
        job.status, slots.map(b => [b.id, b.status])])
    onStreamIdentityChanged: Qt.callLater(refreshStreams)
    function refreshStreams(visibleOnly) {
        if (!panelShown || !visible || !hasJob || !slots.length || !workers) return
        const streams = backend.studioStreams(job.id, job.matrix_stage || job.stage_name || "")
        for (let i = 0; i < workers.count; ++i) {
            const worker = workers.itemAt(i)
            if (!worker || worker.batch.id === undefined || (visibleOnly && !worker.streamVisible)) continue
            const sample = streams[String(worker.batch.id)]
            if (sample) worker.stream.accept(sample)
            else if (worker.streamVisible) worker.stream.snapOnNext = false
        }
    }
    Timer {
        id: streamPoll
        objectName: "studioStreamPoll"
        interval: 50; repeat: true
        running: studio.running && studio.onScreen(matrixCard) && workers.haveVisibleRequest
        onTriggered: studio.refreshStreams(true)
    }
    function outcome(status) {
        switch (status) {
        case "running": return qsTranslate("Tingmu", "处理中")
        case "completed": return qsTranslate("Tingmu", "已验证")
        case "partial": return qsTranslate("Tingmu", "部分通过")
        case "split": return qsTranslate("Tingmu", "拆分重试")
        case "failed": return qsTranslate("Tingmu", "失败")
        case "cancelled": return qsTranslate("Tingmu", "已取消")
        case "interrupted": return qsTranslate("Tingmu", "已中断")
        default: return qsTranslate("Tingmu", "空闲")
        }
    }
    function sourceLabel() {
        switch (job.adoption) {
        case "bilingual": return qsTranslate("Tingmu", "双语字幕")
        case "source": return qsTranslate("Tingmu", "采用原文")
        case "reference": return qsTranslate("Tingmu", "仅作参考")
        case "asr": return qsTranslate("Tingmu", "语音识别")
        }
        switch (audit.source_kind) {
        case "sidecar": return qsTranslate("Tingmu", "外挂字幕")
        case "embedded": return qsTranslate("Tingmu", "内嵌字幕")
        case "local_qwen": case "local_whisper": case "local_api": case "external_api":
            return qsTranslate("Tingmu", "语音识别")
        }
        return ""
    }
    function elapsed() {
        if (running && job.run_started_at)
            return S.time(Math.max(0, clockMs - job.run_started_at * 1000))
        return job.elapsed_seconds !== undefined ? S.time(job.elapsed_seconds * 1000) : "—"
    }
    Timer {
        objectName: "studioElapsedTimer"
        interval: 1000
        repeat: true
        running: studio.running && studio.onScreen(metrics)
        onTriggered: studio.clockMs = Date.now()
    }
    component Label: Text {
        textFormat: Text.PlainText
        font.family: Theme.font
        font.pixelSize: 12
        color: Theme.text
        wrapMode: Text.Wrap
        Layout.fillWidth: true
        Layout.minimumWidth: 0
    }
    component Heading: Label {
        font.weight: Font.DemiBold
        font.pixelSize: 14
    }
    component Card: Rectangle {
        default property alias contents: stack.data
        readonly property real contentsHeight: stack.implicitHeight + 24
        Layout.fillWidth: true
        Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
        implicitHeight: contentsHeight
        color: Theme.surfaceElevated
        border.color: Theme.line
        radius: Theme.radiusMd
        ColumnLayout {
            id: stack
            anchors { left: parent.left; right: parent.right; top: parent.top; margins: 12 }
            spacing: 8
        }
    }
    component Metric: ColumnLayout {
        property string value
        property string caption
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        spacing: 2
        Text {
            Layout.fillWidth: true
            textFormat: Text.PlainText
            text: parent.value
            font.family: Theme.mono
            font.pixelSize: studio.width >= 615 ? 29 : studio.compact ? 17 : 22
            color: Theme.accent
            elide: Text.ElideRight
        }
        Label { text: parent.caption; font.pixelSize: 11; color: Theme.muted }
    }

    Card {
        objectName: "studioEmpty"
        visible: !studio.hasJob
        Heading { text: qsTranslate("Tingmu", "准备第一部视频"); font.pixelSize: 20 }
        Label {
            text: qsTranslate("Tingmu", "在媒体库勾选视频并加入队列，即可查看字幕来源、处理进度和译稿核验结果。")
            color: Theme.muted
        }
        Flow {
            Layout.fillWidth: true; spacing: 5
            TButton {
                text: qsTranslate("Tingmu", "添加视频…"); iconSource: "plus"
                onClicked: backend.command("addFiles", {})
            }
            TButton {
                text: qsTranslate("Tingmu", "准备当前视频"); primary: true
                visible: !!backend.mediaState.mediaPath
                onClicked: backend.command("prepareCurrent", {})
            }
        }
    }
    ColumnLayout {
        visible: studio.hasJob
        Layout.fillWidth: true; spacing: 6
        Label {
            visible: !studio.compact
            text: studio.job.name || ""
            font.family: Theme.serif; font.pixelSize: 21; font.weight: Font.DemiBold
            maximumLineCount: 2; elide: Text.ElideMiddle; wrapMode: Text.WrapAnywhere
        }
        Flow {
            Layout.fillWidth: true; spacing: 5
            StatusBadge {
                visible: !studio.compact
                text: S.status(studio.job.status)
                filledAccent: studio.complete || studio.running
                tone: studio.complete || studio.running ? "accent" : studio.job.status === "failed" ? "danger" : "neutral"
            }
            StatusBadge {
                visible: !!studio.job.settings
                text: studio.job.settings && studio.job.settings.preparation_workflow === "subtitle_agent"
                      ? qsTranslate("Tingmu", "字幕 Agent") : qsTranslate("Tingmu", "经典流程")
            }
            StatusBadge {
                objectName: "studioAdoptedSource"; visible: studio.adopted
                tone: "accent"; filledAccent: true
                text: qsTranslate("Tingmu", "已采纳原文字幕 · 跳过语音转录")
            }
            StatusBadge {
                objectName: "studioSegmentationMode"
                visible: !!studio.job.settings
                text: studio.job.settings && studio.job.settings.segmentation_mode === "llm_regroup"
                      ? qsTranslate("Tingmu", "模型智能分句") : qsTranslate("Tingmu", "本地声学分句")
            }
        }
        Label {
            visible: studio.job.status === "queued" && !studio.plan.length
            text: qsTranslate("Tingmu", "启动队列后，此处会显示本片实际采用的处理步骤。")
            color: Theme.muted
        }
        TButton {
            objectName: "studioWatchButton"
            visible: studio.complete
            primary: true; iconSource: "play"
            text: qsTranslate("Tingmu", "开始观看")
            onClicked: studio.watchRequested(studio.job.path || studio.job.media_path)
        }
    }
    Card {
        visible: studio.hasJob && (studio.plan.length > 0 || studio.running || studio.complete)
        RowLayout {
            Layout.fillWidth: true
            Heading {
                text: studio.complete ? qsTranslate("Tingmu", "处理完成")
                    : qsTranslate("TingmuStatus", studio.job.activity || studio.job.stage_name || "") || qsTranslate("Tingmu", "处理进度")
                font.pixelSize: studio.compact ? 16 : 19
            }
            Label {
                visible: studio.stageIndex >= 0 && !studio.complete; Layout.fillWidth: false; color: Theme.muted
                text: qsTranslate("Tingmu", "步骤 %1 / %2").arg(studio.stageIndex + 1).arg(studio.plan.length)
            }
        }
        RowLayout {
            id: metrics
            objectName: "studioMetrics"; Layout.fillWidth: true; spacing: studio.width > 600 ? 14 : 6
            ColumnLayout {
                spacing: 1; Layout.fillWidth: true; Layout.preferredWidth: 1.18
                Label {
                    objectName: "studioOverallPercent"
                    text: studio.complete || studio.stageIndex >= 0 || studio.measured
                        ? Math.floor(100 * percentMotion.value) + "%" : "—"
                    StudioValue {
                        id: percentMotion; destination: studio.overall
                        permitted: studio.onScreen(metrics)
                    }
                    font.family: Theme.mono; font.pixelSize: studio.width >= 610 ? 39 : studio.width >= 350 ? 30 : 22
                    font.weight: Font.DemiBold; color: Theme.accent
                }
                Label { text: qsTranslate("Tingmu", "总进度 · 按步骤"); font.pixelSize: 11; color: Theme.muted }
            }
            Metric { caption: qsTranslate("Tingmu", "耗时"); value: studio.elapsed() }
            Metric { caption: qsTranslate("Tingmu", "句 / 秒"); value: studio.job.throughput > 0 ? studio.job.throughput.toFixed(2) : "—" }
            Metric {
                caption: qsTranslate("Tingmu", "活跃批次")
                value: studio.job.worker_limit || studio.slots.length ? studio.activeCount + "/" + studio.capacity : "—"
            }
        }
        AppProgressBar {
            id: stageBar
            objectName: "studioStageProgressBar"; Layout.fillWidth: true; value: progressMotion.value
            indeterminate: studio.running && studio.stageIndex <= 0 && !studio.measured
            animationEnabled: studio.onScreen(stageBar)
            StudioValue {
                id: progressMotion; destination: studio.overall
                permitted: studio.onScreen(stageBar)
            }
        }
        StudioStepper { studio: studioRoot }
        Label {
            visible: !!studio.job.stage_total && !studio.complete
            color: Theme.muted; font.pixelSize: 12
            text: qsTranslate("TingmuStatus", studio.job.activity || studio.job.stage_name || "") + "   "
                  + Math.floor(studio.job.stage_done || 0) + " / " + Math.ceil(studio.job.stage_total || 0)
                  + " " + (studio.job.unit === "块" ? qsTranslate("Tingmu", "块") : qsTranslate("TingmuStatus", studio.job.unit || ""))
                  + "   ·   " + Math.floor(studio.stageFraction * 100) + "%"
        }
    }
    GridLayout {
        id: detailGrid; Layout.fillWidth: true
        visible: studio.hasJob; columns: width >= 705 ? 2 : 1
        columnSpacing: 10; rowSpacing: 10
        Card {
            id: matrixCard
            Layout.columnSpan: studio.samples.length > 0 ? 1 : detailGrid.columns
            objectName: "studioBatchMatrix"
            visible: studio.hasJob && (studio.slots.length > 0 || matrixReveal.value > 0)
            implicitHeight: contentsHeight * matrixReveal.value
            opacity: matrixReveal.value; clip: true
            StudioValue {
                id: matrixReveal; destination: studio.slots.length > 0 ? 1 : 0
                permitted: studio.onScreen(matrixCard, detailGrid)
            }
            Heading { text: qsTranslate("Tingmu", "并发批次") }
            Label {
                color: Theme.muted; font.pixelSize: 11
                text: qsTranslate("TingmuStatus", studio.job.matrix_stage || studio.job.stage_name || "")
            }
            GridLayout {
                Layout.fillWidth: true
                columns: Math.min(studio.capacity, width >= 640 ? 4 : width >= 425 ? 3 : 2)
                columnSpacing: 4; rowSpacing: 4
                Repeater {
                    id: workers
                    model: studio.capacity
                    property int revision: 0
                    onItemAdded: { revision++; Qt.callLater(studio.refreshStreams) }
                    onItemRemoved: revision++
                    readonly property bool haveVisibleRequest: {
                        const revisionRead = revision
                        for (let i = 0; i < count; ++i) {
                            const worker = itemAt(i)
                            if (worker && worker.processing && worker.streamVisible) return true
                        }
                        return false
                    }
                    delegate: StudioWorker { studio: studioRoot }
                }
            }
            Label {
                readonly property var issues: (studio.job.batches || []).filter(b => ["partial", "split", "failed"].includes(b.status))
                visible: issues.length > 0; color: Theme.warning; font.pixelSize: 11
                text: issues.map(b => qsTranslate("TingmuStatus", b.label) + " · " + studio.outcome(b.status)).join("   ")
            }
        }
        Card {
            objectName: "studioVerifiedPreview"; visible: studio.samples.length > 0
            Heading { text: qsTranslate("Tingmu", "最近核验的双语字幕") }
            VerifiedRows { studio: studioRoot }
        }
        Card {
            objectName: "studioSourceEvidence"
            visible: studio.hasJob && (!!studio.job.decision || !!studio.sourceLabel() || !!(studio.job.evidence_notes || []).length)
            RowLayout {
                Layout.fillWidth: true
                Heading { text: qsTranslate("Tingmu", "字幕来源") }
                StatusBadge { objectName: "studioAdoptionBadge"; visible: !!text; text: studio.sourceLabel(); tone: "accent" }
            }
            Label { visible: !!text; text: qsTranslate("TingmuStatus", studio.job.decision || "") }
            Label {
                visible: !!text; font.pixelSize: 11; color: Theme.muted; wrapMode: Text.WrapAnywhere
                text: [studio.job.source_name, studio.job.reference_name !== studio.job.source_name ? studio.job.reference_name : ""]
                          .filter(Boolean).map(name => /^内嵌字幕轨 \d+$/.test(name) ? qsTranslate("TingmuStatus", name) : name).join(" · ")
            }
            Label {
                objectName: "studioPairedRatio"
                visible: studio.counts.length === 3 && (studio.counts[0] > 0 || studio.counts[2] > 0)
                text: studio.counts[0] > 0 ? qsTranslate("Tingmu", "已有译文配对 %1 / %2 句（%3%）")
                        .arg(studio.counts[1]).arg(studio.counts[0]).arg(Math.round(100 * studio.counts[1] / studio.counts[0]))
                        : qsTranslate("Tingmu", "参考译文 %1 句").arg(studio.counts[2] || 0)
            }
            AppProgressBar {
                objectName: "studioCoverageBar"; Layout.fillWidth: true; visible: studio.counts[0] > 0
                value: studio.counts[0] ? studio.counts[1] / studio.counts[0] : 0
            }
            TButton {
                id: evidenceToggle
                objectName: "studioEvidenceToggle"
                visible: !!(studio.job.evidence_notes || []).length || !!(studio.job.track_evidence || []).length
                quiet: true; implicitHeight: Theme.compactHeight
                text: studio.evidenceOpen ? qsTranslate("Tingmu", "收起字幕检查详情") : qsTranslate("Tingmu", "字幕检查详情")
                onClicked: studio.evidenceOpen = !studio.evidenceOpen
            }
            Item {
                id: evidenceBody
                objectName: "studioEvidenceBody"
                Layout.fillWidth: true
                visible: expansion.value > 0
                implicitHeight: evidenceContent.implicitHeight * expansion.value
                opacity: expansion.value; clip: true
                StudioValue {
                    id: expansion; destination: studio.evidenceOpen ? 1 : 0
                    permitted: studio.onScreen(evidenceToggle)
                }
                ColumnLayout {
                    id: evidenceContent
                    width: parent.width; spacing: 6
                    Repeater {
                        model: studio.job.track_evidence || []
                        delegate: ColumnLayout {
                            required property var modelData
                            Layout.fillWidth: true; spacing: 2
                            Label {
                                text: [(modelData.name.startsWith("内嵌字幕轨 ") ? qsTranslate("TingmuStatus", modelData.name) : modelData.name),
                                    modelData.codec, modelData.language].filter(Boolean).join(" · ")
                                font.weight: Font.DemiBold; wrapMode: Text.WrapAnywhere
                            }
                            Label {
                                text: [modelData.source ? qsTranslate("Tingmu", "采用原文") : "", modelData.reference ? qsTranslate("Tingmu", "参考译文") : "",
                                    qsTranslate("TingmuEvidence", modelData.reason || "")].filter(Boolean).join(" · ")
                                visible: !!text; color: Theme.muted; font.pixelSize: 11
                            }
                        }
                    }
                    Label {
                        visible: !!text
                        text: (studio.job.screening_notes || studio.job.evidence_notes || []).map(note => qsTranslate("TingmuEvidence", note)).join("\n")
                        font.pixelSize: 11; color: Theme.muted
                    }
                }
            }
        }
        Card {
            objectName: "studioQualityAudit"; visible: studio.audit.input_cue_count !== undefined
            Heading { text: qsTranslate("Tingmu", "分句质量") }
            Label {
                text: qsTranslate("Tingmu", "输入 %1 句 → 输出 %2 句").arg(studio.audit.input_cue_count || 0).arg(studio.audit.output_cue_count || 0)
                color: Theme.accent
            }
            Label {
                text: qsTranslate("Tingmu", "合并 %1 · 拆分 %2 · 时间修复 %3 · 短于 0.5 秒 %4")
                    .arg(studio.audit.coalesced_count || 0).arg(studio.audit.split_runon_count || 0)
                    .arg(studio.audit.repaired_timing_count || 0).arg(studio.audit.sub_500ms_count || 0)
                color: Theme.muted; font.pixelSize: 11
            }
            Label {
                visible: studio.audit.chunk_count !== undefined
                text: qsTranslate("Tingmu", "智能重组 %1 块 · 本地回退 %2 块 · 文本调整 %3 句")
                    .arg(studio.audit.chunk_count || 0).arg(studio.audit.fallback_chunk_count || 0).arg(studio.audit.corrected_count || 0)
                color: Theme.muted; font.pixelSize: 11
            }
            Label {
                visible: studio.audit.input_unit_count !== undefined
                text: studio.audit.covered_unit_count !== undefined
                    ? qsTranslate("Tingmu", "覆盖单元 %1 / %2").arg(studio.audit.covered_unit_count).arg(studio.audit.input_unit_count)
                    : qsTranslate("Tingmu", "输入单元 %1").arg(studio.audit.input_unit_count || 0)
                color: Theme.muted; font.pixelSize: 11
            }
        }
    }
    ReadinessCard {
        Layout.fillWidth: true
        active: studio.visible && studio.panelShown
        job: studio.job
        onNavigate: target => studio.navigateRequested(target)
    }
}
