import QtQuick
import QtQuick.Layouts

ColumnLayout {
    Layout.fillWidth: true
    spacing: 4
    RowLayout {
        Layout.fillWidth: true
        Text {
            text: qsTranslate("Tingmu", "新任务流程")
            color: Theme.text; font.family: Theme.font; font.pixelSize: 12
        }
        Choice {
            objectName: "preparationWorkflowChoice"
            Layout.fillWidth: true
            translateOptions: true
            model: ["经典流程", "字幕 Agent"]
            currentIndex: backend.settings.preparation_workflow === "subtitle_agent" ? 1 : 0
            onActivated: backend.setting("preparation_workflow", currentIndex === 1 ? "subtitle_agent" : "classic")
        }
    }
    TButton {
        objectName: "agentAttachSubtitle"
        Layout.fillWidth: true
        visible: backend.settings.preparation_workflow === "subtitle_agent"
        enabled: !!backend.state.mediaPath
        text: qsTranslate("Tingmu", "为当前视频选字幕并入队…")
        tip: backend.state.mediaName || qsTranslate("Tingmu", "先打开一个视频")
        onClicked: backend.command("importStudySubtitle", {})
    }
}
