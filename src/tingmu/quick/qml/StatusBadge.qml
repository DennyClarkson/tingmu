import QtQuick

Rectangle {
    id: badge
    property string text: ""
    property string tone: "neutral"
    property bool filledAccent: false
    readonly property color foreground: filledAccent && tone === "accent" ? Theme.onAccent : tone === "danger" ? Theme.danger : tone === "warning" ? Theme.warning
                                       : tone === "organized" ? Theme.organizedWord : tone === "neutral" ? Theme.muted : Theme.accent
    implicitWidth: label.implicitWidth + 14
    implicitHeight: Math.max(20, label.implicitHeight + 4)
    radius: height / 2
    color: filledAccent && tone === "accent" ? Theme.accent : tone === "danger" ? Theme.dangerSoft : tone === "warning" ? Theme.warningSoft
           : tone === "organized" ? Theme.organizedSoft : tone === "neutral" ? Theme.raised : Theme.accentSoft
    Text {
        id: label
        anchors.centerIn: parent
        width: Math.max(0, parent.width - 14)
        text: badge.text
        color: badge.foreground
        font.family: Theme.font
        font.pixelSize: 11
        textFormat: Text.PlainText
        horizontalAlignment: Text.AlignHCenter
        elide: Text.ElideRight
    }
}
