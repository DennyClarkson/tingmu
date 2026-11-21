import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: root
    readonly property var counts: backend.state.lessonCounts || ({total:0, taught:0, verified:0})
    readonly property bool ready: counts.total > 0 && counts.verified === counts.total
    property bool showWhenReady: true
    visible: counts.total > 0 && (showWhenReady || !ready)
    Layout.fillWidth: true
    spacing: 4
    Text {
        Layout.fillWidth: true
        textFormat: Text.PlainText; wrapMode: Text.Wrap
        color: Theme.muted; font.family: Theme.font; font.pixelSize: 12
        text: "逐句讲解 " + root.counts.taught + "/" + root.counts.total
              + "   ·   " + backend.lessonModel.language + "译稿已复核 " + root.counts.verified + "/" + root.counts.total
    }
    TButton {
        objectName: "prepareLessonButton"
        visible: !root.ready
        enabled: !backend.state.queueRunning
        text: backend.state.queueRunning ? "正在准备…" : backend.lessonModel.configured ? "补全讲解与译稿" : "配置逐句讲解模型…"
        onClicked: backend.command(backend.lessonModel.configured ? "prepareLessons" : "openLessonSettings", {})
    }
}
