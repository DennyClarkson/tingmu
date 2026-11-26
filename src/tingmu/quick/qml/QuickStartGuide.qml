import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

AppDialog {
    id: guide
    objectName: "helpDialog"
    signal navigate(string target)
    property string page: "start"
    title: qsTranslate("Tingmu", "快速入门")
    anchors.centerIn: parent
    width: Math.min(810, parent ? parent.width - 28 : 810)
    height: Math.min(650, parent ? parent.height - 24 : 650)
    modal: true
    standardButtons: Dialog.Close
    function go(target) {
        close()
        navigate(target)
    }
    component Para: Text {
        Layout.fillWidth: true; Layout.minimumWidth: 0
        wrapMode: Text.Wrap; textFormat: Text.PlainText
        color: Theme.muted; font.family: Theme.font; font.pixelSize: 13
    }
    component Heading: Para {
        font.family: Theme.serif; font.pixelSize: 17; font.weight: Font.DemiBold; color: Theme.text
    }
    component Card: Rectangle {
        id: tile
        required property string heading
        required property string detail
        required property string buttonText
        required property string target
        Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumWidth: 0
        implicitHeight: tileContent.implicitHeight + 26
        radius: Theme.radiusMd; color: Theme.bg; border.color: Theme.line
        ColumnLayout {
            id: tileContent
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.margins: 13; spacing: 6
            Heading { text: tile.heading; font.pixelSize: 15 }
            Para { text: tile.detail; font.pixelSize: 12 }
            TButton {
                objectName: "guideAction-" + tile.target; text: tile.buttonText
                Layout.maximumWidth: tileContent.width
                tip: tile.heading; onClicked: guide.go(tile.target)
            }
        }
    }
    component Step: RowLayout {
        required property string number
        required property string heading
        required property string detail
        required property string buttonText
        required property string target
        id: step
        Layout.fillWidth: true; spacing: 10
        StatusBadge { text: step.number; tone: "accent"; Layout.alignment: Qt.AlignTop }
        ColumnLayout {
            Layout.fillWidth: true; spacing: 2
            Heading { font.pixelSize: 14; text: step.heading }
            Para { text: step.detail; font.pixelSize: 12 }
        }
        TButton {
            objectName: "guideStep-" + step.target; text: step.buttonText
            Layout.maximumWidth: guide.width * 0.30
            onClicked: guide.go(step.target)
        }
    }

    contentItem: FirstUseLoader {
        requested: guide.visible
        sourceComponent: ColumnLayout {
            Connections {
                target: guide
                function onPageChanged() { if (scroll.contentItem) scroll.contentItem.contentY = 0 }
            }
            spacing: 12
            Flow {
                Layout.fillWidth: true; spacing: 5
                TButton {
                    objectName: "guideStartTab"; text: qsTranslate("Tingmu", "快速入门"); checked: guide.page === "start"
                    onClicked: guide.page = "start"
                }
                TButton {
                    objectName: "guideSettingsTab"; text: qsTranslate("Tingmu", "三处设置各管什么"); checked: guide.page === "settings"
                    onClicked: guide.page = "settings"
                }
                TButton {
                    objectName: "guideShortcutsTab"; text: qsTranslate("Tingmu", "快捷键与浮动面板"); checked: guide.page === "keys"
                    onClicked: guide.page = "keys"
                }
            }
            AppScrollView {
                id: scroll
                Layout.fillWidth: true; Layout.fillHeight: true
                clip: true; rightPadding: 10; contentWidth: availableWidth
                ColumnLayout {
                    width: scroll.availableWidth; spacing: 10
                    Heading { visible: guide.page === "start"; text: qsTranslate("Tingmu", "选择你想做的事") }
                    GridLayout {
                        visible: guide.page === "start"; Layout.fillWidth: true
                        columns: scroll.availableWidth >= 630 ? 3 : 1
                        uniformCellWidths: true; columnSpacing: 8; rowSpacing: 6
                        Card {
                            heading: qsTranslate("Tingmu", "只看视频")
                            detail: qsTranslate("Tingmu", "播放视频和外挂字幕无需下载组件或配置模型。打开视频，在「文件」菜单导入已有字幕。")
                            buttonText: qsTranslate("Tingmu", "进入观看"); target: "watch"
                        }
                        Card {
                            heading: qsTranslate("Tingmu", "用已有字幕学习")
                            detail: qsTranslate("Tingmu", "字幕 Agent 可复用原文与已有译文。双语精讲需要全局讲解模型和本工作区的文本权限；读取内嵌字幕需要 FFmpeg。")
                            buttonText: qsTranslate("Tingmu", "进入准备工作室"); target: "prepare"
                        }
                        Card {
                            heading: qsTranslate("Tingmu", "从音轨生成字幕")
                            detail: qsTranslate("Tingmu", "用 FFmpeg 提取音轨，通过本机引擎和所选模型权重，或转写 API 生成字幕。讲解另需文本模型，多种语言/方言的对白可选智能分句。")
                            buttonText: qsTranslate("Tingmu", "选择语音服务"); target: "speech"
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; visible: guide.page === "start"; spacing: 10
                        Heading { text: qsTranslate("Tingmu", "从视频到学习材料") }
                        Step {
                            number: "1"; heading: qsTranslate("Tingmu", "语言/方言与文件")
                            detail: qsTranslate("Tingmu", "在工作区选择已知语言/方言和学习语言/方言，再打开或拖入视频。")
                            buttonText: qsTranslate("Tingmu", "打开视频"); target: "open"
                        }
                        Step {
                            number: "2"; heading: qsTranslate("Tingmu", "准备字幕")
                            detail: qsTranslate("Tingmu", "在媒体库勾选视频，选择流程和分句方式，加入队列并点击开始。")
                            buttonText: qsTranslate("Tingmu", "处理队列"); target: "queue"
                        }
                        Step {
                            number: "3"; heading: qsTranslate("Tingmu", "逐句学习")
                            detail: qsTranslate("Tingmu", "在观看页点击字幕定位并重听单句；在学习页收藏词句，打开对话追问当前句。")
                            buttonText: qsTranslate("Tingmu", "进入学习"); target: "learn"
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; visible: guide.page === "settings"; spacing: 8
                        Card {
                            heading: qsTranslate("Tingmu", "工作区")
                            detail: qsTranslate("Tingmu", "选择语言/方言、文件位置、语音后端；分别决定本工作区的文本权限和音轨上传授权。")
                            buttonText: qsTranslate("Tingmu", "学习工作区"); target: "workspace"
                        }
                        Card {
                            heading: qsTranslate("Tingmu", "全局模型库")
                            detail: qsTranslate("Tingmu", "文本 API 的连接和凭据只需配置一次。各工作区共享字幕复核、逐句讲解、学习对话的模型分配，远程权限在各工作区单独授权。")
                            buttonText: qsTranslate("Tingmu", "全局模型"); target: "models"
                        }
                        Card {
                            heading: qsTranslate("Tingmu", "运行环境")
                            detail: qsTranslate("Tingmu", "检查 FFmpeg、Whisper 或 Qwen 引擎。CUDA 用于可选加速。模型权重在工作区语音设置中选择和下载，各工作区共享已下载的文件。")
                            buttonText: runtimeSetup.available ? qsTranslate("Tingmu", "检查运行环境") : qsTranslate("Tingmu", "语音设置")
                            target: "runtime"
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; visible: guide.page === "keys"; spacing: 10
                        Heading { text: qsTranslate("Tingmu", "播放、静音、定位") }
                        Para { text: qsTranslate("Tingmu", "空格 播放或暂停　·　M 静音　·　← / → 前后 5 秒　·　↑ / ↓ 上一句或下一句") }
                        Para { text: qsTranslate("Tingmu", "F 全屏　·　Esc 退出全屏　·　Ctrl+O 打开视频　·　Ctrl+Shift+O 打开文件夹　·　Ctrl+Enter 发送对话") }
                        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
                        Para { text: qsTranslate("Tingmu", "全屏时可直接拖动字幕调整位置。拖动面板标题或点击右上箭头可浮动面板，点击放回按钮可归位。拖动分隔线可调整面板大小。") }
                        TButton {
                            objectName: "guideResetLayout"; text: qsTranslate("Tingmu", "恢复默认布局"); onClicked: guide.go("reset")
                        }
                    }
                }
            }
        }
    }
}
