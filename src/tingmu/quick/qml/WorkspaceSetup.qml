import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S

AppDialog {
    id: root
    objectName: "workspaceSetup"
    signal modelsRequested()
    anchors.centerIn: parent
    width: Math.min(850, parent.width - 32)
    height: Math.min(660, parent.height - 20)
    title: qsTranslate("Tingmu", "学习工作区")
    modal: true
    closePolicy: Popup.NoAutoClose
    property bool creating: false
    property bool openAsCreate: false
    property bool openForLessons: false
    function openTextSettings(){if(opened){creating=false;section="text"}else{openForLessons=true;open()}}
    property string savedSource: ""
    property string savedTarget: ""
    property string section: "general"
    property var reuseOffer: ({roles:[], signature:""})
    readonly property bool cantonese: /^(yue(?:[-_]|$)|zh-hk$|cantonese$)/i.test(backend.settings.target_language)
    property var current: workspaces.info
    function activatePage() {
        creating = false
        section = "general"
        savedSource = backend.settings.source_language
        savedTarget = backend.settings.target_language
        known.code = savedSource
        learning.code = savedTarget
    }
    Connections {
        target: backend
        function onSettingsChanged() {
            if (!root.opened || root.creating) return
            if (known.code === root.savedSource) known.code = backend.settings.source_language
            if (learning.code === root.savedTarget) learning.code = backend.settings.target_language
            root.savedSource = backend.settings.source_language
            root.savedTarget = backend.settings.target_language
        }
    }
    property bool initialOpen: true
    onOpened: {
        activatePage()
        if(openAsCreate) { beginCreate(); openAsCreate = false }
        else if(openForLessons){root.section="text";openForLessons=false}
        else if(initialOpen && root.current.showSetup === "models") root.section = "models"
        initialOpen = false
    }
    function beginCreate() {
        creating = true
        reuseOffer = workspaces.textReuseOffer()
        reuseText.checked = false
        workspaceName.text = ""
        known.code = backend.settings.source_language
        learning.code = backend.settings.target_language
        workspaceName.forceActiveFocus()
    }
    function openCreate() { openAsCreate = true; open() }
    function confirmDownload(kind) {
        var plan = workspaces.downloadPlan(kind)
        if (plan.kind) {
            manualDownload.plan = plan
            manualDownload.open()
        }
    }
    AppDialog {
        id: manualDownload
        objectName: "manualModelDownload"
        property var plan: ({})
        title: plan.installed ? qsTranslate("Tingmu", "模型已就绪") : qsTranslate("Tingmu", "确认获取模型")
        anchors.centerIn: parent; width: Math.min(410, root.width - 30); modal: true
        contentItem: ColumnLayout {
            Hint { text: qsTranslate("TingmuStatus", manualDownload.plan.label || ""); color: Theme.text }
            Hint { text: qsTranslate("Tingmu", "模型包总量约 ") + S.bytes(manualDownload.plan.packageBytes) }
            Hint { text: manualDownload.plan.bytes > 0 ? qsTranslate("Tingmu", "预计下载 ") + S.bytes(manualDownload.plan.bytes)
                      + qsTranslate("Tingmu", "。已缓存的文件会复用，模型供所有工作区使用。")
                      : qsTranslate("Tingmu", "所需下载量为 0 B，使用共享缓存中的模型文件。") }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                TButton { text: manualDownload.plan.installed ? qsTranslate("Tingmu", "关闭") : qsTranslate("Tingmu", "取消"); onClicked: manualDownload.close() }
                TButton {
                    objectName: "approveManualDownload"; visible: !manualDownload.plan.installed; primary: true
                    text: manualDownload.plan.bytes > 0 ? qsTranslate("Tingmu", "确认下载") : qsTranslate("Tingmu", "从缓存安装")
                    onClicked: {
                        workspaces.action(manualDownload.plan.kind, manualDownload.plan)
                        manualDownload.close()
                    }
                }
            }
        }
    }
    component Hint: Text {
        Layout.fillWidth: true; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12
        wrapMode: Text.Wrap; textFormat: Text.PlainText
    }
    component SectionTitle: Text {
        Layout.fillWidth: true; color: Theme.text; font.family: Theme.serif; font.pixelSize: 19
        font.weight: Font.DemiBold; textFormat: Text.PlainText
    }
    contentItem: ColumnLayout {
        spacing: 12
        RowLayout {
            Layout.fillHeight: true; Layout.fillWidth: true; spacing: 20
            ColumnLayout {
                Layout.preferredWidth: 182; Layout.minimumWidth: 162; Layout.fillHeight: true; spacing: 8
                SectionTitle { text: qsTranslate("Tingmu", "我的工作区") }
                ListView {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    clip: true; spacing: 4
                    ScrollBar.vertical: AppScrollBar {}
                    model: root.current.items
                    delegate: WorkspaceRow {
                        width: Math.max(0, ListView.view.width - 9)
                        currentWorkspace: modelData.id === root.current.id && !root.creating
                        onClicked: {
                            if (modelData.id === root.current.id) root.activatePage()
                            else workspaces.action("switch", {id: modelData.id})
                        }
                    }
                }
                TButton {
                    objectName: "workspaceNew"; Layout.fillWidth: true; text: qsTranslate("Tingmu", "＋ 新建工作区")
                    onClicked: root.beginCreate()
                }
                TButton { Layout.fillWidth: true; text: qsTranslate("Tingmu", "刷新本机工作区"); onClicked: workspaces.action("discover", {}) }
            }
            Rectangle { Layout.fillHeight: true; width: 1; color: Theme.line }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
                SectionTitle { text: root.creating ? qsTranslate("Tingmu", "新建学习工作区") : (root.current.id === "default" ? qsTranslate("Tingmu", root.current.name) : root.current.name) }
                SegmentBar {
                    visible: !root.creating
                    TButton { text: qsTranslate("Tingmu", "语言与文件"); quiet:true; checked: root.section === "general"; onClicked: root.section = "general" }
                    TButton { objectName: "workspaceModelsTab"; text: qsTranslate("Tingmu", "语音识别"); quiet:true; checked: root.section === "models"; onClicked: root.section = "models" }
                    TButton { objectName: "workspaceTextTab"; text: qsTranslate("Tingmu", "文本模型与 API"); quiet:true; checked: root.section === "text"; onClicked: root.section = "text" }
                }
                AppScrollView {
                    id: scroll
                    rightPadding: 10
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
                    ColumnLayout {
                        width: scroll.availableWidth; spacing: 12
                        ColumnLayout {
                            visible: root.creating; Layout.fillWidth: true; spacing: 6
                            Hint { text: qsTranslate("Tingmu", "工作区名称") }
                            Field { id: workspaceName; objectName: "workspaceName"; Layout.fillWidth: true; maximumLength: 80; placeholderText: qsTranslate("Tingmu", "例如：港剧听力") }
                            Toggle { id:reuseText;objectName:"reuseWorkspaceText";visible:root.reuseOffer.roles.some(r=>!r.local);text:qsTranslate("Tingmu", "允许此工作区使用全局分配的远程文本模型") }
                            Hint {
                                text: reuseText.checked ? root.reuseOffer.roles.map(r=>qsTranslate("TingmuStatus", r.role || "")+" · "+r.model+" · "+r.host).join("\n") + qsTranslate("Tingmu", "\n将按以上用途向所列服务发送学习文本。")
                                    : qsTranslate("Tingmu", "新工作区可使用全局分配的本机模型。使用远程文本服务需要在此工作区授权。")
                            }
                        }
                        ColumnLayout {
                            visible: root.creating || root.section === "general"; Layout.fillWidth: true; spacing: 10
                            RowLayout {
                                Hint { text: qsTranslate("Tingmu", "界面语言 / Language") }
                                Choice {
                                    objectName: "workspaceInterfaceLanguage"
                                    model: ["简体中文", "繁體中文", "English"]
                                    readonly property var codes: ["zh-CN", "zh-TW", "en"]
                                    currentIndex: codes.indexOf(interfaceLanguage.code)
                                    onActivated: interfaceLanguage.select(codes[currentIndex])
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 14
                                WorkspaceLanguage { id: known; objectName: "knownLanguage"; Layout.fillWidth: true; label: qsTranslate("Tingmu", "用哪种语言学") }
                                WorkspaceLanguage { id: learning; objectName: "learningLanguage"; Layout.fillWidth: true; label: qsTranslate("Tingmu", "想学哪种语言") }
                            }
                            TButton {
                                visible: !root.creating
                                enabled: known.code !== backend.settings.source_language || learning.code !== backend.settings.target_language
                                text: qsTranslate("Tingmu", "保存语言"); onClicked: workspaces.action("languages", {source: known.code, target: learning.code})
                            }
                            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
                            RowLayout {
                                Layout.fillWidth: true
                                SectionTitle { text: qsTranslate("Tingmu", "文件位置") }
                                TButton { visible: !root.creating; text: qsTranslate("Tingmu", "打开文件夹"); onClicked: workspaces.action("folder", {}) }
                            }
                            Hint { text: root.creating ? root.current.defaultParent + "/" + workspaceName.text : root.current.path; wrapMode: Text.WrapAnywhere }
                            RowLayout {
                                visible: !root.creating; Layout.fillWidth: true
                                Toggle { Layout.fillWidth: true; text: qsTranslate("Tingmu", "自动准备放入 media 的视频")
                                    checked: !!backend.settings.auto_prepare_media
                                    onToggled: backend.setting("auto_prepare_media", checked) }
                                TButton { text: qsTranslate("Tingmu", "打开视频目录"); onClicked: workspaces.action("mediaFolder", {}) }
                            }
                            Hint { text: qsTranslate("Tingmu", "media/　导入的视频和随附字幕\ndocuments/　生成的字幕和逐句学习材料\nnotebooks/　分语言整理的 Markdown 笔记\nexports/　默认导出位置\nstudy.db　收藏、对话和播放记录"); lineHeight: 1.45 }
                            Hint { visible:!root.creating; text:backend.lessonModel.configured ? qsTranslate("Tingmu", "已配置逐句讲解与译稿核验模型。") : qsTranslate("Tingmu", "当前只准备原文字幕。可在「文本模型与 API」分配逐句讲解模型。") }
                        }
                        ColumnLayout {
                            visible: !root.creating && root.section === "models"; Layout.fillWidth: true; spacing: 10
                            Hint { text: root.cantonese
                                ? qsTranslate("Tingmu", "粤语原声可选 Qwen3-ASR，下载时会一并取得对齐模型。Whisper 可选 large-v3-turbo 或 large-v3；small 不支持指定粤语。")
                                : qsTranslate("Tingmu", "多语种识别可先试 Whisper small。选择 Qwen 或转写接口前，请确认模型支持当前学习语言 ") + backend.settings.target_language + "。" }
                            SpeechSettings { Layout.fillWidth: true }
                            ColumnLayout {
                                visible: backend.settings.asr_backend === "local"; Layout.fillWidth: true; spacing: 7
                                Hint {
                                    text: root.current && qsTranslate("TingmuStatus", workspaces.readinessFor(backend.settings.asr_model) || "")
                                    color: Theme.accent
                                }
                                Hint { text: qsTranslate("Tingmu", "设备选 cpu 可用处理器识别；有兼容的 NVIDIA GPU 可选 cuda。auto 会根据运行环境选择设备。\n同一模型供所有工作区共用，缓存位于 ") + root.current.cache; wrapMode: Text.WrapAnywhere }
                                RowLayout {
                                    TButton { objectName: "workspaceDownload"; text: qsTranslate("Tingmu", "下载所选模型"); visible: !String(backend.settings.asr_model).startsWith("local:"); enabled: !backend.applicationState.busy; onClicked: root.confirmDownload("download") }
                                    TButton { text: qsTranslate("Tingmu", "打开共享模型目录"); visible: String(backend.settings.asr_model).startsWith("local:"); onClicked: workspaces.action("modelFolder", {}) }
                                    TButton { text: qsTranslate("Tingmu", "查看缓存"); quiet: true; onClicked: workspaces.action("cache", {}) }
                                }
                            }
                            Hint { text: workspaces.speechStatus.split(" · ").map(label => qsTranslate("Tingmu", label)).join(" · ") }
                            RowLayout {
                                TButton { text: qsTranslate("Tingmu", "下载人声分离与换人检测模型"); enabled: !backend.applicationState.busy; onClicked: root.confirmDownload("downloadSpeech") }
                                TButton { text: qsTranslate("Tingmu", "暂停下载"); visible: !!backend.downloadState.modelDownloading; onClicked: workspaces.action("stopDownload", {}) }
                            }
                            DownloadProgress { visible: !!backend.downloadState.modelDownloading; Layout.fillWidth: true }
                        }
                        ColumnLayout {
                            visible: !root.creating && root.section === "text"; Layout.fillWidth: true; spacing: 11
                            SectionTitle { text: qsTranslate("Tingmu", "全局任务分配") }
                            Hint { text: qsTranslate("Tingmu", "逐句讲解模型为每句准备讲解，再核对译稿。可选择本机 API 或允许的远程连接。") }
                            Repeater {
                                model: [{label: qsTranslate("Tingmu", "字幕文本复核"), field: "review_model"}, {label: qsTranslate("Tingmu", "逐句讲解"), field: "model"}, {label: qsTranslate("Tingmu", "学习对话"), field: "companion_model"}]
                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Hint { Layout.preferredWidth: 112; Layout.fillWidth: false; text: modelData.label }
                                    Hint { text: backend.settings[modelData.field] || qsTranslate("Tingmu", "待配置"); color: Theme.text }
                                }
                            }
                            TextModelPolicy { Layout.fillWidth: true }
                            LlmConcurrencySettings { Layout.fillWidth: true }
                            TButton { text: qsTranslate("Tingmu", "管理全局连接与任务分配…"); primary: true; onClicked: root.modelsRequested() }
                        }
                    }
                }
            }
        }
        Hint {
            visible: !!text; color: (root.current.error || backend.workspaceState.error) ? Theme.error : Theme.accent
            text: qsTranslate("TingmuStatus", root.current.error || backend.workspaceState.error || (root.section === "models" ? backend.workspaceState.status : "") || "")
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            TButton { visible: root.creating; text: qsTranslate("Tingmu", "取消"); onClicked: root.activatePage() }
            TButton {
                objectName: "workspaceContinue"; primary: true
                text: root.creating ? qsTranslate("Tingmu", "创建工作区") : qsTranslate("Tingmu", "完成")
                onClicked: {
                    if (root.creating) {
                        workspaces.action("create", {name: workspaceName.text, source: known.code, target: learning.code,
                            textReuseSignature: reuseText.checked ? root.reuseOffer.signature : ""})
                    } else if (workspaces.action("languages", {source: known.code, target: learning.code}) && workspaces.action("enter", {})) root.close()
                }
            }
        }
    }
}
