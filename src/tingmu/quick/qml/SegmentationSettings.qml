import QtQuick
import QtQuick.Layouts

ColumnLayout {
    spacing: 4
    Text {
        id: label
        text: qsTranslate("Tingmu", "新任务分句方式")
        color: Theme.text; font.family: Theme.font; font.pixelSize: 12
    }
    Choice {
        objectName: "segmentationModeChoice"
        Layout.fillWidth: true
        Accessible.name: label.text
        translateOptions: true
        model: ["本地分句", "模型智能分句"]
        function selectedIndex() { return backend.settings.segmentation_mode === "llm_regroup" ? 1 : 0 }
        currentIndex: selectedIndex()
        onActivated: function(index) {
            backend.setting("segmentation_mode", index === 1 ? "llm_regroup" : "local")
            currentIndex = Qt.binding(selectedIndex)
        }
    }
    Text {
        Layout.fillWidth: true
        text: backend.settings.segmentation_mode === "llm_regroup"
              ? qsTranslate("Tingmu", "多语言或复杂对白推荐使用智能分句。按工作区文本权限，使用复核模型重组相邻字幕，未分配时使用讲解模型。已有配对译文的字幕保留原分段。")
              : qsTranslate("Tingmu", "在本机按字幕时间戳、停顿和标点分句。启用逐句复核后，模型可校对每句文字。")
        color: Theme.muted; font.family: Theme.font; font.pixelSize: 11; wrapMode: Text.Wrap
    }
}
