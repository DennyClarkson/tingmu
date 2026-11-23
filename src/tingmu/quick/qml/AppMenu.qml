import QtQuick
import QtQuick.Controls.Basic
Menu {
    id:root
    implicitWidth: interfaceLanguage.code === "en" ? 290 : 248
    padding:6
    font.family:Theme.font
    font.pixelSize:12
    delegate:MenuItem {
        id:item
        implicitHeight:38
        leftPadding:32;rightPadding:18
        contentItem:Text {text:item.text;textFormat:Text.PlainText;color:!item.enabled ? Theme.muted : item.highlighted ? Theme.accent : Theme.text;font:item.font;verticalAlignment:Text.AlignVCenter;elide:Text.ElideRight}
        indicator:Text{x:11;anchors.verticalCenter:parent.verticalCenter;text:item.checked ? "✓" : "";color:Theme.accent;font.pixelSize:14}
        background:Rectangle{radius:5;color:item.highlighted ? Theme.selection : "transparent"}
    }
    background:Rectangle{color:Theme.panel;radius:8;border.color:Theme.line}
}
