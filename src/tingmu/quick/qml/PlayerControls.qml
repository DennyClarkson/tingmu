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
    color: fullscreen ? Theme.surfaceElevated : Theme.panel
    readonly property bool hasMedia: !!backend.mediaState.mediaPath
    readonly property bool wide: width >= 590
    readonly property bool tight: width < 285
    readonly property real position: seek.pressed ? seek.value : backend.playbackPosition

    component Tool: TButton {
        quiet: true
        implicitHeight: Theme.compactHeight
        implicitWidth: Theme.compactHeight
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
                Accessible.name: qsTranslate("Tingmu", "播放进度")
                Layout.fillWidth: true; Layout.minimumWidth: 36
                from: 0; to: Math.max(1, backend.playbackDetails.duration || 0)
                value: backend.playbackPosition || 0
                enabled: to > 1
                onMoved: backend.command("seek", {position: value})
            }
            TButton {
                objectName: root.fullscreen ? "fullscreenTimeDisplay" : "playerTimeDisplay"
                quiet: true
                implicitHeight: Theme.compactHeight
                horizontalPadding: 4
                focusPolicy: Qt.TabFocus
                text: root.settings.remainingTime ? "−" + S.time(backend.playbackDetails.duration-root.position) : S.time(backend.playbackDetails.duration)
                tip: root.settings.remainingTime ? qsTranslate("Tingmu", "显示总时长") : qsTranslate("Tingmu", "显示剩余时间")
                Accessible.name: tip
                labelColor: Theme.muted
                font.family: Theme.mono
                font.pixelSize: 12
                onClicked: { root.settings.remainingTime = !root.settings.remainingTime; root.settings.savePlayerPrefs() }
            }
        }
        RowLayout {
            Layout.fillWidth: true; spacing: root.wide ? 5 : 1
            Tool {
                id: play
                objectName: root.fullscreen ? "fullscreenPlay" : "playerPlay"
                iconSource: backend.playbackDetails.playing ? "pause" : "play"; primary: true
                implicitWidth: 38; implicitHeight: 38; enabled: root.hasMedia
                tip: qsTranslate("Tingmu", "播放 / 暂停 · 空格")
                background: Rectangle {
                    radius: height / 2
                    color: play.down ? Theme.accentPressed : play.hovered ? Theme.accentHover : Theme.accent
                    border.width: play.activeFocus ? 2 : 0
                    border.color: Theme.text
                }
                onClicked: backend.command("playPause",{})
            }
            Tool { visible:!root.tight; iconSource:"skip-back"; tip:qsTranslate("Tingmu", "上一句 · ↑"); enabled:backend.cueCount>0; onClicked:backend.command("previousCue",{}) }
            Tool { visible:!root.tight; iconSource:"skip-forward"; tip:qsTranslate("Tingmu", "下一句 · ↓"); enabled:backend.cueCount>0; onClicked:backend.command("nextCue",{}) }
            Tool { visible: root.wide; iconSource: "repeat"; tip: qsTranslate("Tingmu", "重听此句"); enabled: !!backend.activeCue.id; onClicked: backend.command("repeatCue",{}) }
            Rule { visible: root.settings.showLoop && root.width>=370 }
            RowLayout {
                visible: root.settings.showLoop && root.width>=370; spacing: 0
                Tool { text:"A"; checked:backend.playbackDetails.loopA>=0; enabled:root.hasMedia; tip:qsTranslate("Tingmu", "设置循环起点");onClicked:backend.command("loopA",{}) }
                Tool { text:"B"; checked:backend.playbackDetails.loopB>=0; enabled:backend.playbackDetails.loopA>=0; tip:qsTranslate("Tingmu", "设置循环终点");onClicked:backend.command("loopB",{}) }
                Tool { iconSource:"close";visible:backend.playbackDetails.loopA>=0; tip:qsTranslate("Tingmu", "清除循环");onClicked:backend.command("clearLoop",{}) }
            }
            Item { Layout.fillWidth: true }
            Choice {
                id: rate
                objectName: root.fullscreen ? "fullscreenSpeedChoice" : "playerSpeedChoice"
                popupMinWidth: 96
                implicitWidth: 68; implicitHeight: Theme.compactHeight; leftPadding: 8; rightPadding: 22
                model: ["0.5×","0.75×","1×","1.25×","1.5×","2×"]
                readonly property var rates: [0.5,0.75,1,1.25,1.5,2]
                currentIndex: Math.max(0,rates.indexOf(backend.playbackDetails.rate || 1))
                onActivated: backend.command("setRate",{value:rates[currentIndex]})
            }
            Tool { visible: root.width>=370;iconSource:backend.playbackDetails.muted ? "volume-off" : "volume";tip:qsTranslate("Tingmu", "静音 · M");onClicked:backend.command("setMuted",{value:!backend.playbackDetails.muted}) }
            TrackSlider { visible:root.wide;Accessible.name:qsTranslate("Tingmu", "音量");Layout.preferredWidth:58;from:0;to:1;value:backend.playbackDetails.volume;onMoved:backend.command("setVolume",{value:value}) }
            Rule { visible: root.width>=420 }
            Tool { objectName:root.fullscreen ? "fullscreenSettings" : "openPlayerSettings";iconSource:"settings";tip:qsTranslate("Tingmu", "播放器设置");onClicked:root.settingsRequested() }
            Tool { objectName:root.fullscreen ? "leaveFullscreen" : "enterFullscreen";iconSource:root.fullscreen ? "minimize" : "maximize";tip:root.fullscreen ? qsTranslate("Tingmu", "退出全屏 · Esc") : qsTranslate("Tingmu", "全屏 · F");onClicked:root.fullscreenRequested() }
        }
    }
}
