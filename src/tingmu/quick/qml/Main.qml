import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "Style.js" as S
ApplicationWindow {
    id:app
    objectName:"mainWindow"
    title:(backend.state.mediaName ? backend.state.mediaName + " — " : "") + (workspaces.info.id === "default" ? qsTranslate("Tingmu", workspaces.info.name) : workspaces.info.name) + qsTranslate("Tingmu", " · 听幕")
    width:1480; height:930
    minimumWidth:Math.min(980,Math.max(760,Screen.desktopAvailableWidth-24))
    minimumHeight:Math.min(600,Math.max(420,Screen.desktopAvailableHeight-64))
    visible:true
    color:Theme.bg
    font.family:Theme.font
    font.pixelSize:12
    palette.window:Theme.bg
    palette.base:Theme.panel
    palette.text:Theme.text
    palette.windowText:Theme.text
    palette.button:Theme.raised
    palette.buttonText:Theme.text
    palette.highlight:Theme.accent
    property var savedLayout:backend.layout || ({})
    property bool restoring:true
    property bool closingSession:false
    property string dropTarget:""
    property string viewMode:savedLayout.viewMode || "watch"
    property var viewLayouts:savedLayout.viewLayouts || ({})
    property var viewExtras:savedLayout.viewExtras || ({})
    property var additionalModules:viewExtras[viewMode] || ({})
    property var moduleSlots:viewLayouts[viewMode] || defaultSlots(viewMode)
    function chatSubtitle(){
        if(backend.state.chatStopping)return qsTranslate("Tingmu", "正在停止…等待当前连接结束")
        if(!backend.state.chatBusy)return ""
        var messages=backend.messages || []
        for(var i=messages.length-1;i>=0;i--){var message=messages[i];if(message.role==="assistant" && (message.status==="pending" || message.status==="streaming"))return ({connecting:qsTranslate("Tingmu", "正在连接…"),thinking:qsTranslate("Tingmu", "正在思考…"),acting:qsTranslate("Tingmu", "正在执行学习操作…"),answering:qsTranslate("Tingmu", "正在生成回复…")})[message.phase] || (message.status==="pending" ? qsTranslate("Tingmu", "正在连接…") : qsTranslate("Tingmu", "正在回复…"))}
        return ""
    }
    function defaultSlots(mode){return mode==="prepare" ? {queue:"player",player:"queue",subtitles:"chat",chat:"subtitles"} : {subtitles:"chat",chat:"subtitles"}}
    function moduleEnabled(key){return !!additionalModules[key] || (viewMode==="prepare" ? key==="library" || key==="queue" : ["library","player","companion","subtitles"].indexOf(key)>=0)}
    function slotEnabled(key){var all=docks();for(var m in all)if((moduleSlots[m] || m)===key)return moduleEnabled(m) && !!all[m] && !all[m].detached;return moduleEnabled(key) && !!all[key] && !all[key].detached}
    function changeView(mode){
        if(mode==="learn")backend.command("syncNotebook",{})
        if(mode===viewMode)return
        var layouts=Object.assign({},viewLayouts);layouts[viewMode]=moduleSlots;viewLayouts=layouts
        var extras=Object.assign({},viewExtras);extras[viewMode]=additionalModules;viewExtras=extras
        viewMode=mode;moduleSlots=layouts[mode] || defaultSlots(mode);additionalModules=extras[mode] || ({})
        persist.restart()
    }
    function openModule(key){var d=docks()[key];if(!moduleEnabled(key)){d.detached=true;d.save()}else{d.detached=!d.detached;d.save()}}
    Connections{target:backend;ignoreUnknownSignals:true;function onAppAction(name,payload){if(name!=="workspace")return;var p=payload || {};if(["watch","prepare","learn"].indexOf(p.view)>=0){if(p.view!=="watch" && playerDock.loadedItem && playerDock.loadedItem.fullscreen)playerDock.loadedItem.toggleFullscreen();app.changeView(p.view);app.raise();app.requestActivate()}if(p.module && app.docks()[p.module]){var dock=app.docks()[p.module];if(!app.moduleEnabled(p.module)){dock.detached=true;dock.save()}dock.present()}}}
    function docks(){return {library:libraryDock,queue:queueDock,player:playerDock,subtitles:subtitleDock,companion:companionDock,chat:chatDock}}
    function dockHost(key){return docks()[moduleSlots[key] || key]}
    function slotModule(key){var all=docks();for(var m in all)if((moduleSlots[m] || m)===key)return all[m];return all[key]}
    function slotMinimumHeight(key,base){
        var buddies={library:"queue",queue:"library",player:"subtitles",subtitles:"player",companion:"chat",chat:"companion"}
        var defaults={library:250,queue:210,player:300,subtitles:180,companion:220,chat:190}
        var buddy=buddies[key]
        if(slotModule(key).moduleId==="chat")return Math.min(420,Math.max(300,columns.height-defaults[buddy]-8))
        if(slotEnabled(buddy) && slotModule(buddy).moduleId==="chat")return Math.min(base,Math.max(100,columns.height-slotMinimumHeight(buddy,defaults[buddy])-8))
        return base
    }
    function previewDrop(point){
        var all=docks();dropTarget=""
        for(var key in all){if(!all[key].visible)continue;var p=all[key].mapToGlobal(0,0);if(point.x>=p.x && point.x<=p.x+all[key].width && point.y>=p.y && point.y<=p.y+all[key].height){dropTarget=key;break}}
    }
    function finishDrop(key,point){
        previewDrop(point);var target=dropTarget;dropTarget=""
        if(!target)return false
        if(!moduleEnabled(key)){var extra=Object.assign({},additionalModules);extra[key]=true;additionalModules=extra}
        var occupied=slotModule(target).moduleId;var prior=moduleSlots[key] || key
        var next=Object.assign({},moduleSlots);next[key]=target;next[occupied]=prior;moduleSlots=next
        docks()[key].detached=false
        var copy=Object.assign({},savedLayout);copy.moduleSlots=next;savedLayout=copy;docks()[key].save();persist.restart();return true
    }
    function moduleLayout(key){return savedLayout.modules ? savedLayout.modules[key] : null}
    function overlayLayout(){return savedLayout.overlay || null}
    function saveModule(key,value){var copy=Object.assign({},savedLayout);var mods=Object.assign({},copy.modules || {});mods[key]=value;copy.modules=mods;savedLayout=copy;persist.restart()}
    function saveOverlay(value){var copy=Object.assign({},savedLayout);copy.overlay=value;savedLayout=copy;persist.restart()}
    function savePreference(key,value){var copy=Object.assign({},savedLayout);var prefs=Object.assign({},copy.preferences || {});prefs[key]=value;copy.preferences=prefs;savedLayout=copy;persist.restart()}
    function preference(key){return savedLayout.preferences ? savedLayout.preferences[key] : null}
    function saveWorkspace(){
        if(restoring)return
        var copy=Object.assign({},savedLayout)
        var views=Object.assign({},viewLayouts);views[viewMode]=moduleSlots;viewLayouts=views;copy.viewLayouts=views;copy.viewMode=viewMode
        var extras=Object.assign({},viewExtras);extras[viewMode]=additionalModules;viewExtras=extras;copy.viewExtras=extras
        copy.workspace={width:app.width,height:app.height,x:app.x,y:app.y,leftWidth:leftColumn.width,rightWidth:rightColumn.width,playerHeight:playerDock.height,libraryHeight:libraryDock.height,companionHeight:companionDock.height}
        savedLayout=copy;backend.saveLayout(copy)
    }
    function resetWorkspace(){
        libraryDock.detached=false;queueDock.detached=false;playerDock.detached=false;subtitleDock.detached=false;companionDock.detached=false;chatDock.detached=false
        leftColumn.SplitView.preferredWidth=248;rightColumn.SplitView.preferredWidth=364
        playerDock.SplitView.preferredHeight=app.height*0.54;libraryDock.SplitView.preferredHeight=app.height*0.48;companionDock.SplitView.preferredHeight=app.height*0.51
        moduleSlots=defaultSlots(viewMode);additionalModules={};savedLayout=Object.assign({},savedLayout,{modules:{},moduleSlots:moduleSlots});saveWorkspace()
    }
    Component.onCompleted:{
        var w=savedLayout.workspace || ({})
        app.width=Math.max(app.minimumWidth,Math.min(w.width || 1480,Screen.desktopAvailableWidth-24))
        app.height=Math.max(app.minimumHeight,Math.min(w.height || 930,Screen.desktopAvailableHeight-64))
        var narrow=app.width<1300
        leftColumn.SplitView.preferredWidth=Math.min(w.leftWidth || 248,narrow ? 220 : 460)
        rightColumn.SplitView.preferredWidth=Math.min(w.rightWidth || 364,narrow ? 320 : 620)
        playerDock.SplitView.preferredHeight=Math.min(w.playerHeight || 480,app.height*0.66)
        libraryDock.SplitView.preferredHeight=Math.min(w.libraryHeight || 420,app.height*0.62)
        companionDock.SplitView.preferredHeight=Math.min(w.companionHeight || 450,(app.height-104)*0.56)
        restoring=false
        if(workspaces.info.showSetup) Qt.callLater(function(){workspaceSetup.open()})
    }
    onClosing: function(event) {
        if (!closingSession && desktopTray.shouldHideOnClose(app)) {
            event.accepted = false
            desktopTray.hideToTray()
            return
        }
        learningPanel.flushDrafts()
        Theme.closing = true
        saveWorkspace()
        persist.stop()
        developerPanel.hide()
        if (playerDock.loadedItem && playerDock.loadedItem.fullscreen) playerDock.loadedItem.toggleFullscreen()
        var all = docks()
        for (var key in all) all[key].detached = false
    }
    DeveloperPanel{id:developerPanel}
    ModelManager{id:modelManager}
    WorkspaceSetup{id:workspaceSetup;onModelsRequested:modelManager.open()}
    Connections {
        target:backend
        function onAppAction(name,payload){
            if(name!=="lessonModels")return
            const player=playerDock.loadedItem
            if(player){player.closeSettings();if(player.fullscreen)player.toggleFullscreen()}
            workspaceSetup.openTextSettings()
            app.raise();app.requestActivate()
        }
    }
    ModelDownloadConsent { onConfirmationRequested: { app.raise(); app.requestActivate() } }
    AppDialog {
        id: activeDownload
        title: qsTranslate("Tingmu", "模型下载"); anchors.centerIn: parent; width: Math.min(430, app.width - 24)
        contentItem: ColumnLayout {
            DownloadProgress { Layout.fillWidth: true }
            RowLayout {
                Layout.alignment: Qt.AlignRight
                TButton { text: qsTranslate("Tingmu", "暂停下载"); onClicked: workspaces.action("stopDownload", {}) }
                TButton { text: qsTranslate("Tingmu", "后台下载"); onClicked: activeDownload.close() }
            }
        }
        Connections {
            target: backend
            function onStateChanged() { if (!backend.state.modelDownloading && activeDownload.opened) activeDownload.close() }
        }
    }
    TextConnectionConsent { onConfirmationRequested: { app.raise(); app.requestActivate() } }
    ActionGroup { id: interfaceActions; exclusive: true }
    Timer{id:persist;interval:350;onTriggered:app.saveWorkspace()}
    Shortcut{sequence:"Ctrl+O";onActivated:backend.command("openFiles",{})}
    Shortcut{sequence:"Ctrl+Shift+O";onActivated:backend.command("openFolder",{})}
    header:Rectangle {
        height:66;color:Theme.bg
        RowLayout {
            anchors.fill:parent;anchors.leftMargin:app.width<1100 ? 10 : 20;anchors.rightMargin:anchors.leftMargin;spacing:app.width<1100 ? 6 : 14
            RowLayout {
                spacing:10
                Rectangle { width:26;height:26;radius:6;color:Theme.accent;Text { textFormat:Text.PlainText;anchors.centerIn:parent;text:qsTranslate("Tingmu", "听");color:Theme.bg;font.family:Theme.font;font.pixelSize:17;font.weight:Font.DemiBold} }
                Text { visible:app.width>=1100;textFormat:Text.PlainText;text:qsTranslate("Tingmu", "听幕");color:Theme.text;font.family:Theme.font;font.pixelSize:20;font.weight:Font.DemiBold}
            }
            WorkspacePicker {
                Layout.preferredWidth:app.width<950 ? 142 : app.width<1100 ? 172 : 200
                onCreateRequested:workspaceSetup.openCreate()
                onManageRequested:workspaceSetup.open()
            }
            MenuBar {
                id:menuBar;objectName:"applicationMenuBar"
                Layout.preferredHeight:36
                background:Rectangle{color:"transparent"}
                delegate:MenuBarItem {
                    id:menuItem
                    implicitHeight:34
                    contentItem:Text { textFormat:Text.PlainText;text:menuItem.text;color:menuItem.highlighted ? Theme.accent : Theme.muted;font.family:Theme.font;font.pixelSize:12;horizontalAlignment:Text.AlignHCenter;verticalAlignment:Text.AlignVCenter}
                    background:Rectangle{color:menuItem.highlighted ? Theme.raised : "transparent";radius:4}
                }
                AppMenu {
                    objectName:"fileMenu";title:qsTranslate("Tingmu", "文件")
                    Action{text:qsTranslate("Tingmu", "打开视频…    Ctrl+O");onTriggered:backend.command("openFiles",{})}
                    Action{text:qsTranslate("Tingmu", "添加多个视频…");onTriggered:backend.command("addFiles",{})}
                    Action{text:qsTranslate("Tingmu", "打开文件夹…    Ctrl+Shift+O");onTriggered:backend.command("openFolder",{})}
                    MenuSeparator{}
                    Action{text:qsTranslate("Tingmu", "导入播放字幕…");enabled:!!backend.state.mediaPath;onTriggered:backend.command("importSubtitle",{})}
                    Action{text:backend.settings.preparation_workflow === "subtitle_agent" ? qsTranslate("Tingmu", "选择字幕并加入 Agent 队列…") : backend.cueCount ? qsTranslate("Tingmu", "从字幕替换学习文档…") : qsTranslate("Tingmu", "从字幕建立学习文档…");enabled:!!backend.state.mediaPath;onTriggered:backend.command("importStudySubtitle",{})}
                    Action{text:qsTranslate("Tingmu", "导出字幕…");onTriggered:backend.command("exportSubtitle",{})}
                    Action{text:qsTranslate("Tingmu", "导出")+qsTranslate("TingmuLanguage", backend.lessonModel.language || "")+qsTranslate("Tingmu", "译稿…");enabled:!!backend.state.lessonCounts && backend.state.lessonCounts.total>0 && (backend.state.lessonCounts.verified + (backend.state.lessonCounts.provided || 0))===backend.state.lessonCounts.total;onTriggered:backend.command("exportTranslation",{})}
                    Action{text:qsTranslate("Tingmu", "打开数据文件夹");onTriggered:backend.command("openDataFolder",{})}
                    MenuSeparator{}
                    Action{objectName:"globalTextModelsAction";text:qsTranslate("Tingmu", "全局文本模型…");onTriggered:modelManager.open()}
                    Action{objectName:"hideToTrayAction";text:qsTranslate("Tingmu", "收起到系统托盘");enabled:desktopTray.available;onTriggered:desktopTray.hideToTray()}
                    Action{text:qsTranslate("Tingmu", "退出听幕");onTriggered:desktopTray.quitApplication()}
                }
                AppMenu {
                    objectName:"playbackMenu";title:qsTranslate("Tingmu", "播放")
                    Action{text:qsTranslate("Tingmu", "播放 / 暂停    Space");onTriggered:backend.command("playPause",{})}
                    Action{text:qsTranslate("Tingmu", "上一句");onTriggered:backend.command("previousCue",{})}
                    Action{text:qsTranslate("Tingmu", "下一句");onTriggered:backend.command("nextCue",{})}
                    Action{text:qsTranslate("Tingmu", "重听当前句");onTriggered:backend.command("repeatCue",{})}
                    Action{text:qsTranslate("Tingmu", "保存当前画面");enabled:!!backend.state.mediaPath;onTriggered:backend.command("saveFrame",{})}
                    MenuSeparator{}
                    Action{text:qsTranslate("Tingmu", "上一个视频");onTriggered:backend.command("previousMedia",{})}
                    Action{text:qsTranslate("Tingmu", "下一个视频");onTriggered:backend.command("nextMedia",{})}
                    Action{text:qsTranslate("Tingmu", "全屏    F");onTriggered:if(playerDock.loadedItem)playerDock.loadedItem.toggleFullscreen()}
                }
                AppMenu {
                    objectName:"workspaceMenu";title:qsTranslate("Tingmu", "视图")
                    AppMenu {
                        objectName: "interfaceLanguageMenu"
                        title: qsTranslate("Tingmu", "界面语言 / Language")
                        Action {
                            objectName: "interfaceSimplified"; text: "简体中文"; checkable: true
                            ActionGroup.group: interfaceActions
                            checked: interfaceLanguage.code === "zh-CN"; onTriggered: interfaceLanguage.select("zh-CN")
                        }
                        Action {
                            objectName: "interfaceTraditional"; text: "繁體中文"; checkable: true
                            ActionGroup.group: interfaceActions
                            checked: interfaceLanguage.code === "zh-TW"; onTriggered: interfaceLanguage.select("zh-TW")
                        }
                        Action {
                            objectName: "interfaceEnglish"; text: "English"; checkable: true
                            ActionGroup.group: interfaceActions
                            checked: interfaceLanguage.code === "en"; onTriggered: interfaceLanguage.select("en")
                        }
                    }
                    MenuSeparator{}
                    Action{text:qsTranslate("Tingmu", "学习布局");onTriggered:app.changeView("learn")}
                    Action{text:qsTranslate("Tingmu", "观看布局");onTriggered:app.changeView("watch")}
                    Action{text:qsTranslate("Tingmu", "准备布局");onTriggered:app.changeView("prepare")}
                    MenuSeparator{}
                    Action{text:qsTranslate("Tingmu", "浮动播放器");checkable:true;checked:playerDock.detached;onTriggered:app.openModule("player")}
                    Action{text:qsTranslate("Tingmu", "浮动学习伴侣");checkable:true;checked:companionDock.detached;onTriggered:app.openModule("companion")}
                    Action{text:qsTranslate("Tingmu", "浮动字幕列表");checkable:true;checked:subtitleDock.detached;onTriggered:app.openModule("subtitles")}
                    Action{text:qsTranslate("Tingmu", "独立聊天窗口");checkable:true;checked:chatDock.detached;onTriggered:app.openModule("chat")}
                    Action{text:qsTranslate("Tingmu", "浮动媒体库");checkable:true;checked:libraryDock.detached;onTriggered:app.openModule("library")}
                    Action{text:qsTranslate("Tingmu", "处理队列");checkable:true;checked:queueDock.detached;onTriggered:app.openModule("queue")}
                    MenuSeparator{}
                    Action{text:qsTranslate("Tingmu", "恢复默认布局");onTriggered:app.resetWorkspace()}
                }
                AppMenu {
                    title:qsTranslate("Tingmu", "帮助")
                    Action{text:qsTranslate("Tingmu", "使用与快捷键");onTriggered:helpDialog.open()}
                    Action{text:qsTranslate("Tingmu", "关于听幕");onTriggered:aboutDialog.open()}
                    MenuSeparator{}
                    Action{objectName:"developerModeAction";text:qsTranslate("Tingmu", "开发者模式");checkable:true;checked:!!backend.settings.developer_mode;onTriggered:backend.setting("developer_mode",checked)}
                    MenuItem{objectName:"viewLogsAction";text:qsTranslate("Tingmu", "查看日志");visible:!!backend.settings.developer_mode;onTriggered:developerPanel.open()}
                }
            }
            Item{Layout.fillWidth:true}
            RowLayout {spacing:2;TButton{text:qsTranslate("Tingmu", "观看");quiet:true;checked:app.viewMode==="watch";onClicked:app.changeView("watch")}TButton{text:qsTranslate("Tingmu", "准备");quiet:true;checked:app.viewMode==="prepare";onClicked:app.changeView("prepare")}TButton{objectName:"learningViewButton";text:qsTranslate("Tingmu", "学习");quiet:true;checked:app.viewMode==="learn";onClicked:app.changeView("learn")}}
            Choice{objectName:"themeChoice";Layout.preferredWidth:88;translateOptions:true;model:["森林","纸页","薄雾","石墨"];property var values:["forest","paper","mist","graphite"];currentIndex:Math.max(0,values.indexOf(backend.settings.theme));onActivated:backend.setting("theme",values[currentIndex])}
            TButton{visible:app.width>=980;text:qsTranslate("Tingmu", "对话");quiet:true;onClicked:{chatDock.detached=true;chatDock.save()}}
            TButton{text:app.width>=950 ? qsTranslate("Tingmu", "打开视频") : "";tip:qsTranslate("Tingmu", "打开视频");iconSource:"arrow-up-right";primary:true;onClicked:backend.command("openFiles",{})}
        }
    }
    SplitView {
        id:columns;objectName:"workspaceSplit"
        visible:app.viewMode!=="learn"
        anchors.fill:parent;anchors.leftMargin:12;anchors.rightMargin:12;anchors.bottomMargin:8
        orientation:Qt.Horizontal
        handle:Rectangle {
            implicitWidth:8;implicitHeight:8;color:"transparent"
            Rectangle{anchors.centerIn:parent;width:2;height:30;radius:1;color:SplitHandle.hovered || SplitHandle.pressed ? Theme.accent : Theme.line}
        }
        onResizingChanged:if(!resizing)persist.restart()
        SplitView {
            id:leftColumn;orientation:Qt.Vertical
            visible:app.slotEnabled("library") || app.slotEnabled("queue")
            SplitView.preferredWidth:248;SplitView.minimumWidth:188;SplitView.maximumWidth:460
            handle:Rectangle{implicitHeight:8;implicitWidth:8;color:"transparent";Rectangle{anchors.centerIn:parent;width:30;height:2;color:SplitHandle.hovered || SplitHandle.pressed ? Theme.accent : Theme.line}}
            onResizingChanged:if(!resizing)persist.restart()
            DockPanel { id:libraryDock;objectName:"libraryModule";moduleId:"library";title:qsTranslate("Tingmu", "媒体库");subtitle:String(backend.library.length);workspace:app;visible:app.slotEnabled(moduleId);SplitView.preferredHeight:430;SplitView.fillHeight:!queueDock.visible;SplitView.minimumHeight:app.slotMinimumHeight("library",250);panelContent:Component{LibraryPanel{}} }
            DockPanel { id:queueDock;objectName:"queueModule";moduleId:"queue";title:qsTranslate("Tingmu", "处理队列");subtitle:backend.state.queueRunning ? qsTranslate("Tingmu", "进行中") : String(backend.jobs.length);workspace:app;visible:app.slotEnabled(moduleId);SplitView.fillHeight:true;SplitView.minimumHeight:app.slotMinimumHeight("queue",210);panelContent:Component{QueuePanel{}} }
        }
        SplitView {
            id:centerColumn;orientation:Qt.Vertical
            visible:app.slotEnabled("player") || app.slotEnabled("subtitles")
            SplitView.fillWidth:true;SplitView.minimumWidth:420
            handle:Rectangle{implicitHeight:8;implicitWidth:8;color:"transparent";Rectangle{anchors.centerIn:parent;width:30;height:2;color:SplitHandle.hovered || SplitHandle.pressed ? Theme.accent : Theme.line}}
            onResizingChanged:if(!resizing)persist.restart()
            DockPanel { id:playerDock;objectName:"playerModule";moduleId:"player";title:qsTranslate("Tingmu", "放映");subtitle:backend.state.mediaName || "";workspace:app;visible:app.slotEnabled(moduleId);SplitView.preferredHeight:490;SplitView.fillHeight:!subtitleDock.visible;SplitView.minimumHeight:app.slotMinimumHeight("player",300);panelContent:Component{PlayerPanel{workspace:app}} }
            DockPanel { id:subtitleDock;objectName:"subtitleModule";moduleId:"subtitles";title:qsTranslate("Tingmu", "字幕");subtitle:String(backend.cueCount);workspace:app;visible:app.slotEnabled(moduleId);SplitView.fillHeight:true;SplitView.minimumHeight:app.slotMinimumHeight("subtitles",180);panelContent:Component{SubtitlePanel{}} }
        }
        SplitView {
            id:rightColumn;orientation:Qt.Vertical
            visible:app.slotEnabled("companion") || app.slotEnabled("chat")
            SplitView.preferredWidth:364;SplitView.minimumWidth:280;SplitView.maximumWidth:620
            handle:Rectangle{implicitHeight:8;implicitWidth:8;color:"transparent";Rectangle{anchors.centerIn:parent;width:30;height:2;color:SplitHandle.hovered || SplitHandle.pressed ? Theme.accent : Theme.line}}
            onResizingChanged:if(!resizing)persist.restart()
            DockPanel { id:companionDock;objectName:"companionModule";moduleId:"companion";title:qsTranslate("Tingmu", "学习伴侣");subtitle:"";workspace:app;visible:app.slotEnabled(moduleId);SplitView.preferredHeight:460;SplitView.minimumHeight:app.slotMinimumHeight("companion",220);panelContent:Component{CompanionPanel{workspace:app}} }
            DockPanel { id:chatDock;objectName:"chatModule";moduleId:"chat";title:qsTranslate("Tingmu", "对话");subtitle:app.chatSubtitle();workspace:app;visible:app.slotEnabled(moduleId);SplitView.fillHeight:true;SplitView.minimumHeight:app.slotMinimumHeight("chat",190);panelContent:Component{ChatPanel{}} }
        }
    }
    LearningPanel{id:learningPanel;anchors.fill:parent;visible:app.viewMode==="learn";workspace:app}
    DropArea {
        anchors.fill:parent
        onDropped:function(drop){if(drop.hasUrls){backend.command("addPaths",{paths:drop.urls.map(function(url){return url.toString()})});drop.acceptProposedAction()}}
    }
    footer:Rectangle {
        height:30;color:Theme.bg
        RowLayout {
            anchors.fill:parent;anchors.leftMargin:20;anchors.rightMargin:20;spacing:9
            Rectangle{width:5;height:5;radius:3;color:backend.state.error ? Theme.error : Theme.accent}
            Text { objectName:"applicationStatus";textFormat:Text.PlainText;Layout.fillWidth:true;text:qsTranslate("TingmuStatus", backend.state.error || backend.state.status || "") || qsTranslate("Tingmu", "就绪");color:backend.state.error ? Theme.error : Theme.muted;font.family:Theme.font;font.pixelSize:10;elide:Text.ElideRight}
            TButton{visible:!!backend.state.error;iconSource:"close";quiet:true;implicitHeight:24;tip:qsTranslate("Tingmu", "关闭提示");onClicked:backend.command("dismissError",{})}
            TButton { visible: !!backend.state.modelDownloading; text: qsTranslate("Tingmu", "查看下载"); quiet: true
                implicitHeight: 24; onClicked: activeDownload.open() }
            Text { textFormat:Text.PlainText;text:!backend.settings.text_models_enabled ? qsTranslate("Tingmu", "文本模型已停用") : backend.state.cloudEnabled ? qsTranslate("Tingmu", "允许远程文本 API") : qsTranslate("Tingmu", "文本仅用本机 API");color:Theme.muted;font.family:Theme.font;font.pixelSize:10}
        }
    }
    AppDialog {
        id:helpDialog;title:qsTranslate("Tingmu", "使用听幕");anchors.centerIn:parent;width:470;modal:true;standardButtons:Dialog.Close
        background:Rectangle{color:Theme.panel;radius:10;border.color:Theme.line}
        contentItem:Text { textFormat:Text.PlainText;text:qsTranslate("Tingmu", "1. 添加视频或打开文件夹，点击文件开始播放。\n2. 勾选需要学习的视频，加入处理队列并开始。\n3. 点击字幕定位，点击伴侣里的词语收藏。\n\n拖动模块标题可弹出独立窗口；↙ 放回原位置。\n拖动模块间的细线，调整工作区比例。\n设置分别位于每个模块右上角的齿轮。\n\n空格　播放 / 暂停\n← / →　前后 5 秒\n↑ / ↓　上一句 / 下一句\nF　全屏　　Esc　退出全屏\nCtrl+O　打开视频\nCtrl+Enter　发送对话");color:Theme.text;font.family:Theme.font;font.pixelSize:13;wrapMode:Text.Wrap;lineHeight:1.4}
    }
    AppDialog {
        id:aboutDialog;title:qsTranslate("Tingmu", "关于听幕");anchors.centerIn:parent;width:390;modal:true;standardButtons:Dialog.Close
        background:Rectangle{color:Theme.panel;radius:10;border.color:Theme.line}
        contentItem:ColumnLayout{spacing:14;Text { textFormat:Text.PlainText;text:qsTranslate("Tingmu", "听幕");color:Theme.text;font.family:Theme.font;font.pixelSize:30}Text { textFormat:Text.PlainText;Layout.fillWidth:true;text:qsTranslate("Tingmu", "在故事里，听懂另一种语言。\n\n本地播放与识别，逐句阅读与收藏。\n文本模型的复核和解释仍需对照原声。");color:Theme.muted;font.family:Theme.font;font.pixelSize:13;wrapMode:Text.Wrap;lineHeight:1.4}Text { textFormat:Text.PlainText;text:"VERSION " + (backend.state.version || "3");color:Theme.accent;font.family:Theme.mono;font.pixelSize:11}}
    }
}
