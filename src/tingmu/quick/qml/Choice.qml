import QtQuick
import QtQuick.Controls.Basic
import "Icons.js" as Icons

ComboBox {
    id: control
    // Keep model identity stable when the interface language changes.
    property bool translateOptions: false
    property var optionLabel: null
    property int popupMinWidth: 180
    function labelAt(index, value) {
        if (index < 0 || value === undefined || value === null) return ""
        if (optionLabel) return optionLabel(index, value)
        return translateOptions ? qsTranslate("Tingmu", value) : value
    }
    displayText: labelAt(currentIndex, currentText)
    Accessible.name: displayText
    implicitHeight: Theme.controlHeight
    implicitWidth: 180
    font.family: Theme.font; font.pixelSize: 12
    leftPadding: 12; rightPadding: 30
    opacity: enabled ? 1 : Theme.disabledOpacity
    hoverEnabled: true
    HoverHandler { enabled: control.enabled; cursorShape: Qt.PointingHandCursor }
    background: Rectangle {
        color: control.down ? Theme.pressed : control.hovered ? Theme.hover : Theme.bg
        radius: Theme.radiusSm
        border.color: control.activeFocus || control.popup.visible ? Theme.accent : Theme.line
    }
    contentItem: Text {
        textFormat: Text.PlainText; text: control.displayText; color: Theme.text; font: control.font
        verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
    }
    indicator: Image {
        x: control.width - 24; y: (control.height - height) / 2
        width: 13; height: 13
        sourceSize.width: 20; sourceSize.height: 20
        source: Icons.source("chevron-right", Theme.muted)
        rotation: control.popup.visible ? -90 : 90
    }
    delegate: ItemDelegate {
        id: option
        required property var modelData
        required property int index
        width: control.popup.availableWidth; implicitHeight: 38
        text: control.labelAt(index, control.textRole ? modelData[control.textRole] : modelData)
        font: control.font; leftPadding: 28; rightPadding: 12
        highlighted: control.highlightedIndex === index
        contentItem: Text {
            textFormat: Text.PlainText; text: option.text; color: option.highlighted ? Theme.accent : Theme.text
            font: control.font; elide: Text.ElideRight; verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle { color: option.highlighted ? Theme.accentSoft : Theme.transparent; radius: Theme.radiusSm }
        Image {
            x: 7; anchors.verticalCenter: parent.verticalCenter
            width: 15; height: 15
            sourceSize.width: 20; sourceSize.height: 20
            source: Icons.source("check", Theme.accent)
            visible: control.currentIndex === option.index
        }
        HoverHandler { enabled: control.enabled && option.enabled; cursorShape: Qt.PointingHandCursor }
    }
    popup: Popup {
        objectName: "choicePopup"
        y: control.height + 5
        width: Math.max(control.width, control.popupMinWidth)
        padding: 6
        implicitHeight: Math.min(contentItem.implicitHeight + 2 * padding, 320)
        background: Rectangle { color: Theme.surfaceElevated; radius: Theme.radiusMd; border.color: Theme.line }
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            highlightMoveDuration: 0
            ScrollBar.vertical: AppScrollBar {}
        }
    }
}
