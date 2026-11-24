import QtQuick
import QtQuick.Controls.Basic
import "Icons.js" as Icons

Button {
    id: control
    property bool primary: false
    property bool quiet: false
    property bool danger: false
    property string tip: ""
    property string iconSource: ""
    property color labelColor: danger ? Theme.danger : primary ? Theme.onAccent : checked ? Theme.accent : Theme.text
    implicitHeight: Theme.controlHeight
    implicitWidth: Math.max(32, contentItem.implicitWidth + leftPadding + rightPadding)
    horizontalPadding: 10
    verticalPadding: 3
    font.family: Theme.font
    font.pixelSize: 12
    hoverEnabled: true
    opacity: enabled ? 1 : Theme.disabledOpacity
    Accessible.name: text || tip
    Accessible.description: text ? tip : ""
    HoverHandler { enabled: control.enabled; cursorShape: Qt.PointingHandCursor }
    background: Rectangle {
        radius: Theme.radiusSm
        color: {
            if (control.danger)
                return control.down ? Theme.dangerPressed : control.hovered ? Theme.dangerHover : Theme.dangerSoft
            if (control.primary)
                return control.down ? Theme.accentPressed : control.hovered ? Theme.accentHover : Theme.accent
            return control.down ? Theme.pressed : control.hovered ? Theme.hover : control.checked ? Theme.accentSoft
                   : control.quiet ? Theme.transparent : Theme.raised
        }
        border.width: control.activeFocus || (!control.primary && !control.quiet && !control.danger) ? 1 : 0
        border.color: control.activeFocus ? (control.danger ? Theme.danger : control.primary ? Theme.onAccent : Theme.accent) : Theme.line
    }
    TextMetrics { id: labelMetrics; text: control.text; font: control.font }
    contentItem: Item {
        implicitWidth: Math.ceil(labelMetrics.advanceWidth) + 2 + (control.iconSource ? 16 : 0) + (control.text && control.iconSource ? 7 : 0)
        implicitHeight: buttonRow.implicitHeight
        Row {
            id: buttonRow
            anchors.centerIn: parent
            spacing: control.text && control.iconSource ? 7 : 0
            Image {
                visible: control.iconSource.length > 0
                source: visible ? Icons.source(control.iconSource, control.labelColor) : ""
                width: visible ? 16 : 0; height: 16
                anchors.verticalCenter: parent.verticalCenter
                sourceSize.width: 24; sourceSize.height: 24
            }
            Text {
                objectName: "buttonText"
                width: Math.min(Math.ceil(labelMetrics.advanceWidth) + 2, Math.max(0, control.availableWidth - (control.iconSource ? 23 : 0)))
                elide: Text.ElideRight
                textFormat: Text.PlainText
                text: control.text
                visible: text.length > 0
                color: control.labelColor
                font: control.font
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }
    Loader {
        active: control.hovered && control.tip.length > 0
        sourceComponent: AppToolTip {
            parent: control
            text: control.tip
            visible: true
        }
    }
}
