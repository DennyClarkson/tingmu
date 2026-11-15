pragma Singleton
import QtQuick
QtObject {
    property bool closing:false
    property string name: backend.settings.theme || "forest"
    readonly property var palettes: ({
        forest:["#101315","#191e21","#232a2d","#30393c","#eceae4","#a1aeac","#8bbda1","#293f34","#dfa28e"],
        paper:["#f5f1e8","#fffdf8","#eee9df","#d9d2c5","#292d28","#686c60","#39694c","#dbe8da","#a64531"],
        mist:["#e9f0ef","#f7fbfa","#dce8e5","#c4d6d1","#1d3534","#586f6b","#276b69","#cbe3dc","#a44135"],
        graphite:["#17191d","#22252b","#2c3038","#3c414b","#eeece6","#adb1b8","#c5b58e","#423f35","#e1a28f"]
    })
    readonly property var p: palettes[name] || palettes.forest
    readonly property color bg:p[0]
    readonly property color panel:p[1]
    readonly property color raised:p[2]
    readonly property color line:p[3]
    readonly property color text:p[4]
    readonly property color muted:p[5]
    readonly property color accent:p[6]
    readonly property color selection:p[7]
    readonly property color error:p[8]
    readonly property color hover:Qt.tint(panel,Qt.rgba(accent.r,accent.g,accent.b,0.13))
    readonly property color pressed:Qt.tint(panel,Qt.rgba(accent.r,accent.g,accent.b,0.23))
    readonly property color dropTint:Qt.rgba(accent.r,accent.g,accent.b,0.16)
    readonly property color warning:name==="paper" || name==="mist" ? "#806018" : "#d7c58c"
    readonly property color video:"#090b0c"
    readonly property color captionText:"#ffffff"
    readonly property color captionBg:"#b3000000"
    readonly property color scrim:Qt.rgba(panel.r,panel.g,panel.b,0.92)
    readonly property color transparent:"transparent"
    readonly property string font:"Source Han Sans SC"
    readonly property string serif:"Source Han Serif SC"
    readonly property string latin:"Source Sans 3"
    readonly property string mono:"Source Code Pro"
}
