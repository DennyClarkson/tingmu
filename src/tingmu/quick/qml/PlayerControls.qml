import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S

Rectangle {
    id: root
    required property var settings
    property bool fullscreen: false
    signal fullscreenRequested()
    signal settingsRequested()
    implicitHeight: 86
    color: Theme.panel
    readonly property bool hasMedia: !!backend.state.mediaPath
    readonly property bool wide: width >= 590
    readonly property real position: seek.pressed ? seek.value : backend.playback.position

    component Tool: TButton {
        quiet: true
        implicitHeight: 32
        implicitWidth: 32
        leftPadding: 3; rightPadding: 3
        focusPolicy: Qt.TabFocus
        Accessible.name: tip || text
    }
    component Rule: Rectangle { implicitWidth: 1; implicitHeight: 16; color: Theme.line }
    Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.line }
    ColumnLayout {
        anchors.fill: parent; anchors.leftMargin: root.wide ? 18 : 9; anchors.rightMargin: anchors.leftMargin
        anchors.topMargin: 5; anchors.bottomMargin: 7; spacing: 1
        RowLayout {
            Layout.fillWidth: true; spacing: 9
            Text { text: S.time(root.position); color: Theme.text; font.family: Theme.mono; font.pixelSize: 12 }
            TrackSlider {
                id: seek; objectName: root.fullscreen ? "fullscreenSeek" : "playerSeek"
                Accessible.name: "播放进度"
                Layout.fillWidth: true; Layout.minimumWidth: 36
                from: 0; to: Math.max(1, backend.playback.duration || 0)
                value: backend.playback.position || 0
                enabled: to > 1
                onMoved: backend.command("seek", {position: value})
            }
            Text {
                text: root.settings.remainingTime ? "−" + S.time(backend.playback.duration-root.position) : S.time(backend.playback.duration)
                color: Theme.muted; font.family: Theme.mono; font.pixelSize: 12
            }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: root.wide ? 5 : 1
            Tool {
                objectName: root.fullscreen ? "fullscreenPlay" : "playerPlay"
                iconSource: backend.playback.playing ? "pause" : "play"; primary: true
                implicitWidth: 38; implicitHeight: 38; enabled: root.hasMedia
                tip: "播放 / 暂停 · 空格"
                background: Rectangle { radius: height/2; color: parent.down ? Qt.darker(Theme.accent,1.15) : Theme.accent; border.width:parent.activeFocus ? 2 : 0; border.color:Theme.text }
                onClicked: backend.command("playPause",{})
            }
            Tool { iconSource: "skip-back"; tip: "上一句 · ↑"; enabled: backend.cues.length>0; onClicked: backend.command("previousCue",{}) }
            Tool { iconSource: "skip-forward"; tip: "下一句 · ↓"; enabled: backend.cues.length>0; onClicked: backend.command("nextCue",{}) }
            Tool { visible: root.wide; iconSource: "repeat"; tip: "重听此句"; enabled: !!backend.activeCue.id; onClicked: backend.command("repeatCue",{}) }
            Rule { visible: root.settings.showLoop && root.width>=370 }
            RowLayout {
                visible: root.settings.showLoop && root.width>=370; spacing: 0
                Tool { text:"A"; checked:backend.playback.loopA>=0; enabled:root.hasMedia; tip:"设置循环起点";onClicked:backend.command("loopA",{}) }
                Tool { text:"B"; checked:backend.playback.loopB>=0; enabled:backend.playback.loopA>=0; tip:"设置循环终点";onClicked:backend.command("loopB",{}) }
                Tool { iconSource:"close";visible:backend.playback.loopA>=0; tip:"清除循环";onClicked:backend.command("clearLoop",{}) }
            }
            Item { Layout.fillWidth: true }
            Choice {
                id: rate; implicitWidth: 68; implicitHeight: 30; leftPadding: 8; rightPadding: 20
                model: ["0.5×","0.75×","1×","1.25×","1.5×","2×"]
                readonly property var rates: [0.5,0.75,1,1.25,1.5,2]
                currentIndex: Math.max(0,rates.indexOf(backend.playback.rate || 1))
                onActivated: backend.command("setRate",{value:rates[currentIndex]})
            }
            Tool { visible: root.width>=370;iconSource:backend.playback.muted ? "volume-off" : "volume";tip:"静音 · M";onClicked:backend.command("setMuted",{value:!backend.playback.muted}) }
            TrackSlider { visible:root.wide;Accessible.name:"音量";Layout.preferredWidth:58;from:0;to:1;value:backend.playback.volume;onMoved:backend.command("setVolume",{value:value}) }
            Rule { visible: root.width>=420 }
            Tool { objectName:root.fullscreen ? "fullscreenSettings" : "openPlayerSettings";iconSource:"settings";tip:"播放器设置";onClicked:root.settingsRequested() }
            Tool { objectName:root.fullscreen ? "leaveFullscreen" : "enterFullscreen";iconSource:root.fullscreen ? "minimize" : "maximize";tip:root.fullscreen ? "退出全屏 · Esc" : "全屏 · F";onClicked:root.fullscreenRequested() }
        }
    }
}
