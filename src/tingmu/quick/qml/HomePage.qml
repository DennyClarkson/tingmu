import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Icons.js" as Icons
import "Style.js" as S

Item {
    id: root
    signal settingsRequested(string target)
    readonly property var stateData: studyHome.state
    readonly property string page: stateData.page
    readonly property bool busy: page === "working" || page === "download"
    readonly property bool hasLast: !!stateData.last
    readonly property var repairNames: ({speech: "语音设置", models: "文本模型设置", text: "文本授权", runtime: "运行组件", queue: "查看设置"})
    component Option: Button {
        id: option
        required property string symbol
        property string hint: ""
        Layout.fillWidth: true
        implicitHeight: 104
        hoverEnabled: true
        Accessible.name: text
        background: Rectangle {
            radius: 8
            color: option.hovered || option.activeFocus ? Theme.hover : Theme.panel
            border.color: option.activeFocus ? Theme.accent : Theme.line
        }
        contentItem: RowLayout {
            spacing: 14
            Rectangle {
                Layout.leftMargin: 14
                implicitWidth: 40; implicitHeight: 40; radius: 8
                color: Theme.accentSoft
                Image { anchors.centerIn: parent; width: 21; height: 21; source: Icons.source(option.symbol, Theme.accent) }
            }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 4
                Text { text: option.text; font.pixelSize: 18; font.family: Theme.font; color: Theme.text }
                Text { visible: !!option.hint; text: option.hint; font.pixelSize: 13; color: Theme.muted }
            }
            Image { Layout.rightMargin: 12; width: 15; height: 15; source: Icons.source("chevron-right", Theme.muted) }
        }
    }
    component Caption: Text {
        Layout.fillWidth: true
        color: Theme.muted; font.family: Theme.font; font.pixelSize: 14
        textFormat: Text.PlainText; wrapMode: Text.Wrap
    }
    component DirectionButton: TButton {
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        implicitHeight: 48
        primary: true
        font.pixelSize: 16
    }
    AppDialog {
        id: processing
        objectName: "processingWelcome"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(530, parent.width - 32)
        modal: true
        focus: true
        closePolicy: Popup.NoAutoClose
        visible: ["method", "speechSetup", "textSetup"].includes(root.page)
        readonly property bool local: root.stateData.method === "local"
        readonly property bool speech: root.page === "speechSetup"
        title: root.page === "method" ? "选择字幕处理方式" : speech
               ? (local ? "准备本地语音模型" : "设置语音转写 API") : "设置文本模型"
        contentItem: ColumnLayout {
            spacing: 12
            Option {
                objectName: "chooseLocalProcessing"
                visible: root.page === "method"; symbol: "folder"
                text: "下载语音模型本地处理"; hint: "在本机生成原文字幕，可使用离线词典。"
                onClicked: studyHome.chooseMethod("local")
            }
            Option {
                objectName: "chooseApiProcessing"
                visible: root.page === "method"; symbol: "settings"
                text: "使用 API 密钥"; hint: "连接语音转写服务，再配置讲解和学习伴侣。"
                onClicked: studyHome.chooseMethod("api")
            }
            TButton {
                objectName: "skipProcessing"
                Layout.alignment: Qt.AlignHCenter
                visible: root.page === "method"
                quiet: true
                text: "跳过，稍后设置"
                onClicked: studyHome.chooseMethod("skip")
            }
            Caption {
                visible: root.page !== "method"
                text: processing.speech ? (processing.local
                    ? "选择支持当前语言的模型并下载，已有模型可以直接验证。"
                    : "填写转写地址、语音模型 ID 和密钥，选择带时间戳的格式并授权发送音轨。DeepSeek 的聊天密钥用于下一步文本设置。")
                    : "为逐句讲解、学习伴侣及已开启的字幕复核分配模型。可以共用一个连接。请在当前工作区确认文本使用权限。"
            }
            TButton {
                objectName: "configureProcessing"
                visible: root.page !== "method"
                enabled: !root.stateData.verifying
                text: processing.speech ? (processing.local ? "选择和下载模型" : "填写语音设置") : "配置文本模型和授权"
                onClicked: root.settingsRequested(processing.speech ? "speech" : "text")
            }
            Caption {
                visible: root.page !== "method" && !processing.local
                text: processing.speech ? "验证会向所选服务发送 1 秒生成的静音，服务商可能计费。"
                                        : "验证会向每个所选连接发送一句固定测试文本，服务商可能计费。"
            }
            Caption {
                visible: !!root.stateData.message; text: root.stateData.message
                color: root.stateData.verifying ? Theme.muted : Theme.warning
            }
            TButton {
                visible: root.stateData.repair === "runtime"
                text: "检查运行组件"
                onClicked: root.settingsRequested("runtime")
            }
            RowLayout {
                visible: root.page !== "method"
                Layout.fillWidth: true
                TButton { text: "更换处理方式"; quiet: true; enabled: !root.stateData.verifying; onClicked: studyHome.changeMethod() }
                Item { Layout.fillWidth: true }
                TButton {
                    objectName: "verifyProcessing"; primary: true
                    text: processing.speech && !processing.local ? "验证并继续" : "验证并开始学习"
                    enabled: !root.stateData.verifying && !backend.downloadState.modelDownloading
                    onClicked: studyHome.verifySetup()
                }
            }
        }
    }
    AppDialog {
        id: welcome
        objectName: "homeWelcome"
        property bool custom: false
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(custom ? 440 : 540, root.width - 32)
        padding: 28
        modal: true
        focus: true
        closePolicy: Popup.NoAutoClose
        visible: (root.visible || promptOnLaunch || root.stateData.setupRequired) && root.page === "direction"
        title: custom ? "自定义学习方向" : "你想怎么学？"
        onOpened: defaultStart.forceActiveFocus()
        contentItem: ColumnLayout {
            spacing: 10
            RowLayout {
                Layout.fillWidth: true
                visible: !welcome.custom
                spacing: 12
                DirectionButton {
                    id: defaultStart
                    objectName: "homeCantonese"
                    text: "开始用普通话学粤语"
                    onClicked: studyHome.direction("zh-CN", "yue")
                }
                DirectionButton {
                    objectName: "homeEnglish"
                    text: "开始用普通话学英语"
                    onClicked: studyHome.direction("zh-CN", "en")
                }
            }
            TButton {
                objectName: "homeCustomDirection"
                Layout.alignment: Qt.AlignHCenter
                visible: !welcome.custom
                quiet: true
                text: "自定义学习方向"
                onClicked: welcome.custom = true
            }
            TButton {
                visible: root.stateData.setupRequired && !welcome.custom
                Layout.alignment: Qt.AlignHCenter
                quiet: true; text: "更换处理方式"; onClicked: studyHome.changeMethod()
            }
            WorkspaceLanguage {
                id: known
                objectName: "homeKnownLanguage"
                Layout.fillWidth: true
                visible: welcome.custom
                label: "用哪种语言学"
                code: "zh-CN"
            }
            WorkspaceLanguage {
                id: learning
                objectName: "homeLearningLanguage"
                Layout.fillWidth: true
                visible: welcome.custom
                label: "想学哪种语言"
                code: "yue"
            }
            Caption {
                visible: !!text
                text: root.stateData.message
                color: Theme.warning
            }
            RowLayout {
                Layout.fillWidth: true
                visible: welcome.custom
                TButton { text: "返回"; quiet: true; onClicked: welcome.custom = false }
                Item { Layout.fillWidth: true }
                TButton {
                    objectName: "homeStartCustom"
                    primary: true
                    text: "开始学习"
                    enabled: !!known.code.trim() && !!learning.code.trim()
                    onClicked: studyHome.direction(known.code, learning.code)
                }
            }
        }
    }
    Flickable {
        visible: root.page !== "direction"
        anchors.fill: parent; clip: true
        contentHeight: Math.max(height, panel.implicitHeight + 80)
        ScrollBar.vertical: AppScrollBar {}
        ColumnLayout {
            id: panel
            width: Math.min(root.width - 56, 720)
            x: (root.width - width) / 2
            y: Math.max(32, (root.height - implicitHeight) * 0.42)
            spacing: 16
            TButton {
                visible: ["catalog", "selected", "error"].includes(root.page)
                text: "返回首页"; quiet: true; onClicked: studyHome.home()
            }
            Caption { visible: root.page !== "direction"; text: workspaces.info.languages; color: Theme.accent }
            Text {
                Layout.fillWidth: true
                text: ({home:root.hasLast ? "继续学习" : "今天学什么？",catalog:"视频源",
                        selected:"准备这段视频",download:"正在下载视频",working:"正在准备学习材料",error:"准备遇到了问题"})[root.page] || ""
                color: Theme.text; font.pixelSize: 34; font.family: Theme.font; font.weight: Font.DemiBold
            }
            RowLayout {
                visible: root.page === "home" && root.hasLast; Layout.fillWidth: true; spacing: 12
                ColumnLayout {
                    Layout.fillWidth: true
                    Caption { text: root.stateData.last; color: Theme.text; font.pixelSize: 19; elide: Text.ElideMiddle; maximumLineCount: 1 }
                    Caption { text: "上次播放至 " + S.time(root.stateData.position) }
                }
                TButton { objectName: "homeResume"; text: "继续学习"; iconSource: "play"; primary: true; horizontalPadding: 18; implicitHeight: 44; onClicked: studyHome.resume() }
            }
            RowLayout {
                visible: root.page === "home"; Layout.fillWidth: true; spacing: 14
                Option { objectName: "homeLocal"; text: "本地视频"; hint: "选择或拖入文件"; symbol: "folder"; onClicked: studyHome.browse() }
                Option { objectName: "homeCatalog"; text: "视频源"; hint: "浏览当前方向的片单"; symbol: "play"; onClicked: studyHome.catalog() }
            }
            ColumnLayout {
                visible: root.page === "catalog"; Layout.fillWidth: true; spacing: 12
                Caption { visible: !!text; text: root.stateData.sourceUrl || ""; color: Theme.muted }
                Repeater {
                    model: studyHome.sources
                    delegate: RowLayout {
                        required property var modelData
                        Layout.fillWidth: true; spacing: 18
                        ColumnLayout {
                            Layout.fillWidth: true
                            Caption { text: modelData.title; color: Theme.text; font.pixelSize: 22 }
                            Caption { text: (modelData.language === "en" ? "英语" : "粤语") + " · " + modelData.duration }
                            Caption { text: modelData.credit; font.pixelSize: 12 }
                        }
                        TButton {
                            objectName: "homeDownload_" + modelData.id
                            text: "下载并准备"; primary: true; horizontalPadding: 18; implicitHeight: 44
                            onClicked: studyHome.download(modelData.id)
                        }
                    }
                }
                Caption { visible: studyHome.sources.length === 0; text: "当前方向暂无片源，可返回选择本地视频。" }
            }
            Caption {
                visible: root.busy || root.page === "selected" || root.page === "error"
                text: root.stateData.name; color: Theme.text; font.pixelSize: 18
            }
            ProgressBar {
                Layout.fillWidth: true; visible: root.busy; indeterminate: visible
                palette.highlight: Theme.accent
                Accessible.name: root.stateData.message
            }
            Caption { visible: !!text; text: root.stateData.message; color: root.page === "error" ? Theme.warning : Theme.muted }
            RowLayout {
                visible: root.page === "selected" || root.page === "error"
                TButton {
                    objectName: "homePrepare"; primary: true
                    text: root.page === "selected" ? "一键准备" : root.stateData.repair === "download" ? "重试下载" : "继续准备"
                    horizontalPadding: 20; implicitHeight: 44
                    onClicked: root.page === "error" ? studyHome.retry() : studyHome.prepare()
                }
                TButton {
                    objectName: "homeRepairSettings"; visible: !!root.repairNames[root.stateData.repair] && root.page === "error"
                    text: root.repairNames[root.stateData.repair] || ""
                    onClicked: root.settingsRequested(root.stateData.repair === "queue" ? "workspace" : root.stateData.repair)
                }
            }
        }
    }
}
