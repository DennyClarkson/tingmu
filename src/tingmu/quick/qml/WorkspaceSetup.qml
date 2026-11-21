import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

AppDialog {
    id: root
    objectName: "workspaceSetup"
    signal modelsRequested()
    anchors.centerIn: parent
    width: Math.min(850, parent.width - 32)
    height: Math.min(660, parent.height - 20)
    title: "学习工作区"
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
    component Hint: Text {
        Layout.fillWidth: true; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12
        wrapMode: Text.Wrap; textFormat: Text.PlainText
    }
    component SectionTitle: Text {
        Layout.fillWidth: true; color: Theme.text; font.family: Theme.font; font.pixelSize: 16
        font.weight: Font.DemiBold; textFormat: Text.PlainText
    }
    contentItem: ColumnLayout {
        spacing: 12
        RowLayout {
            Layout.fillHeight: true; Layout.fillWidth: true; spacing: 20
            ColumnLayout {
                Layout.preferredWidth: 176; Layout.fillHeight: true; spacing: 8
                SectionTitle { text: "我的工作区" }
                ListView {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    clip: true; spacing: 4
                    model: root.current.items
                    delegate: WorkspaceRow {
                        width: ListView.view.width
                        currentWorkspace: modelData.id === root.current.id && !root.creating
                        onClicked: {
                            if (modelData.id === root.current.id) root.activatePage()
                            else workspaces.action("switch", {id: modelData.id})
                        }
                    }
                }
                TButton {
                    objectName: "workspaceNew"; Layout.fillWidth: true; text: "＋ 新建工作区"
                    onClicked: root.beginCreate()
                }
                TButton { Layout.fillWidth: true; text: "刷新本机工作区"; onClicked: workspaces.action("discover", {}) }
            }
            Rectangle { Layout.fillHeight: true; width: 1; color: Theme.line }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12
                SectionTitle { text: root.creating ? "新建学习工作区" : root.current.name }
                RowLayout {
                    visible: !root.creating
                    TButton { text: "语言与文件"; checked: root.section === "general"; onClicked: root.section = "general" }
                    TButton { objectName: "workspaceModelsTab"; text: "语音识别"; checked: root.section === "models"; onClicked: root.section = "models" }
                    TButton { objectName: "workspaceTextTab"; text: "文本模型与 API"; checked: root.section === "text"; onClicked: root.section = "text" }
                }
                ScrollView {
                    id: scroll
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
                    ColumnLayout {
                        width: scroll.availableWidth; spacing: 12
                        ColumnLayout {
                            visible: root.creating; Layout.fillWidth: true; spacing: 6
                            Hint { text: "工作区名称" }
                            Field { id: workspaceName; objectName: "workspaceName"; Layout.fillWidth: true; maximumLength: 80; placeholderText: "例如：港剧听力" }
                            Toggle { id:reuseText;objectName:"reuseWorkspaceText";visible:!!root.reuseOffer.signature;text:"沿用当前工作区的文本模型" }
                            Hint {
                                text: reuseText.checked ? root.reuseOffer.roles.map(r=>r.role+" · "+r.model+" · "+r.host).join("\n") + "\n将按以上用途向所列服务发送学习文本。"
                                    : "只准备原文字幕。创建后可在「文本模型与 API」分配讲解模型，再生成逐句讲解和译稿。"
                            }
                        }
                        ColumnLayout {
                            visible: root.creating || root.section === "general"; Layout.fillWidth: true; spacing: 10
                            RowLayout {
                                Layout.fillWidth: true; spacing: 14
                                WorkspaceLanguage { id: known; objectName: "knownLanguage"; Layout.fillWidth: true; label: "用哪种语言学" }
                                WorkspaceLanguage { id: learning; objectName: "learningLanguage"; Layout.fillWidth: true; label: "想学哪种语言" }
                            }
                            TButton {
                                visible: !root.creating
                                enabled: known.code !== backend.settings.source_language || learning.code !== backend.settings.target_language
                                text: "保存语言"; onClicked: workspaces.action("languages", {source: known.code, target: learning.code})
                            }
                            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
                            RowLayout {
                                Layout.fillWidth: true
                                SectionTitle { text: "文件位置" }
                                TButton { visible: !root.creating; text: "打开文件夹"; onClicked: workspaces.action("folder", {}) }
                            }
                            Hint { text: root.creating ? root.current.defaultParent + "/" + workspaceName.text : root.current.path; wrapMode: Text.WrapAnywhere }
                            RowLayout {
                                visible: !root.creating; Layout.fillWidth: true
                                Toggle { Layout.fillWidth: true; text: "自动准备放入 media 的视频"
                                    checked: !!backend.settings.auto_prepare_media
                                    onToggled: backend.setting("auto_prepare_media", checked) }
                                TButton { text: "打开视频目录"; onClicked: workspaces.action("mediaFolder", {}) }
                            }
                            Hint { text: "media/　导入的视频和随附字幕\ndocuments/　生成的字幕和逐句学习材料\nnotebooks/　分语言整理的 Markdown 笔记\nexports/　默认导出位置\nstudy.db　收藏、对话和播放记录"; lineHeight: 1.45 }
                            Hint { visible:!root.creating; text:backend.lessonModel.configured ? "已配置逐句讲解与译稿核验模型。" : "当前只准备原文字幕。可在「文本模型与 API」分配逐句讲解模型。" }
                        }
                        ColumnLayout {
                            visible: !root.creating && root.section === "models"; Layout.fillWidth: true; spacing: 10
                            Hint { text: root.cantonese
                                ? "粤语原声可选 Qwen3-ASR，下载时会一并取得对齐模型。Whisper 可选 large-v3-turbo 或 large-v3；small 不支持指定粤语。"
                                : "多语种识别可先试 Whisper small。选择 Qwen 或转写接口前，请确认模型支持当前学习语言 " + backend.settings.target_language + "。" }
                            SpeechSettings { Layout.fillWidth: true }
                            ColumnLayout {
                                visible: backend.settings.asr_backend === "local"; Layout.fillWidth: true; spacing: 7
                                Hint {
                                    text: root.current && workspaces.readinessFor(backend.settings.asr_model)
                                    color: Theme.accent
                                }
                                Hint { text: "设备选 cpu 可用处理器识别；有兼容的 NVIDIA GPU 可选 cuda。auto 会根据运行环境选择设备。\n同一模型供所有工作区共用，缓存位于 " + root.current.cache; wrapMode: Text.WrapAnywhere }
                                RowLayout {
                                    TButton { objectName: "workspaceDownload"; text: "下载所选模型"; visible: !String(backend.settings.asr_model).startsWith("local:"); enabled: !backend.state.busy; onClicked: workspaces.action("download", {}) }
                                    TButton { text: "打开共享模型目录"; visible: String(backend.settings.asr_model).startsWith("local:"); onClicked: workspaces.action("modelFolder", {}) }
                                    TButton { text: "查看缓存"; quiet: true; onClicked: workspaces.action("cache", {}) }
                                }
                            }
                            Hint { text: workspaces.speechStatus }
                            RowLayout {
                                TButton { text: "下载人声分离与换人检测模型"; enabled: !backend.state.busy; onClicked: workspaces.action("downloadSpeech", {}) }
                                TButton { text: "暂停下载"; visible: !!backend.state.modelDownloading; onClicked: workspaces.action("stopDownload", {}) }
                            }
                        }
                        ColumnLayout {
                            visible: !root.creating && root.section === "text"; Layout.fillWidth: true; spacing: 11
                            SectionTitle { text: "复核、讲解与对话" }
                            Hint { text: "逐句讲解模型为每句准备讲解，再核对译稿。可选择本机 API 或允许的远程连接。" }
                            Repeater {
                                model: [{label: "字幕文本复核", field: "review_model"}, {label: "逐句讲解", field: "model"}, {label: "学习对话", field: "companion_model"}]
                                delegate: RowLayout {
                                    required property var modelData
                                    Layout.fillWidth: true
                                    Hint { Layout.preferredWidth: 112; Layout.fillWidth: false; text: modelData.label }
                                    Hint { text: backend.settings[modelData.field] || "待配置"; color: Theme.text }
                                }
                            }
                            TextModelPolicy { Layout.fillWidth: true }
                            LlmConcurrencySettings { Layout.fillWidth: true }
                            TButton { text: "管理文本连接与任务分配…"; primary: true; onClicked: root.modelsRequested() }
                        }
                    }
                }
            }
        }
        Hint {
            visible: !!text; color: (root.current.error || backend.state.error) ? Theme.error : Theme.accent
            text: root.current.error || backend.state.error || (root.section === "models" ? backend.state.status : "")
        }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            TButton { visible: root.creating; text: "取消"; onClicked: root.activatePage() }
            TButton {
                objectName: "workspaceContinue"; primary: true
                text: root.creating ? "创建工作区" : "完成"
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
