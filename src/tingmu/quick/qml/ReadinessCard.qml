import QtQuick
import QtQuick.Layouts

Rectangle {
    id: card
    objectName: "preparationReadinessCard"
    property bool active: visible
    property var job: ({})
    property string scenario: "auto"
    property bool expanded: false
    property var report: ({issues: []})
    readonly property var issues: report.issues || []
    readonly property var conditional: report.asrFallback || []
    readonly property var checks: issues.concat(conditional)
    signal navigate(string target)
    readonly property string currentPath: backend.mediaState.mediaPath || ""
    // Refresh only when the source evidence changes, not on word/token/progress updates.
    readonly property string evidence: JSON.stringify([
        (job || {}).id, (job || {}).media_path || (job || {}).path, (job || {}).subtitle_path,
        (job || {}).evidence_counts, (job || {}).force_asr, (job || {}).reuse_existing_transcript,
        (job || {}).teaching_only, (job || {}).settings, (job || {}).retry_text_settings,
        (job || {}).needs_teaching, (job || {}).status
    ])
    readonly property var issueText: ({
            "teach_frozen": "入队时的权限未允许逐句讲解。",
            "review_frozen": "入队时的权限未允许文本复核。",
            "smart_frozen": "入队时的权限未允许模型智能分句。",
            "frozenRetry": "此 Agent 任务保留了原文。配置讲解模型并授权后，点击本任务的重试以应用新的文本设置。",
            "frozenSpec": "本任务沿用入队时的设置。要使用当前工作区的新设置，请在媒体库重新将视频加入队列。",
            "unchecked": "尚未检测所需运行组件。可在本机检查，无需下载。",
            "ffmpeg": "缺少 FFmpeg，无法提取音轨进行识别。",
            "local-asr": "缺少 Whisper 引擎，无法运行本机 Whisper 模型。",
            "qwen": "缺少 Qwen 运行环境，无法运行本机 Qwen 模型。",
            "whisper-cuda": "当前选择 CUDA 加速，组件未就绪。可安装组件，或在语音设置改用 CPU / auto。",
            "weights": "缺少 %1 的完整模型文件，识别前请下载或选择已有模型。",
            "language": "所选模型不支持 %1，请更换语音模型。",
            "apiConfig": "转写地址或模型 ID 无效，请检查所选语音服务。",
            "audioConsent": "工作区未授权音轨上传。外部识别需要单独允许发送所选音轨。",
            "localApi": "已选择本机转写服务 %1。识别前请启动该服务。",
            "externalApi": "已授权所选音轨发送到 %1。服务连接情况将在转写时确认。",
            "teach_off": "工作区已停用文本模型。原文可保留，逐句讲解和译稿核验需启用文本模型。",
            "teach_missing": "逐句讲解缺少可用的全局连接。请分配讲解模型并检查凭据。",
            "teach_remote": "已分配远程讲解模型 %1，但工作区仅允许本机连接。请核对权限或改用本机模型。",
            "smart_off": "智能分句需要文本模型。可启用模型或切换本地分句。",
            "smart_missing": "智能分句缺少可用模型。请分配复核模型，或在未分配复核连接时使用讲解模型。",
            "smart_remote": "智能分句所用的 %1 是远程模型，当前权限仅限本机。",
            "review_off": "文本复核已暂停，原始字幕仍可保存。",
            "review_missing": "逐句文本复核缺少可用连接。需要复核时请检查全局任务分配。",
            "review_remote": "已分配远程复核模型 %1，当前权限仅限本机。"
    })
    readonly property var sourceText: ({
        "watch": "播放与外挂字幕可直接使用。",
        "caption": "已选择原文字幕，可跳过语音识别。",
        "asr": "需要从所选音轨生成原文字幕。",
        "evaluate": "Agent 将检查本片字幕，缺少可用原文时再识别音轨。",
        "choose": "选择视频后，经典流程会查找同名字幕。",
        "text": "逐句讲解与智能分句使用本工作区允许的文本连接。"
    })
    function refresh() {
        if (active)
            report = workspaces.preparationReadiness(scenario, job || {}, currentPath)
    }
    function schedule() { if (active) Qt.callLater(refresh) }
    Component.onCompleted: schedule()
    onActiveChanged: { if (!active) expanded = false; schedule() }
    onEvidenceChanged: schedule()
    onScenarioChanged: schedule()
    onCurrentPathChanged: schedule()
    Connections {
        target: backend
        function onSettingsChanged() { card.schedule() }
        function onModelsChanged() { card.schedule() }
    }
    Connections {
        target: workspaces
        function onPreparationReadinessChanged() { card.schedule() }
    }
    function repair(action) {
        if (action === "allowRemote") { permission.active = true; permission.item.review() }
        else if (action === "allowLocal") backend.command("textModelPolicy", {mode: "local"})
        else if (action === "checkRuntime") runtimeSetup.detect()
        else navigate(action)
    }
    function buttonLabel(action) {
        if (action === "checkRuntime" || action === "runtime") return qsTranslate("Tingmu", "检查运行环境")
        if (action === "allowLocal") return qsTranslate("Tingmu", "启用本机文本模型")
        if (action === "allowRemote") return qsTranslate("Tingmu", "授权远程文本…")
        if (action === "speech") return qsTranslate("Tingmu", "语音设置")
        if (action === "text") return qsTranslate("Tingmu", "文本权限")
        if (action === "models") return qsTranslate("Tingmu", "全局模型")
        return ""
    }
    Loader {
        id: permission
        active: false
        sourceComponent: RemoteTextPermissionDialog { origin: card }
    }
    color: Theme.bg
    border.color: Theme.line
    radius: Theme.radiusMd
    implicitHeight: content.implicitHeight + 20
    component Body: Text {
        Layout.fillWidth: true; Layout.minimumWidth: 0; wrapMode: Text.Wrap
        textFormat: Text.PlainText; font.family: Theme.font; font.pixelSize: 12; color: Theme.muted
    }
    ColumnLayout {
        id: content
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: 10; spacing: 5
        Body {
            text: card.report.job ? qsTranslate("Tingmu", "所选任务的准备条件") : qsTranslate("Tingmu", "当前工作区的准备条件")
            color: Theme.text; font.weight: Font.DemiBold
        }
        Body {
            visible: !!card.report.source
            text: qsTranslate("Tingmu", card.sourceText[card.report.source] || "")
        }
        Body {
            visible: !!card.report.workflow && card.scenario !== "watch"
            text: [card.report.workflow === "subtitle_agent" ? qsTranslate("Tingmu", "字幕 Agent") : qsTranslate("Tingmu", "经典流程"),
                   card.report.mode === "llm_regroup" ? qsTranslate("Tingmu", "模型智能分句") : qsTranslate("Tingmu", "本地分句"),
                   card.report.backend === "external" ? qsTranslate("Tingmu", "外部 API · 远程转写") :
                       card.report.backend === "local_api" ? qsTranslate("Tingmu", "本机 API · 本机语音服务") :
                       qsTranslate("Tingmu", "内置模型 · 本机运行"),
                   card.report.policy === "off" ? qsTranslate("Tingmu", "停用文本模型") : card.report.policy === "remote" ?
                       qsTranslate("Tingmu", "允许远程文本 API") : qsTranslate("Tingmu", "仅本机 API")].join("  ·  ")
            font.pixelSize: 11
        }
        Repeater {
            model: card.checks
            delegate: ColumnLayout {
                required property var modelData
                required property int index
                visible: card.expanded || index === 0 || modelData.code === "frozenSpec" || modelData.code === "frozenRetry"
                Layout.fillWidth: true; spacing: 3
                Body {
                    objectName: "readinessIssue-" + modelData.code
                    readonly property string description: modelData.value && card.issueText[modelData.code].indexOf("%1") >= 0
                        ? qsTranslate("Tingmu", card.issueText[modelData.code]).arg(modelData.value)
                        : qsTranslate("Tingmu", card.issueText[modelData.code])
                    text: index < card.issues.length ? description
                        : qsTranslate("Tingmu", "若需从音轨识别：%1").arg(description)
                    color: modelData.action ? Theme.warning : Theme.text
                }
                TButton {
                    objectName: "readinessFix-" + modelData.code
                    visible: !!modelData.action
                    enabled: modelData.action !== "checkRuntime" || !runtimeSetup.state.phase
                    Layout.maximumWidth: content.width
                    text: card.buttonLabel(modelData.action)
                    onClicked: card.repair(modelData.action)
                }
            }
        }
        Body {
            visible: !!card.report.source && !(card.report.issues || []).length &&
                     ["asr", "text"].indexOf(card.report.source) >= 0
            color: Theme.accent; text: qsTranslate("Tingmu", "已检查的配置可用。")
        }
        TButton {
            objectName: "readinessExpand"; quiet: true
            visible: card.checks.length > 1
            text: card.expanded ? qsTranslate("Tingmu", "收起检查详情") :
                qsTranslate("Tingmu", "查看全部（%1）").arg(card.checks.length)
            onClicked: card.expanded = !card.expanded
        }
    }
}
