import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtMultimedia
import "Style.js" as S
import "CaptionStyles.js" as Styles
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
    property bool displayTranslation:false
    property bool displayImported:true
    property int bitmapScale:100
    property int bitmapShift:0
    property bool reserveBar:true
    property bool autoHide:true
    property bool showLoop:true
    property bool remainingTime:false
    property int hideDelay:3
    property var appearance:Object.assign({}, Styles.base)
    property var savedStyles:({})
    property int settingsTab:0
    readonly property bool playing:backend.playback.playing
    readonly property bool settingsOpen:settingsPopup.opened
    property int aspectMode:0
    property bool fullscreenGeometryReady:false
    function savePlayerPrefs(){
        if(workspace) workspace.savePreference("player",{displayCaption,displayTranslation,displayImported,aspectMode,reserveBar,autoHide,
            showLoop,remainingTime,hideDelay,appearance,savedStyles,bitmapScale,bitmapShift})
    }
    function selectTrack(index) {
        backend.command("setSubtitleTrack",{index:index})
    }
    function setAppearance(next){appearance=next;savePlayerPrefs()}
    function openSettings(){ settingsPopup.open() }
    function closeSettings(){settingsPopup.close()}
    function focusPlayback(){if(fullscreen)fullVideoHost.forceActiveFocus();else root.forceActiveFocus()}
    function toggleFullscreen(){ fullscreen=!fullscreen; if(fullscreen){fullWindow.showFullScreen();showControls()}else fullWindow.hide();focusPlayback();Qt.callLater(function(){root.focusPlayback();backend.refreshVideo()}) }
    onFullscreenChanged:{fullscreenGeometryReady=false;sceneSettled.restart();Qt.callLater(function(){backend.refreshVideo()})}
    function showControls(){ controlsVisible=true; hideControls.restart() }
    onPlayingChanged:if(fullscreen)showControls()
    function typing(win){var item=win ? win.activeFocusItem : null;return !!item && (item.hasOwnProperty("echoMode") || item.hasOwnProperty("readOnly") || item.hasOwnProperty("popup") || item.hasOwnProperty("currentIndex") || item.hasOwnProperty("highlighted"))}
    function saveOverlay(){ if(workspace) workspace.saveOverlay({x:overlay.x,y:overlay.y,width:overlay.width,height:overlay.height,visible:overlayVisible,reading:overlayReading,translation:overlayTranslation,explanation:overlayExplanation}) }
    function clampOverlay(){overlay.width=Math.min(overlay.width,Math.max(280,fullWindow.width));overlay.height=Math.min(overlay.height,Math.max(180,fullVideoHost.height));overlay.x=Math.max(0,Math.min(overlay.x,fullWindow.width-overlay.width));overlay.y=Math.max(0,Math.min(overlay.y,fullVideoHost.height-overlay.height))}
    onReserveBarChanged:if(fullscreenGeometryReady)Qt.callLater(clampOverlay)
    Component.onCompleted:{
        backend.attachVideo(video.videoSink)
        var o=workspace ? workspace.overlayLayout() : null
        if(o){overlay.width=o.width || 430;overlay.height=o.height || 360;overlay.x=o.x===undefined ? 30 : o.x;overlay.y=o.y===undefined ? 80 : o.y;overlayVisible=o.visible!==false;overlayReading=o.reading!==false;overlayTranslation=o.translation!==false;overlayExplanation=o.explanation!==false}
        var prefs=workspace ? workspace.preference("player") : null
        if(prefs){
            displayCaption=prefs.displayCaption!==false;displayTranslation=prefs.displayTranslation===true;displayImported=prefs.displayImported!==false
            aspectMode=prefs.aspectMode || 0;reserveBar=prefs.reserveBar!==false;autoHide=prefs.autoHide!==false
            showLoop=prefs.showLoop!==false;remainingTime=!!prefs.remainingTime;hideDelay=prefs.hideDelay || 3
            appearance=Object.assign({},Styles.base,prefs.captionSize ? {size:prefs.captionSize} : {},prefs.appearance || {})
            savedStyles=prefs.savedStyles || {}
            bitmapScale=prefs.bitmapScale || 100;bitmapShift=prefs.bitmapShift || 0
        }
    }
    Timer { id:hideControls; interval:root.hideDelay*1000; onTriggered:if(root.autoHide && backend.playback.playing && !barHover.hovered && !overlayDrag.pressed && !resizeGrip.pressed && !settingsPopup.opened)root.controlsVisible=false }
    Timer {
        id:sceneSettled
        interval:120
        onTriggered:{
            if(root.fullscreen && fullWindow.visible && fullWindow.visibility===Window.FullScreen && fullWindow.width>0 && fullWindow.height>0){root.fullscreenGeometryReady=true;root.clampOverlay()}
            backend.refreshVideo()
        }
    }
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
        PlayerControls { Layout.fillWidth:true; settings:root; onFullscreenRequested:root.toggleFullscreen(); onSettingsRequested:root.openSettings() }
    }
    VideoOutput {
        id:video; objectName:"playerVideo"
        parent:root.fullscreen ? fullVideoHost : normalVideoHost
        anchors.fill:parent
        fillMode:root.aspectMode===1 ? VideoOutput.PreserveAspectCrop : root.aspectMode===2 ? VideoOutput.Stretch : VideoOutput.PreserveAspectFit
        clip:true
        Item {
            id:bitmapViewport
            x:captions.x;y:captions.y;width:captions.width;height:captions.height;clip:true
            readonly property rect rect:video.contentRect
            readonly property real factor:root.bitmapScale/100
            readonly property var bounds:backend.playback.embeddedBounds || ({})
            readonly property real centerX:bounds.width ? bounds.x + bounds.width/2 : 0.5
            readonly property real centerY:bounds.height ? bounds.y + bounds.height/2 : 0.9
            Image {
                id:embeddedBitmap;objectName:"embeddedBitmap"
                x:bitmapViewport.rect.x-bitmapViewport.x+bitmapViewport.centerX*(bitmapViewport.rect.width-width)
                y:bitmapViewport.rect.y-bitmapViewport.y+bitmapViewport.centerY*(bitmapViewport.rect.height-height)+root.bitmapShift*bitmapViewport.rect.height/100
                width:bitmapViewport.rect.width*bitmapViewport.factor;height:bitmapViewport.rect.height*bitmapViewport.factor
                source:backend.playback.embeddedImage || "";cache:false;fillMode:Image.Stretch
            }
        }
        CaptionLayer {
            id:captions;objectName:"playbackCaptionLayer"
            x:Math.max(0,video.contentRect.x);y:Math.max(0,video.contentRect.y)
            width:Math.max(0,Math.min(video.width,video.contentRect.width));height:Math.max(0,Math.min(video.height,video.contentRect.height))
            appearance:root.appearance
            readonly property var bitmap:bitmapViewport.bounds
            readonly property real bitmapTop:embeddedBitmap.y + (bitmap.y || 0)*embeddedBitmap.height
            readonly property real bitmapBottom:bitmapTop + (bitmap.height || 0)*embeddedBitmap.height
            readonly property string embeddedCaption:backend.playback.embeddedText || ""
            clearance:Math.max(root.fullscreen && !root.reserveBar && root.controlsVisible ? Math.max(0, y+height-fullWindow.height+transport.height+8) : 0,
                               bitmap.height && bitmapTop>height/2 ? height-bitmapTop+5 : 0)
            topClearance:bitmap.height && bitmapTop<=height/2 ? bitmapBottom+5 : 0
            fontScale:root.fullscreen ? 1.2 : 1
            lines:[
                root.displayCaption && backend.activeCue.text ? (backend.activeCue.speaker ? backend.activeCue.speaker+"  " : "") + backend.activeCue.text : "",
                root.displayTranslation && backend.activeCue.translationVerified ? backend.activeCue.translation : "",
                embeddedCaption, root.displayImported ? backend.sources.importedText : ""
            ].filter(t=>!!t)
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
        Item { id:fullVideoHost;objectName:"fullscreenVideoArea"; anchors.fill:parent;anchors.bottomMargin:root.reserveBar ? transport.height : 0 }
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
                onPositionChanged:function(mouse){if(pressed){var p=mapToGlobal(mouse.x,mouse.y);overlay.width=Math.max(280,Math.min(fullWindow.width-overlay.x,initialWidth+p.x-origin.x));overlay.height=Math.max(180,Math.min(fullVideoHost.height-overlay.y,initialHeight+p.y-origin.y))}}
                onReleased:root.saveOverlay()
                Text { textFormat:Text.PlainText; anchors.centerIn:parent; text:"◢"; color:Theme.muted; font.pixelSize:13 }
            }
        }
        Rectangle {
            id:transport;objectName:"fullscreenTransport"
            anchors.bottom:parent.bottom; width:parent.width; height:fullControls.implicitHeight+8; visible:root.controlsVisible
            color:Theme.panel
            PlayerControls { id:fullControls;anchors.centerIn:parent;width:Math.min(parent.width,1160);settings:root;fullscreen:true;onFullscreenRequested:root.toggleFullscreen();onSettingsRequested:root.openSettings() }
            HoverHandler { id:barHover;onHoveredChanged:root.showControls() }
        }
    }
    SettingPopup {
        id:settingsPopup;objectName:"playerSettingsPopup"
        parent:root.fullscreen ? fullWindow.contentItem : (root.Window.window ? root.Window.window.contentItem : root)
        width:Math.min(492,parent.width-16);height:Math.min(650,parent.height-20)
        x:(parent.width-width)/2;y:(parent.height-height)/2
        modal:true;focus:true
        onClosed:{root.focusPlayback();root.showControls()}
        component SectionLabel: Text {
            Layout.fillWidth:true; textFormat:Text.PlainText; color:Theme.muted
            font.family:Theme.font;font.pixelSize:12;wrapMode:Text.Wrap
        }
        contentItem:ColumnLayout {
            spacing:10
            RowLayout {
                Layout.fillWidth:true
                Text { Layout.fillWidth:true;text:"播放器设置";color:Theme.text;font.family:Theme.font;font.pixelSize:18;font.bold:true }
                TButton { iconSource:"close";tip:"关闭设置";quiet:true;onClicked:settingsPopup.close() }
            }
            RowLayout {
                Layout.fillWidth:true;spacing:4
                Repeater {
                    model:["控制栏","声音","字幕","样式","伴侣"]
                    delegate:TButton {
                        required property string modelData
                        required property int index
                        objectName:"playerSettingsTab"+index
                        Layout.fillWidth:true;Layout.preferredWidth:1;text:modelData
                        checked:root.settingsTab===index
                        onClicked:{root.settingsTab=index;settingsScroll.contentItem.contentY=0}
                    }
                }
            }
            Rectangle { Layout.fillWidth:true;height:1;color:Theme.line }
            ScrollView {
                id:settingsScroll
                Layout.fillWidth:true;Layout.fillHeight:true;clip:true;contentWidth:availableWidth
                ColumnLayout {
                    width:settingsScroll.availableWidth-4;spacing:10
                    ColumnLayout {
                        visible:root.settingsTab===0;Layout.fillWidth:true;spacing:10
                        SectionLabel { text:"视频缩放" }
                        Choice { Layout.fillWidth:true;model:["适应画面","填满裁切","拉伸画面"];currentIndex:root.aspectMode;onActivated:{root.aspectMode=currentIndex;root.savePlayerPrefs()} }
                        SectionLabel { text:"播放栏" }
                        Toggle { text:"显示 A–B 循环工具";checked:root.showLoop;onToggled:{root.showLoop=checked;root.savePlayerPrefs()} }
                        Toggle { text:"时间轴右侧显示剩余时间";checked:root.remainingTime;onToggled:{root.remainingTime=checked;root.savePlayerPrefs()} }
                        SectionLabel { text:"全屏" }
                        Toggle { objectName:"reserveFullscreenBar";text:"为下方控制栏预留空间";checked:root.reserveBar;onToggled:{root.reserveBar=checked;root.savePlayerPrefs()} }
                        Toggle { text:"自动隐藏控制栏";checked:root.autoHide;onToggled:{root.autoHide=checked;root.showControls();root.savePlayerPrefs()} }
                        SectionLabel { visible:root.autoHide;text:"鼠标静止 "+root.hideDelay+" 秒后隐藏" }
                        TrackSlider { visible:root.autoHide;Layout.fillWidth:true;from:1;to:10;stepSize:1;value:root.hideDelay;onMoved:{root.hideDelay=Math.round(value);root.savePlayerPrefs()} }
                    }
                    ColumnLayout {
                        visible:root.settingsTab===1;Layout.fillWidth:true;spacing:10
                        SectionLabel { text:"音频输出设备" }
                        Choice { objectName:"audioOutputChoice";Layout.fillWidth:true;model:backend.sources.audioOutputs;textRole:"name";currentIndex:backend.sources.outputIndex;onActivated:backend.command("setAudioOutput",{id:model[currentIndex].id}) }
                        SectionLabel { text:"视频音轨" }
                        Choice { Layout.fillWidth:true;model:backend.playback.audioTracks || [];textRole:"name";enabled:count>0;currentIndex:backend.playback.audioTrack;onActivated:backend.command("setAudioTrack",{index:currentIndex}) }
                        SectionLabel { text:"音量 · "+Math.round(backend.playback.volume*100)+"%" }
                        TrackSlider { Layout.fillWidth:true;from:0;to:1;value:backend.playback.volume;onMoved:backend.command("setVolume",{value:value}) }
                        Toggle { text:"静音";checked:backend.playback.muted;onToggled:backend.command("setMuted",{value:checked}) }
                    }
                    ColumnLayout {
                        visible:root.settingsTab===2;Layout.fillWidth:true;spacing:7
                        Toggle { objectName:"showLearningTrack";text:"显示学习字幕";checked:root.displayCaption;onToggled:{root.displayCaption=checked;root.savePlayerPrefs()} }
                        Toggle { objectName:"showTranslationTrack";text:"显示"+backend.lessonModel.language+"译文";checked:root.displayTranslation;onToggled:{root.displayTranslation=checked;root.savePlayerPrefs()} }
                        LessonPreparation {}
                        TButton { text:"准备当前视频";visible:!backend.cues.length;enabled:!!backend.state.mediaPath;onClicked:backend.command("prepareCurrent",{}) }
                        Rectangle { Layout.fillWidth:true;height:1;color:Theme.line }
                        Toggle {
                            objectName:"showEmbeddedTrack";text:"显示内嵌字幕";enabled:embeddedChoice.count>0
                            checked:backend.playback.subtitleTrack>=0
                            onToggled:root.selectTrack(checked ? Math.max(0,embeddedChoice.currentIndex) : -1)
                        }
                        Choice {
                            id:embeddedChoice;objectName:"embeddedTrackChoice";Layout.fillWidth:true
                            model:backend.playback.subtitleTracks || [];textRole:"name";enabled:count>0
                            currentIndex: {
                                if(!count) return -1
                                if(backend.playback.subtitleTrack>=0) return backend.playback.subtitleTrack
                                const choice=backend.sources.embeddedChoice
                                return choice ? Math.min(count-1,choice.index) : 0
                            }
                            onActivated:root.selectTrack(currentIndex)
                        }
                        SectionLabel { visible:!embeddedChoice.count || !!backend.playback.embeddedStatus;text:backend.playback.embeddedStatus || "此视频没有可选择的内嵌字幕轨。" }
                        SectionLabel { visible:embeddedChoice.count>0;text:"图像字幕大小 · "+root.bitmapScale+"%" }
                        TrackSlider { visible:embeddedChoice.count>0;Layout.fillWidth:true;from:60;to:150;stepSize:5;value:root.bitmapScale;onMoved:{root.bitmapScale=Math.round(value);root.savePlayerPrefs()} }
                        SectionLabel { visible:embeddedChoice.count>0;text:"图像字幕垂直偏移 · "+root.bitmapShift+"%" }
                        TrackSlider { visible:embeddedChoice.count>0;Layout.fillWidth:true;from:-30;to:30;stepSize:1;value:root.bitmapShift;onMoved:{root.bitmapShift=Math.round(value);root.savePlayerPrefs()} }
                        Rectangle { Layout.fillWidth:true;height:1;color:Theme.line }
                        Toggle { objectName:"showImportedTrack";text:"显示导入字幕";checked:root.displayImported;onToggled:{root.displayImported=checked;root.savePlayerPrefs()} }
                        SectionLabel { text:backend.sources.importedName || "可导入 SRT、VTT、ASS 或 SSA 作为独立播放字幕。" }
                        RowLayout {
                            TButton { text:backend.sources.importedName ? "更换字幕文件…" : "导入字幕文件…";enabled:!!backend.state.mediaPath;onClicked:backend.command("importSubtitle",{}) }
                            TButton { text:"移除";visible:!!backend.sources.importedName;onClicked:backend.command("removeDisplaySubtitle",{}) }
                        }
                    }
                    Loader {
                        visible:root.settingsTab===3;active:visible;Layout.fillWidth:true
                        sourceComponent:CaptionStyleEditor {
                            appearance:root.appearance;savedStyles:root.savedStyles
                            onEdited:next=>root.setAppearance(next)
                            onSaveNamed:name=>{const styles=Object.assign({},root.savedStyles);styles[name]=Object.assign({},root.appearance);root.savedStyles=styles;root.savePlayerPrefs()}
                            onRemoveNamed:name=>{const styles=Object.assign({},root.savedStyles);delete styles[name];root.savedStyles=styles;root.savePlayerPrefs()}
                        }
                    }
                    ColumnLayout {
                        visible:root.settingsTab===4;Layout.fillWidth:true;spacing:10
                        SectionLabel { text:"全屏伴侣浮层" }
                        Toggle { text:"显示浮层";checked:root.overlayVisible;onToggled:{root.overlayVisible=checked;root.saveOverlay()} }
                        SectionLabel { text:"透明度" }
                        TrackSlider { Layout.fillWidth:true;from:0.25;to:1;value:backend.settings.overlay_opacity || 0.86;onMoved:backend.setting("overlay_opacity",value) }
                        SectionLabel { text:"字号 · "+(backend.settings.overlay_font_size || 20) }
                        TrackSlider { Layout.fillWidth:true;from:14;to:36;stepSize:1;value:backend.settings.overlay_font_size || 20;onMoved:backend.setting("overlay_font_size",Math.round(value)) }
                        Toggle { text:"读音";checked:root.overlayReading;onToggled:{root.overlayReading=checked;root.saveOverlay()} }
                        Toggle { text:"释义";checked:root.overlayTranslation;onToggled:{root.overlayTranslation=checked;root.saveOverlay()} }
                        Toggle { text:"讲解";checked:root.overlayExplanation;onToggled:{root.overlayExplanation=checked;root.saveOverlay()} }
                    }
                }
            }
        }
    }
    PlaybackShortcuts{player:root;active:!root.fullscreen}
}
