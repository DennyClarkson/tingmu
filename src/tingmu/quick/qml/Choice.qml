import QtQuick
import QtQuick.Controls.Basic
ComboBox {
    id:control
    implicitHeight:36;implicitWidth:180
    font.family:Theme.font;font.pixelSize:12
    leftPadding:12;rightPadding:30
    opacity:enabled ? 1 : 0.45
    background:Rectangle{color:control.hovered ? Theme.hover : Theme.bg;radius:6;border.color:control.activeFocus || control.popup.visible ? Theme.accent : Theme.line}
    contentItem:Text{textFormat:Text.PlainText;text:control.displayText;color:Theme.text;font:control.font;verticalAlignment:Text.AlignVCenter;elide:Text.ElideRight}
    indicator:Canvas{
        x:control.width-24;y:(control.height-height)/2;width:12;height:8
        property color strokeColor:Theme.muted
        rotation:control.popup.visible ? 180 : 0
        onStrokeColorChanged:requestPaint()
        onPaint:{var c=getContext("2d");c.clearRect(0,0,width,height);c.strokeStyle=strokeColor;c.lineWidth=1.5;c.beginPath();c.moveTo(2,2);c.lineTo(6,6);c.lineTo(10,2);c.stroke()}
    }
    delegate:ItemDelegate {
        id:option
        required property var modelData
        required property int index
        width:control.popup.width-12;implicitHeight:38
        text:control.textRole ? modelData[control.textRole] : modelData
        font:control.font;leftPadding:28;rightPadding:12
        contentItem:Text{textFormat:Text.PlainText;text:option.text;color:option.highlighted ? Theme.accent : Theme.text;font:control.font;elide:Text.ElideRight;verticalAlignment:Text.AlignVCenter}
        background:Rectangle{color:option.highlighted ? Theme.selection : "transparent";radius:4}
        highlighted:control.highlightedIndex===index
        Text{x:9;anchors.verticalCenter:parent.verticalCenter;text:control.currentIndex===option.index ? "✓" : "";color:Theme.accent;font.pixelSize:12}
    }
    popup:Popup {
        objectName:"choicePopup"
        y:control.height+5;width:Math.max(control.width,180);padding:6
        implicitHeight:Math.min(contentItem.implicitHeight+12,320)
        background:Rectangle{color:Theme.panel;radius:7;border.color:Theme.line}
        contentItem:ListView{clip:true;implicitHeight:contentHeight;model:control.popup.visible ? control.delegateModel : null;currentIndex:control.highlightedIndex;highlightMoveDuration:0;ScrollBar.vertical:ScrollBar{}}
    }
}
