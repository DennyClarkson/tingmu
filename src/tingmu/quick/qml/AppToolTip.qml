import QtQuick
import QtQuick.Controls.Basic

ToolTip {
    id: control
    delay: 650
    padding: 8
    horizontalPadding: 10
    margins: 6
    x: parent ? (parent.width - width) / 2 : 0
    font.family: Theme.font
    font.pixelSize: 12
    width: Math.min(implicitWidth, 360, parent && parent.Window.window
                    ? Math.max(1, parent.Window.window.width - 2 * margins) : 360)
    contentItem: Text {
        text: control.text
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        font: control.font
        color: Theme.text
    }
    background: Rectangle { color: Theme.tooltipBg; border.color: Theme.tooltipBorder; radius: Theme.radiusSm }
}
