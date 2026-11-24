import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: root
    readonly property var counts: backend.mediaState.lessonCounts || ({total:0, taught:0, verified:0})
    readonly property bool ready: counts.total > 0 && counts.verified + (counts.provided || 0) === counts.total
    property bool showWhenReady: true
    visible: counts.total > 0 && (showWhenReady || !ready)
    Layout.fillWidth: true
    spacing: 4
    Text {
        Layout.fillWidth: true
        textFormat: Text.PlainText; wrapMode: Text.Wrap
        color: Theme.muted; font.family: Theme.font; font.pixelSize: 12
        text: qsTranslate("Tingmu", "逐句讲解 ") + root.counts.taught + "/" + root.counts.total
              + "   ·   " + qsTranslate("TingmuLanguage", backend.lessonModel.language || "") + qsTranslate("Tingmu", "译稿已复核 ") + root.counts.verified + "/" + root.counts.total
              + (root.counts.provided ? "   ·   " + qsTranslate("Tingmu", "字幕自带译文 ") + root.counts.provided : "")
    }
    TButton {
        objectName: "prepareLessonButton"
        visible: !root.ready
        enabled: !backend.queueState.queueRunning
        text: backend.queueState.queueRunning ? qsTranslate("Tingmu", "正在准备…") : backend.lessonModel.configured ? qsTranslate("Tingmu", "补全讲解与译稿") : qsTranslate("Tingmu", "配置逐句讲解模型…")
        onClicked: backend.command(backend.lessonModel.configured ? "prepareLessons" : "openLessonSettings", {})
    }
}
