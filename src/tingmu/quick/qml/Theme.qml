pragma Singleton
import QtQuick
import QtQuick.Window

QtObject {
    id: theme
    property bool closing: false
    function canAnimate(item) {
        if (!onscreenAnimations || closing || !item || !item.visible) return false
        const window = item.Window.window
        return !!window && window.visible && window.visibility !== Window.Minimized
    }
    property string name: backend.settings.theme || "forest"
    readonly property bool isDark: name !== "paper" && name !== "mist"
    readonly property var palettes: ({
        forest: ["#101315","#191e21","#232a2d","#30393c","#eceae4","#a1aeac","#8bbda1","#293f34","#dfa28e"],
        paper: ["#f5f1e8","#fffdf8","#eee9df","#d9d2c5","#292d28","#55594d","#335e44","#dbe8da","#933d2b"],
        mist: ["#e9f0ef","#f7fbfa","#dce8e5","#c4d6d1","#1d3534","#475e5a","#245c5a","#cbe3dc","#92392f"],
        graphite: ["#17191d","#22252b","#2c3038","#3c414b","#eeece6","#adb1b8","#c5b58e","#423f35","#e1a28f"]
    })
    readonly property var p: palettes[name] || palettes.forest
    readonly property color bg: p[0]
    readonly property color panel: p[1]
    readonly property color raised: p[2]
    readonly property color line: p[3]
    readonly property color text: p[4]
    readonly property color muted: p[5]
    readonly property color accent: p[6]
    readonly property color accentSoft: p[7]
    readonly property color selection: accentSoft
    readonly property color error: p[8]
    readonly property color danger: error
    readonly property alias onAccent: theme.primaryText
    readonly property color primaryText: isDark ? bg : Qt.rgba(1, 1, 1, 1)
    readonly property color accentHover: isDark ? Qt.lighter(accent, 1.10) : Qt.darker(accent, 1.11)
    readonly property color accentPressed: isDark ? Qt.darker(accent, 1.14) : Qt.darker(accent, 1.24)
    readonly property color hover: tint(panel, accent, 0.10)
    readonly property color pressed: tint(panel, accent, 0.16)
    readonly property color dropTint: Qt.rgba(accent.r, accent.g, accent.b, 0.16)
    readonly property color warning: isDark ? "#d7c58c" : "#715313"
    readonly property color organizedWord: isDark ? "#cfafe9" : "#613f83"
    readonly property color dangerSoft: tint(panel, danger, 0.08)
    readonly property color dangerHover: tint(panel, danger, 0.13)
    readonly property color dangerPressed: tint(panel, danger, 0.18)
    readonly property color warningSoft: tint(panel, warning, 0.10)
    readonly property color organizedSoft: tint(panel, organizedWord, 0.10)
    readonly property color surfaceElevated: tint(panel, isDark ? text : Qt.rgba(1, 1, 1, 1), isDark ? 0.045 : 0.50)
    readonly property color modalScrim: Qt.rgba(0, 0, 0, isDark ? 0.55 : 0.28)
    readonly property color scrollThumb: tint(raised, muted, 0.55)
    readonly property color scrollThumbHover: muted
    readonly property color tooltipBg: surfaceElevated
    readonly property color tooltipBorder: tint(surfaceElevated, muted, 0.55)
    readonly property color video: "#090b0c"
    readonly property color captionShadow: "#a0000000"
    readonly property color captionText: "#ffffff"
    readonly property color captionBg: "#b3000000"
    readonly property color scrim: Qt.rgba(panel.r, panel.g, panel.b, 0.92)
    readonly property color transparent: "transparent"
    readonly property int controlHeight: 34
    readonly property int compactHeight: 28
    readonly property int radiusSm: 6
    readonly property int radiusMd: 8
    readonly property int radiusLg: 12
    readonly property real disabledOpacity: 0.42
    readonly property string font: "Source Han Sans SC"
    readonly property string serif: "Source Han Serif SC"
    readonly property string latin: "Source Sans 3"
    readonly property string mono: "Source Code Pro"

    function tint(base: color, ink: color, alpha: real): color {
        return Qt.tint(base, Qt.rgba(ink.r, ink.g, ink.b, alpha))
    }
}
