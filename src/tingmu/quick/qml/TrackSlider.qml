import QtQuick
import QtQuick.Controls.Basic
import "Style.js" as S
Slider {
    id:control
    implicitHeight:20
    padding:0
    background:Rectangle {
        x:control.leftPadding; y:control.topPadding+(control.availableHeight-height)/2
        implicitWidth:180; implicitHeight:3; width:control.availableWidth; height:3; radius:2; color:Theme.line
        Rectangle { width:control.visualPosition*parent.width; height:parent.height; radius:2; color:Theme.accent }
    }
    handle:Rectangle {
        x:control.leftPadding+control.visualPosition*(control.availableWidth-width)
        y:control.topPadding+(control.availableHeight-height)/2
        implicitWidth:control.hovered || control.pressed ? 11 : 7
        implicitHeight:width; radius:width/2; color:Theme.accent
    }
}
