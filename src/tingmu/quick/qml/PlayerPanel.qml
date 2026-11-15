import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtMultimedia
import "Style.js" as S
Item {
    id:root
    property var workspace
    property bool fullscreen:false
    property bool overlayVisible:true
    property bool overlayReading:true
    property bool overlayTranslation:true
    property bool overlayExplanation:true
    property bool controlsVisible:true
    property bool displayCaption:true
    property int captionSize:21
    property int aspectMode:0
    property bool fullscreenGeometryReady:false
    function savePlayerPrefs(){if(workspace)workspace.savePreference("player",{displayCaption:displayCaption,captionSize:captionSize,aspectMode:aspectMode})}
    function openSettings(){ settingsPopup.open() }
    function focusPlayback(){if(fullscreen)fullVideoHost.forceActiveFocus();else root.forceActiveFocus()}
    function toggleFullscreen(){ fullscreen=!fullscreen; if(fullscreen){fullWindow.showFullScreen();showControls()}else fullWindow.hide();focusPlayback();Qt.callLater(function(){root.focusPlayback();backend.refreshVideo()}) }
    onFullscreenChanged:{fullscreenGeometryReady=false;sceneSettled.restart();Qt.callLater(function(){backend.refreshVideo()})}
    function showControls(){ controlsVisible=true; hideControls.restart() }
    function typing(win){var item=win ? win.activeFocusItem : null;return !!item && (item.hasOwnProperty("echoMode") || item.hasOwnProperty("readOnly") || item.hasOwnProperty("popup") || item.hasOwnProperty("currentIndex") || item.hasOwnProperty("highlighted"))}
    function saveOverlay(){ if(workspace) workspace.saveOverlay({x:overlay.x,y:overlay.y,width:overlay.width,height:overlay.height,visible:overlayVisible,reading:overlayReading,translation:overlayTranslation,explanation:overlayExplanation}) }
    function clampOverlay(){overlay.width=Math.min(overlay.width,Math.max(280,fullWindow.width));overlay.height=Math.min(overlay.height,Math.max(180,fullWindow.height));overlay.x=Math.max(0,Math.min(overlay.x,fullWindow.width-overlay.width));overlay.y=Math.max(0,Math.min(overlay.y,fullWindow.height-overlay.height))}
    Component.onCompleted:{
        backend.attachVideo(video.videoSink)
        var o=workspace ? workspace.overlayLayout() : null
        if(o){overlay.width=o.width || 430;overlay.height=o.height || 360;overlay.x=o.x===undefined ? 30 : o.x;overlay.y=o.y===undefined ? 80 : o.y;overlayVisible=o.visible!==false;overlayReading=o.reading!==false;overlayTranslation=o.translation!==false;overlayExplanation=o.explanation!==false}
        var prefs=workspace ? workspace.preference("player") : null
        if(prefs){displayCaption=prefs.displayCaption!==false;captionSize=prefs.captionSize || 21;aspectMode=prefs.aspectMode || 0}
    }
    Timer { id:hideControls; interval:3200; onTriggered:if(backend.playback.playing && !overlayDrag.pressed && !resizeGrip.pressed && !settingsPopup.opened)root.controlsVisible=false }
    Timer {
        id:sceneSettled
        interval:120
        onTriggered:{
            if(root.fullscreen && fullWindow.visible && fullWindow.visibility===Window.FullScreen && fullWindow.width>0 && fullWindow.height>0){root.fullscreenGeometryReady=true;root.clampOverlay()}
            backend.refreshVideo()
        }
    }
    Connections { target:backend;function onPlaybackChanged(){if(!backend.playback.playing)root.controlsVisible=true} }
    ColumnLayout {
        anchors.fill:parent; spacing:0
        Item {
            id:normalVideoHost
            Layout.fillWidth:true; Layout.fillHeight:true
            Rectangle { anchors.fill:parent; color:Theme.video }
            Column {
                anchors.centerIn:parent; spacing:16; visible:!backend.state.mediaPath
                Text { textFormat:Text.PlainText; anchors.horizontalCenter:parent.horizontalCenter; text:"听 幕"; color:Theme.captionText; font.family:Theme.font; font.pixelSize:36; font.weight:Font.Light }
                TButton { anchors.horizontalCenter:parent.horizontalCenter; text:"打开视频"; iconSource:"arrow-up-right"; primary:true; onClicked:backend.command("openFiles",{}) }
            }
            MouseArea { anchors.fill:parent; onDoubleClicked:root.toggleFullscreen(); onClicked:backend.command("playPause",{}) }
        }
        PlayerControls { Layout.fillWidth:true; Layout.margins:12; onFullscreenRequested:root.toggleFullscreen(); onSettingsRequested:root.openSettings() }
    }
    VideoOutput {
        id:video; objectName:"playerVideo"
        parent:root.fullscreen ? fullVideoHost : normalVideoHost
        anchors.fill:parent
        fillMode:root.aspectMode===1 ? VideoOutput.PreserveAspectCrop : root.aspectMode===2 ? VideoOutput.Stretch : VideoOutput.PreserveAspectFit
        Rectangle {
            anchors.horizontalCenter:parent.horizontalCenter;anchors.bottom:parent.bottom;anchors.bottomMargin:root.fullscreen ? 150 : 18
            width:Math.min(parent.width-40,learningCaption.implicitWidth+28);height:learningCaption.implicitHeight+14
            radius:5;color:Theme.captionBg;visible:root.displayCaption && !!backend.activeCue.text && !backend.playback.embeddedText
            Text { textFormat:Text.PlainText;id:learningCaption;objectName:"learningCaption";width:Math.min(video.width-68,implicitWidth);anchors.centerIn:parent;text:backend.activeCue.text || "";color:Theme.captionText;font.family:Theme.font;font.pixelSize:root.fullscreen ? root.captionSize+5 : root.captionSize;wrapMode:Text.Wrap;maximumLineCount:3;elide:Text.ElideRight;horizontalAlignment:Text.AlignHCenter}
        }
        Rectangle {
            anchors.horizontalCenter:parent.horizontalCenter;anchors.bottom:parent.bottom;anchors.bottomMargin:root.fullscreen ? 150 : 18
            width:Math.min(parent.width-40,embeddedText.implicitWidth+28);height:embeddedText.implicitHeight+14
            radius:5;color:Theme.captionBg;visible:!!backend.playback.embeddedText
            Text { textFormat:Text.PlainText;id:embeddedText;width:Math.min(video.width-68,implicitWidth);anchors.centerIn:parent;text:backend.playback.embeddedText || "";color:Theme.captionText;font.family:Theme.font;font.pixelSize:root.fullscreen ? 24 : 17;wrapMode:Text.Wrap;horizontalAlignment:Text.AlignHCenter}
        }
    }
    Window {
        id:fullWindow; objectName:"fullscreenWindow"
        title:"听幕 · 全屏"
        color:Theme.video; visible:false
        onVisibleChanged:{if(visible)root.showControls();sceneSettled.restart()}
        onClosing:function(close){close.accepted=false;root.fullscreen=false;hide()}
        onWidthChanged:if(visible){root.fullscreenGeometryReady=false;sceneSettled.restart()}
        onHeightChanged:if(visible){root.fullscreenGeometryReady=false;sceneSettled.restart()}
        onVisibilityChanged:sceneSettled.restart()
        PlaybackShortcuts{player:root;active:root.fullscreen}
        Item { id:fullVideoHost; anchors.fill:parent }
        MouseArea {
            anchors.fill:parent; hoverEnabled:true
            cursorShape:root.controlsVisible ? Qt.ArrowCursor : Qt.BlankCursor
            onPositionChanged:root.showControls()
            onClicked:backend.command("playPause",{})
            onDoubleClicked:root.toggleFullscreen()
        }
        Rectangle {
            anchors.top:parent.top; width:parent.width; height:90
            visible:root.controlsVisible
            gradient:Gradient { GradientStop{position:0;color:Theme.scrim} GradientStop{position:1;color:Theme.transparent} }
            RowLayout {
                anchors.left:parent.left; anchors.right:parent.right; anchors.top:parent.top; anchors.margins:24
                Text { textFormat:Text.PlainText; Layout.fillWidth:true; text:backend.state.mediaName || "听幕"; color:Theme.text; font.family:Theme.font; font.pixelSize:16; elide:Text.ElideMiddle }
                TButton { text:root.overlayVisible ? "隐藏伴侣" : "显示伴侣"; onClicked:{root.overlayVisible=!root.overlayVisible;root.saveOverlay()} }
                TButton { text:"退出全屏  Esc"; onClicked:root.toggleFullscreen() }
            }
        }
        Rectangle {
            id:overlay; objectName:"companionOverlay"
            x:30; y:80; width:430; height:360
            visible:root.overlayVisible
            radius:10
            color:Qt.rgba(Theme.panel.r,Theme.panel.g,Theme.panel.b,backend.settings.overlay_opacity===undefined ? 0.86 : backend.settings.overlay_opacity)
            border.color:Theme.line
            clip:true
            MouseArea { anchors.fill:parent; onPositionChanged:root.showControls(); onPressed:function(mouse){mouse.accepted=true} }
            ColumnLayout {
                anchors.fill:parent; anchors.margins:16; spacing:9
                Item {
                    Layout.fillWidth:true; Layout.preferredHeight:26
                    Text { textFormat:Text.PlainText; anchors.left:parent.left; anchors.verticalCenter:parent.verticalCenter; text:"听幕  /  学习伴侣"; color:Theme.accent; font.family:Theme.font; font.pixelSize:11 }
                    MouseArea {
                        id:overlayDrag; objectName:"overlayDrag"
                        anchors.fill:parent; anchors.rightMargin:35
                        cursorShape:pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                        property point lastGlobal
                        onPressed:function(mouse){lastGlobal=mapToGlobal(mouse.x,mouse.y);root.showControls()}
                        onPositionChanged:function(mouse){if(pressed){var p=mapToGlobal(mouse.x,mouse.y);overlay.x+=p.x-lastGlobal.x;overlay.y+=p.y-lastGlobal.y;lastGlobal=p;root.clampOverlay()}}
                        onReleased:root.saveOverlay()
                    }
                    TButton { anchors.right:parent.right; iconSource:"close"; quiet:true; implicitHeight:26; tip:"隐藏伴侣浮层"; onClicked:{root.overlayVisible=false;root.saveOverlay()} }
                }
                CompanionContent {
                    objectName:"overlayScroll"
                    onLearningRequested:{if(root.fullscreen)root.toggleFullscreen();if(root.workspace)root.workspace.changeView("learn")}
                    Layout.fillWidth:true; Layout.fillHeight:true
                    compact:true
                    textSize:backend.settings.overlay_font_size || 20
                    showReading:root.overlayReading; showTranslation:root.overlayTranslation; showExplanation:root.overlayExplanation
                }
            }
            MouseArea {
                id:resizeGrip; objectName:"overlayResizeGrip"
                width:26;height:26;anchors.right:parent.right;anchors.bottom:parent.bottom
                cursorShape:Qt.SizeFDiagCursor
                property point origin
                property real initialWidth
                property real initialHeight
                onPressed:function(mouse){origin=mapToGlobal(mouse.x,mouse.y);initialWidth=overlay.width;initialHeight=overlay.height;root.showControls()}
                onPositionChanged:function(mouse){if(pressed){var p=mapToGlobal(mouse.x,mouse.y);overlay.width=Math.max(280,Math.min(fullWindow.width-overlay.x,initialWidth+p.x-origin.x));overlay.height=Math.max(180,Math.min(fullWindow.height-overlay.y,initialHeight+p.y-origin.y))}}
                onReleased:root.saveOverlay()
                Text { textFormat:Text.PlainText; anchors.centerIn:parent; text:"◢"; color:Theme.muted; font.pixelSize:13 }
            }
        }
        Rectangle {
            anchors.bottom:parent.bottom; width:parent.width; height:138; visible:root.controlsVisible
            gradient:Gradient{ GradientStop{position:0;color:Theme.transparent} GradientStop{position:0.35;color:Theme.scrim} GradientStop{position:1;color:Theme.scrim} }
            PlayerControls { anchors.left:parent.left;anchors.right:parent.right;anchors.bottom:parent.bottom;anchors.margins:28;fullscreen:true;onFullscreenRequested:root.toggleFullscreen();onSettingsRequested:root.openSettings() }
            HoverHandler { onHoveredChanged:if(hovered)root.showControls() }
        }
    }
    SettingPopup {
        id:settingsPopup
        parent:root.fullscreen ? fullWindow.contentItem : root
        y:root.fullscreen ? 80 : 6
        width:Math.min(350,parent.width-16)
        height:Math.min(610,parent.height-16)
        contentItem:ScrollView {
            id:settingsScroll
            clip:true;contentWidth:availableWidth
            ColumnLayout {
                width:settingsScroll.availableWidth;spacing:10
                Text { textFormat:Text.PlainText; text:"播放器";color:Theme.text;font.family:Theme.font;font.pixelSize:16 }
                Choice { Layout.fillWidth:true;model:["适应画面","填满裁切","拉伸画面"];currentIndex:root.aspectMode;onActivated:{root.aspectMode=currentIndex;root.savePlayerPrefs()} }
                Toggle{text:"显示学习字幕";checked:root.displayCaption;onToggled:{root.displayCaption=checked;root.savePlayerPrefs()}}
                Text { textFormat:Text.PlainText;text:"字幕字号 · " + root.captionSize;color:Theme.muted;font.family:Theme.font;font.pixelSize:11}
                TrackSlider{Layout.fillWidth:true;from:14;to:32;stepSize:1;value:root.captionSize;onMoved:{root.captionSize=Math.round(value);root.savePlayerPrefs()}}
                Text { textFormat:Text.PlainText; text:"音轨";color:Theme.muted;font.family:Theme.font;font.pixelSize:11 }
                Choice { Layout.fillWidth:true; model:backend.playback.audioTracks || []; textRole:"name"; currentIndex:backend.playback.audioTrack || 0; onActivated:backend.command("setAudioTrack",{index:currentIndex}) }
                Text { textFormat:Text.PlainText; text:"内嵌字幕";color:Theme.muted;font.family:Theme.font;font.pixelSize:11 }
                Choice { Layout.fillWidth:true; model:["关闭"].concat((backend.playback.subtitleTracks || []).map(function(t,i){return typeof t==="string" ? t : (t.name || "字幕 "+(i+1))})); currentIndex:(backend.playback.subtitleTrack===undefined ? -1 : backend.playback.subtitleTrack)+1;onActivated:backend.command("setSubtitleTrack",{index:currentIndex-1}) }
                RowLayout { TButton{text:"导入字幕";onClicked:backend.command("importSubtitle",{})} TButton{text:"准备当前视频";enabled:!!backend.state.mediaPath;onClicked:backend.command("prepareCurrent",{})} }
                Rectangle{Layout.fillWidth:true;height:1;color:Theme.line}
                Text { textFormat:Text.PlainText; text:"全屏伴侣浮层";color:Theme.text;font.family:Theme.font;font.pixelSize:14 }
                Toggle { text:"显示浮层";checked:root.overlayVisible;onToggled:{root.overlayVisible=checked;root.saveOverlay()} }
                Text { textFormat:Text.PlainText; text:"透明度";color:Theme.muted;font.family:Theme.font;font.pixelSize:11 }
                TrackSlider { Layout.fillWidth:true;from:0.25;to:1;value:backend.settings.overlay_opacity || 0.86;onMoved:backend.setting("overlay_opacity",value) }
                Text { textFormat:Text.PlainText; text:"字号 · " + (backend.settings.overlay_font_size || 20);color:Theme.muted;font.family:Theme.font;font.pixelSize:11 }
                TrackSlider { Layout.fillWidth:true;from:14;to:36;stepSize:1;value:backend.settings.overlay_font_size || 20;onMoved:backend.setting("overlay_font_size",Math.round(value)) }
                Toggle { text:"读音";checked:root.overlayReading;onToggled:{root.overlayReading=checked;root.saveOverlay()} }
                Toggle { text:"释义";checked:root.overlayTranslation;onToggled:{root.overlayTranslation=checked;root.saveOverlay()} }
                Toggle { text:"讲解";checked:root.overlayExplanation;onToggled:{root.overlayExplanation=checked;root.saveOverlay()} }
                Text { textFormat:Text.PlainText; Layout.fillWidth:true;text:"拖动浮层标题移动；拖动右下角改变尺寸。内容在固定区域内滚动。";color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap }
            }
        }
    }
    PlaybackShortcuts{player:root;active:!root.fullscreen}
}
