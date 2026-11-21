import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "CaptionStyles.js" as Styles

ColumnLayout {
    id: editor
    objectName: "captionStyleEditor"
    required property var appearance
    required property var savedStyles
    signal edited(var next)
    signal saveNamed(string name)
    signal removeNamed(string name)
    spacing: 9

    function change(key, value) {
        const next = Object.assign({}, appearance)
        next[key] = value
        edited(next)
    }

    component LabelText: Text {
        color: Theme.muted
        font.family: Theme.font
        font.pixelSize: 12
        textFormat: Text.PlainText
    }
    component NumberSetting: RowLayout {
        required property string title
        required property string key
        required property int low
        required property int high
        property string unit: ""
        Layout.fillWidth: true
        LabelText { text: parent.title; Layout.preferredWidth: 65 }
        TrackSlider {
            Layout.fillWidth: true
            from: parent.low; to: parent.high; stepSize: 1
            value: editor.appearance[parent.key]
            onMoved: editor.change(parent.key, Math.round(value))
        }
        LabelText { Layout.preferredWidth: 36; horizontalAlignment: Text.AlignRight; text: editor.appearance[parent.key] + parent.unit }
    }
    component ColorSetting: RowLayout {
        required property string title
        required property string key
        Layout.fillWidth: true
        LabelText { text: parent.title; Layout.preferredWidth: 65 }
        Rectangle { width: 22; height: 22; radius: 4; color: editor.appearance[parent.key]; border.color: Theme.line }
        Field {
            id: hex
            Layout.fillWidth: true
            text: editor.appearance[parent.key]
            maximumLength: 7
            validator: RegularExpressionValidator { regularExpression: /#[a-fA-F0-9]{6}/ }
            onEditingFinished: {
                if (acceptableInput) editor.change(parent.key, text)
                text = Qt.binding(function(){ return editor.appearance[hex.parent.key] })
            }
        }
        Repeater {
            model: ["#ffffff", "#ffe38e", "#a9d8ff", "#14191c"]
            delegate: Rectangle {
                required property string modelData
                width: 22; height: 22; radius: 11
                color: modelData; border.color: Theme.line
                TapHandler { onTapped: editor.change(parent.parent.key, parent.modelData) }
            }
        }
    }

    Rectangle {
        Layout.fillWidth: true
        implicitHeight: 114
        radius: 6
        gradient: Gradient { GradientStop { position: 0; color: "#627679" } GradientStop { position: 1; color: "#182324" } }
        CaptionLayer {
            anchors.fill: parent
            appearance: editor.appearance
            lines: ["One more time.", "再听一次。"]
            fontScale: 0.76
        }
    }
    LabelText { text: "样式预设" }
    Choice {
        id: presets
        objectName: "captionPresetChoice"
        Layout.fillWidth: true
        readonly property var names: Object.keys(editor.savedStyles)
        model: ["当前样式"].concat(Styles.presets.map(s => s.name), names)
        currentIndex: 0
        onActivated: {
            if (currentIndex === 0) return
            const i = currentIndex - 1
            editor.edited(Object.assign({}, Styles.base, i < Styles.presets.length ? Styles.presets[i].values : editor.savedStyles[currentText]))
            presetName.text = i < Styles.presets.length ? "" : currentText
            currentIndex = 0
        }
    }
    RowLayout {
        Field { id: presetName; objectName:"captionPresetName"; Layout.fillWidth: true; placeholderText: "自定义样式名"; maximumLength: 40 }
        TButton { objectName:"saveCaptionPreset"; text: "保存样式"; enabled: !!presetName.text.trim() && !Styles.presets.some(s => s.name === presetName.text.trim()); onClicked: editor.saveNamed(presetName.text.trim()) }
        TButton { text: "移除"; enabled: Object.keys(editor.savedStyles).indexOf(presetName.text.trim()) >= 0; onClicked: editor.removeNamed(presetName.text.trim()) }
    }
    LabelText { text: "字体" }
    Choice {
        id: family
        Layout.fillWidth: true
        model: Qt.fontFamilies()
        currentIndex: model.indexOf(editor.appearance.family)
        onActivated: editor.change("family", currentText)
    }
    RowLayout {
        Toggle { text: "粗体"; checked: editor.appearance.bold; onToggled: editor.change("bold", checked) }
        Toggle { text: "斜体"; checked: editor.appearance.italic; onToggled: editor.change("italic", checked) }
        Toggle { text: "背景框"; checked: editor.appearance.box; onToggled: editor.change("box", checked) }
    }
    NumberSetting { title: "字号"; key: "size"; low: 12; high: 64; unit: " px" }
    ColorSetting { title: "文字颜色"; key: "fill" }
    ColorSetting { title: "描边颜色"; key: "edge" }
    NumberSetting { title: "描边宽度"; key: "outline"; low: 0; high: 5; unit: " px" }
    NumberSetting { title: "阴影距离"; key: "shadow"; low: 0; high: 6; unit: " px" }
    ColorSetting { visible: editor.appearance.box; title: "背景颜色"; key: "boxColor" }
    NumberSetting { visible: editor.appearance.box; title: "背景浓度"; key: "boxOpacity"; low: 0; high: 100; unit: "%" }
    LabelText { text: "字幕位置" }
    GridLayout {
        columns: 3
        Layout.fillWidth: true
        Repeater {
            model: ["左上", "上中", "右上", "左中", "居中", "右中", "左下", "下中", "右下"]
            delegate: TButton {
                required property int index
                required property string modelData
                readonly property int alignment: [7,8,9,4,5,6,1,2,3][index]
                Layout.fillWidth: true
                text: modelData
                checked: alignment === editor.appearance.align
                onClicked: editor.change("align", alignment)
            }
        }
    }
    NumberSetting { title: "水平边距"; key: "horizontalMargin"; low: 0; high: 25; unit: "%" }
    NumberSetting { title: "垂直边距"; key: "verticalMargin"; low: 0; high: 30; unit: "%" }
    LabelText { Layout.fillWidth: true; wrapMode: Text.Wrap; text: "样式应用于学习字幕、导入字幕和文字内嵌字幕。图像内嵌字幕保留片源字形。" }
}
