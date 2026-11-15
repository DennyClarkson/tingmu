import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S
ColumnLayout {
    id:root
    property bool fullscreen:false
    signal fullscreenRequested()
    signal settingsRequested()
    spacing:5
    RowLayout {
        Layout.fillWidth:true; spacing:10
        Text { textFormat:Text.PlainText; text:S.time(seek.pressed ? seek.value : backend.playback.position); color:Theme.muted; font.family:Theme.mono; font.pixelSize:11; Layout.preferredWidth:58 }
        TrackSlider { id:seek; objectName:root.fullscreen ? "fullscreenSeek" : "playerSeek"; Layout.fillWidth:true; from:0; to:Math.max(1,backend.playback.duration || 0); value:backend.playback.position || 0; onMoved:backend.command("seek",{position:value}); enabled:to>1 }
        Text { textFormat:Text.PlainText; text:S.time(backend.playback.duration); color:Theme.muted; font.family:Theme.mono; font.pixelSize:11; Layout.preferredWidth:58; horizontalAlignment:Text.AlignRight }
    }
    RowLayout {
        Layout.fillWidth:true; spacing:4
        TButton { iconSource:"skip-back"; tip:"上一句 · ↑"; quiet:true; onClicked:backend.command("previousCue",{}) }
        TButton { objectName:root.fullscreen ? "fullscreenPlay" : "playerPlay"; iconSource:backend.playback.playing ? "pause" : "play"; tip:"播放 / 暂停 · 空格"; primary:true; implicitWidth:42; enabled:!!backend.state.mediaPath; onClicked:backend.command("playPause",{}) }
        TButton { iconSource:"skip-forward"; tip:"下一句 · ↓"; quiet:true; onClicked:backend.command("nextCue",{}) }
        TButton { visible:root.width>=460;iconSource:"repeat"; tip:"重听此句"; quiet:true; onClicked:backend.command("repeatCue",{}) }
        TButton { text:"A"; checked:backend.playback.loopA>=0; tip:"设置循环起点"; quiet:true; onClicked:backend.command("loopA",{}) }
        TButton { text:"B"; checked:backend.playback.loopB>=0; tip:"设置循环终点"; quiet:true; onClicked:backend.command("loopB",{}) }
        TButton { iconSource:"close"; visible:backend.playback.loopA>=0 || backend.playback.loopB>=0; quiet:true; tip:"清除循环"; onClicked:backend.command("clearLoop",{}) }
        Item { Layout.fillWidth:true }
        Choice {
            id:rate; implicitWidth:76; implicitHeight:30
            model:["0.5×","0.75×","1×","1.25×","1.5×","2×"]
            property var rates:[0.5,0.75,1,1.25,1.5,2]
            currentIndex:Math.max(0,rates.indexOf(backend.playback.rate || 1))
            onActivated:backend.command("setRate",{value:rates[currentIndex]})
        }
        TButton { visible:root.width>=420;iconSource:backend.playback.muted ? "volume-off" : "volume"; quiet:true; tip:"切换静音"; onClicked:backend.command("setMuted",{value:!backend.playback.muted}) }
        TrackSlider { visible:root.width>530; Layout.preferredWidth:65; from:0; to:1; value:backend.playback.volume===undefined ? 0.8 : backend.playback.volume; onMoved:backend.command("setVolume",{value:value}) }
        TButton { iconSource:"settings"; quiet:true; tip:"音轨 / 字幕 / 浮层"; onClicked:root.settingsRequested() }
        TButton { focusPolicy:Qt.TabFocus;objectName:root.fullscreen ? "leaveFullscreen" : "enterFullscreen"; iconSource:root.fullscreen ? "minimize" : "maximize"; quiet:true; tip:root.fullscreen ? "退出全屏 · Esc" : "全屏 · F"; onClicked:root.fullscreenRequested() }
    }
}
