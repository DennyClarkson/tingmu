import QtQuick
import "Icons.js" as Icons
import QtQuick.Controls.Basic
import "Style.js" as S
Button {
    id: control
    property bool primary: false
    property bool quiet: false
    property string tip: ""
    property string iconSource: ""
    implicitHeight: 32
    implicitWidth: Math.max(32, contentItem.implicitWidth + 20)
    font.family: Theme.font
    font.pixelSize: 12
    hoverEnabled: true
    padding: 9
    opacity: enabled ? 1 : 0.35
    background: Rectangle {
        radius: 5
        color: control.down ? Theme.pressed : control.primary ? Theme.accent : control.hovered ? Theme.hover : control.checked ? Theme.selection : control.quiet ? "transparent" : Theme.raised
        border.width: control.activeFocus ? 1 : 0
        border.color: Theme.accent
    }
    TextMetrics{id:labelMetrics;text:control.text;font:control.font}
    contentItem: Item {
        implicitWidth: Math.ceil(labelMetrics.advanceWidth)+2 + (control.iconSource ? 16 : 0) + (control.text && control.iconSource ? 7 : 0)
        implicitHeight: buttonRow.implicitHeight
        Row {
            id:buttonRow
            anchors.centerIn:parent
            spacing:control.text && control.iconSource ? 7 : 0
            Image { visible:control.iconSource.length>0; source:control.iconSource ? Icons.source(control.iconSource,control.primary ? Theme.bg : control.checked ? Theme.accent : Theme.text) : ""; width:visible ? 16 : 0;height:16;anchors.verticalCenter:parent.verticalCenter;sourceSize.width:24;sourceSize.height:24 }
            Text { id:label;objectName:"buttonText"; width:Math.min(Math.ceil(labelMetrics.advanceWidth)+2,Math.max(0,control.width-control.leftPadding-control.rightPadding-(control.iconSource ? 23 : 0)));elide:Text.ElideRight;textFormat:Text.PlainText; text:control.text;visible:text.length>0;color:control.primary ? Theme.bg : control.checked ? Theme.accent : Theme.text;font:control.font;anchors.verticalCenter:parent.verticalCenter }
        }
    }
    ToolTip.visible: hovered && tip.length > 0
    ToolTip.text: tip
    ToolTip.delay: 650
}
