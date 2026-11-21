import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: root
    required property string label
    property alias code: field.text
    readonly property var choices: workspaces.languageChoices
    spacing: 5
    Text { text: root.label; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12 }
    Choice {
        Layout.fillWidth: true
        model: root.choices
        textRole: "label"
        currentIndex: root.choices.findIndex(function(row){return row.code === root.code})
        displayText: currentIndex >= 0 ? currentText : "自定义语言"
        onActivated: {
            if (currentIndex >= 0) field.text = root.choices[currentIndex].code
        }
    }
    Field { id: field; Layout.fillWidth: true; placeholderText: "输入语言名称或代码，如 pt-BR"; maximumLength: 64 }
}
