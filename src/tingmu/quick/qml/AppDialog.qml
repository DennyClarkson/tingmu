import QtQuick
import QtQuick.Controls.Basic
Dialog {
    id:root
    padding:20
    font.family:Theme.font
    font.pixelSize:13
    palette.window:Theme.panel
    palette.base:Theme.bg
    palette.text:Theme.text
    palette.windowText:Theme.text
    palette.button:Theme.raised
    palette.buttonText:Theme.text
    palette.highlight:Theme.accent
    palette.highlightedText:Theme.bg
    background:Rectangle{color:Theme.panel;radius:10;border.color:Theme.line}
    header:Text {
        text:root.title;textFormat:Text.PlainText
        leftPadding:20;rightPadding:20;topPadding:20;bottomPadding:14
        color:Theme.text;font.family:Theme.serif;font.pixelSize:20
        elide:Text.ElideRight
    }
    footer:DialogButtonBox {
        visible:root.standardButtons!==Dialog.NoButton
        standardButtons:root.standardButtons
        padding:16;spacing:8
        background:Rectangle{color:"transparent"}
        delegate:Button {
            id:button
            implicitHeight:34;leftPadding:16;rightPadding:16
            contentItem:Text{text:button.text;color:Theme.text;font.family:Theme.font;font.pixelSize:12;verticalAlignment:Text.AlignVCenter;horizontalAlignment:Text.AlignHCenter}
            background:Rectangle{color:button.down ? Theme.pressed : button.hovered ? Theme.hover : Theme.raised;radius:5;border.color:button.activeFocus ? Theme.accent : Theme.line}
        }
    }
}
