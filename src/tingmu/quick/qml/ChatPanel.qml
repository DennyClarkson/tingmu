import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
Item {
    id:root
    objectName:"chatPanel"
    property bool panelShown: true
    property string editMessageId:""
    property string draftBeforeEdit:""
    function beginEdit(message) {
        if (!editMessageId) draftBeforeEdit = input.text
        editMessageId = message.id || ""
        input.text = message.content || ""
        input.forceActiveFocus()
    }
    function cancelEdit() {
        if (!editMessageId) return
        editMessageId = ""
        input.text = draftBeforeEdit
        draftBeforeEdit = ""
        input.forceActiveFocus()
    }
    Keys.onEscapePressed: function(event) {
        event.accepted = !!editMessageId
        if (event.accepted) cancelEdit()
    }
    property bool showSessions:true
    property bool followTail:true
    property real readingPosition:0
    readonly property bool stopping:!!backend.chatState.chatStopping
    readonly property bool busy:!!backend.chatState.chatBusy || stopping
    readonly property bool compact:height<550
    property var reasoningExpanded:({})
    function toggleReasoning(id){var next=Object.assign({},reasoningExpanded);next[id]=!next[id];reasoningExpanded=next}
    function phaseLabel(message){if(root.stopping && (message.status==="pending" || message.status==="streaming"))return qsTranslate("Tingmu", "正在停止…等待当前连接结束");if(message.status==="cancelled" || message.status==="stopped")return qsTranslate("Tingmu", "已停止生成");if(message.status==="error")return qsTranslate("TingmuStatus", message.error || "") || qsTranslate("Tingmu", "回复失败，可以重试");return ({connecting:qsTranslate("Tingmu", "正在连接…"),thinking:qsTranslate("Tingmu", "正在思考…"),acting:qsTranslate("Tingmu", "正在执行学习操作…"),answering:qsTranslate("Tingmu", "正在生成回复…")})[message.phase] || (message.status==="pending" ? qsTranslate("Tingmu", "正在连接…") : qsTranslate("Tingmu", "正在回复…"))}
    function toolEvents(events){var out=[];events.forEach(function(event){var index=-1;if(event.status!=="running")for(var i=out.length-1;i>=0;i--)if(out[i].name===event.name && out[i].status==="running"){index=i;break}if(index>=0)out[index]=event;else out.push(event)});return out}
    function openSettings(){settingsPopup.open()}
    readonly property var modelOffer: (workspaces.onboarding || {}).proposal || ({})
    readonly property bool boundOffer: modelOffer.origin_session_id === backend.activeSessionId && modelOffer.status === "pending"
    property string draftOffer: ""
    function send() {
        var text = input.text.trim()
        if (!text || busy) return
        if (!editMessageId && boundOffer && draftOffer === modelOffer.id &&
            /^(同意下载|确认下载|取消下载|暂不下载|同意下載|確認下載|取消下載|暫不下載|confirm download|cancel download)[。.!！]?$/i.test(text)) {
            if (workspaces.decideProposal(draftOffer, /^(同意|确认|確認|confirm)/i.test(text))) input.clear()
            return
        }
        backend.command(editMessageId ? "editChat" : "sendChat", {id:editMessageId, text:text})
        editMessageId=""; draftBeforeEdit=""; input.clear(); followTail=true
    }
    function tail(){if(followTail && controls.item) Qt.callLater(controls.item.scrollToEnd)}
    AppTextArea {
        id: input
        objectName: "chatInput"
        parent: controls.item ? controls.item.inputHost : root
        anchors.fill: parent
        visible: !!controls.item
        enabled: !root.busy
        placeholderText: root.boundOffer ? qsTranslate("Tingmu", "回复「同意下载」或「取消下载」") : qsTranslate("Tingmu", "向听幕提问…   Ctrl+Enter 发送")
        onTextChanged: {
            if (!text) root.draftOffer=""
            else if (!root.draftOffer) root.draftOffer=root.boundOffer ? root.modelOffer.id : "no-proposal"
        }
        Keys.onPressed: function(event) {
            if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && (event.modifiers & Qt.ControlModifier)) {
                root.send()
                event.accepted = true
            }
        }
    }
    Choice {
        id: reasoningChoice
        objectName: "chatReasoningChoice"
        implicitHeight: root.compact ? 30 : Theme.controlHeight
        parent: controls.item ? controls.item.reasoningHost : root
        anchors.fill: parent
        visible: !!controls.item
        translateOptions: true
        model: ["默认 · 沿用模型设置", "不使用推理", "最低", "低", "中", "高", "更高"]
        property var values: ["default", "none", "minimal", "low", "medium", "high", "xhigh"]
        currentIndex: Math.max(0, values.indexOf(backend.settings.companion_reasoning_effort || "default"))
        enabled: !root.busy
        onActivated: backend.setting("companion_reasoning_effort", values[currentIndex])
        AppToolTip {
            visible: reasoningChoice.hovered
            delay: 600
            text: qsTranslate("Tingmu", "默认沿用模型连接设置。各服务支持的强度不同，不支持时会显示明确错误。")
        }
    }
    FirstUseLoader {
        id: controls
        anchors.fill: parent
        anchors.margins: root.compact ? 8 : 14
        requested: root.panelShown
        onLoaded: root.tail()
        sourceComponent: ColumnLayout {
            property alias inputHost: composer
            property alias reasoningHost: reasoningHost
            function scrollToEnd() { history.positionViewAtEnd() }
            spacing:root.compact ? 4 : 12
            RowLayout {
                Layout.fillWidth:true
                TButton{visible:root.width>=660;text:root.showSessions ? qsTranslate("Tingmu", "收起会话") : qsTranslate("Tingmu", "历史会话");implicitHeight:root.compact ? 24 : 32;quiet:true;onClicked:root.showSessions=!root.showSessions}
                Item{Layout.fillWidth:true}
                TButton{objectName:"newChatButton";text:qsTranslate("Tingmu", "新对话");iconSource:"plus";implicitHeight:root.compact ? 24 : 32;quiet:true;enabled:!root.busy;onClicked:backend.command("newChat",{})}
            }
            RowLayout {
                Layout.fillWidth:true;Layout.fillHeight:true;spacing:14
                Rectangle {
                    visible:root.showSessions && root.width>=660
                    Layout.preferredWidth:178;Layout.fillHeight:true;color:Theme.bg;radius:Theme.radiusMd
                    ListView {
                        anchors.fill:parent;anchors.margins:7;spacing:5;clip:true;model:backend.chatSessions || []
                        delegate:TButton{
                            required property var modelData
                            width:ListView.view.width;text:modelData.title || qsTranslate("Tingmu", "新对话");checked:backend.activeSessionId===modelData.id;quiet:true
                            onClicked:backend.command("selectChat",{id:modelData.id})
                        }
                    }
                }
                ColumnLayout {
                    id: chatColumn
                    Layout.fillWidth:true;Layout.minimumWidth:0;Layout.fillHeight:true;spacing:root.compact ? 4 : 10
                    RowLayout{
                        Layout.fillWidth:true
                        Choice{objectName:"chatSessionChoice";Layout.minimumWidth:0;implicitHeight:root.compact ? 30 : Theme.controlHeight;Layout.fillWidth:true;model:backend.chatSessions || [];textRole:"title";currentIndex:Math.max(0,model.findIndex(function(s){return s.id===backend.activeSessionId}));onActivated:backend.command("selectChat",{id:model[currentIndex].id})}
                        TButton {
                            objectName: "renameChatButton"
                            text: qsTranslate("Tingmu", "改名")
                            quiet: true
                            enabled: !!backend.activeSessionId && !root.busy
                            onClicked: {
                                const session = backend.chatSessions.find(s => s.id === backend.activeSessionId)
                                if (!session) return
                                sessionTitle.text = session.title || ""
                                renameDialog.open()
                            }
                        }
                        TButton{iconSource:"trash";tip:qsTranslate("Tingmu", "删除当前会话");quiet:true;danger:true;enabled:!!backend.activeSessionId && !root.busy;onClicked:deleteDialog.open()}
                    }
                    ListView {
                        id:history;objectName:"chatHistory"
                        Layout.fillWidth:true;Layout.fillHeight:true;Layout.minimumHeight:48;clip:true;spacing:24;model:backend.messageModel
                        ScrollBar.vertical:AppScrollBar{}
                        onMovementStarted:{root.followTail=false;root.readingPosition=contentY}
                        onContentYChanged:if(moving)root.readingPosition=contentY
                        onModelChanged:if(!root.followTail)Qt.callLater(function(){history.contentY=Math.max(history.originY,Math.min(root.readingPosition,history.contentHeight-history.height))})
                        onMovementEnded:if(atYEnd)root.followTail=true
                        onCountChanged:root.tail()
                        onContentHeightChanged:root.tail()
                        onHeightChanged:root.tail()
                        delegate: Rectangle {
                            id: message
                            objectName: "chatMessage:" + modelData.id
                            required property var modelData
                            readonly property bool userMessage: modelData.role === "user"
                            readonly property int inset: userMessage ? 12 : 2
                            width: Math.max(0, history.width - 9)
                            height: messageBody.implicitHeight + 2 * inset
                            radius: Theme.radiusMd
                            color: userMessage ? Theme.raised : Theme.transparent
                            border.width: userMessage ? 1 : 0
                            border.color: Theme.line
                            ColumnLayout {
                                id: messageBody
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: message.inset
                                spacing: 8
                                RowLayout{
                                    Layout.fillWidth:true
                                    Text {
                                        text: message.userMessage ? qsTranslate("Tingmu", "你") : qsTranslate("Tingmu", "听幕")
                                        color: message.userMessage ? Theme.muted : Theme.accent
                                        font.family: Theme.font
                                        font.pixelSize: message.userMessage ? 12 : 15
                                        font.weight: Font.DemiBold
                                    }
                                    Text{Layout.fillWidth:true;text:message.modelData.model || "";textFormat:Text.PlainText;color:Theme.muted;font.family:Theme.latin;font.pixelSize:10;elide:Text.ElideRight}
                                    TButton {
                                        objectName: "copyChatMessage"
                                        text: messageCopied.running ? qsTranslate("Tingmu", "已复制") : qsTranslate("Tingmu", "复制")
                                        quiet: true
                                        implicitHeight: 24
                                        onClicked: {
                                            backend.command("copyText", {text: message.modelData.content || ""})
                                            messageCopied.restart()
                                        }
                                        Timer { id: messageCopied; interval: 1400 }
                                    }
                                    TButton{objectName:"editChatMessage";text:qsTranslate("Tingmu", "编辑");visible:message.modelData.role==="user";enabled:!root.busy;quiet:true;implicitHeight:24;onClicked:root.beginEdit(message.modelData)}
                                }
                                ColumnLayout{Layout.fillWidth:true;spacing:8;visible:(message.modelData.tool_events || []).length>0
                                    Repeater {
                                        model: root.toolEvents(message.modelData.tool_events || [])
                                        delegate: RowLayout {
                                            id: toolEvent
                                            required property var modelData
                                            readonly property bool failed: modelData.status === "error" || modelData.status === "failed"
                                            readonly property bool confirming: !!(modelData.result && modelData.result.confirmation_required)
                                            readonly property bool running: modelData.status === "running"
                                            Layout.fillWidth: true
                                            spacing: 8
                                            StatusBadge {
                                                objectName: "chatToolBadge"
                                                Layout.alignment: Qt.AlignTop
                                                tone: toolEvent.failed ? "danger" : toolEvent.confirming ? "warning" : toolEvent.running ? "accent" : "neutral"
                                                text: toolEvent.failed ? qsTranslate("Tingmu", "失败") : toolEvent.running ? qsTranslate("Tingmu", "进行中")
                                                    : toolEvent.confirming ? qsTranslate("Tingmu", "待确认") : qsTranslate("Tingmu", "已完成")
                                            }
                                            Text {
                                                Layout.fillWidth: true
                                                Layout.minimumWidth: 0
                                                text: (qsTranslate("TingmuStatus", toolEvent.modelData.label || "") || toolEvent.modelData.name || qsTranslate("Tingmu", "学习操作"))
                                                    + (typeof toolEvent.modelData.result === "string" && toolEvent.modelData.result ? " · " + toolEvent.modelData.result : "")
                                                wrapMode: Text.Wrap
                                                textFormat: Text.PlainText
                                                color: Theme.muted
                                                font.family: Theme.font
                                                font.pixelSize: 12
                                            }
                                        }
                                    }
                                }
                                ChatReasoning{Layout.fillWidth:true;visible:message.modelData.role==="assistant" && !!message.modelData.reasoning_text;messageData:message.modelData;expanded:!!root.reasoningExpanded[message.modelData.id || ""];stopping:root.stopping && (message.modelData.status==="pending" || message.modelData.status==="streaming");onToggleRequested:root.toggleReasoning(message.modelData.id || "")}
                                MarkdownView{Layout.fillWidth:true;blocks:message.modelData.blocks || [{kind:"code",text:message.modelData.content || "",language:qsTranslate("Tingmu", "文本")}]}
                                Text{objectName:"chatResponseStatus";visible:message.modelData.status==="pending" || message.modelData.status==="streaming" || (message.modelData.status==="cancelled" || message.modelData.status==="stopped") || message.modelData.status==="error";text:root.phaseLabel(message.modelData);color:message.modelData.status==="error" ? Theme.error : Theme.muted;wrapMode:Text.Wrap;Layout.fillWidth:true;font.family:Theme.font;font.pixelSize:11}
                            }
                        }
                        Column{
                            visible:history.count===0;anchors.centerIn:parent;width:Math.min(420,parent.width-24);spacing:16
                            Text{width:parent.width;text:qsTranslate("Tingmu", "今天想学懂什么？");color:Theme.text;font.family:Theme.serif;font.pixelSize:26;wrapMode:Text.Wrap}
                            Text{width:parent.width;text:qsTranslate("Tingmu", "想开一个新工作区，可以说「我要学英语了」。看视频时，也可以问当前句或用收藏练习。");color:Theme.muted;font.family:Theme.font;font.pixelSize:13;wrapMode:Text.Wrap;lineHeight:1.5}
                        }
                    }
                    TButton{Layout.alignment:Qt.AlignHCenter;visible:!root.followTail && !history.atYEnd;text:qsTranslate("Tingmu", "↓ 回到最新回复");quiet:true;onClicked:{root.followTail=true;history.positionViewAtEnd()}}
                    Flow {
                        Layout.fillWidth:true;spacing:6;visible:!root.busy && !root.compact
                        Repeater{model:[qsTranslate("Tingmu", "解释当前句的语气和用法"),qsTranslate("Tingmu", "用我的收藏出一道练习"),qsTranslate("Tingmu", "回顾今天值得记住的表达")];delegate:TButton{required property string modelData;text:modelData;quiet:true;implicitHeight:28;font.pixelSize:11;onClicked:{input.text=modelData;input.forceActiveFocus()}}}
                    }
                    RowLayout {
                        objectName: "chatEditBanner"
                        visible: !!root.editMessageId
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: qsTranslate("Tingmu", "正在编辑消息 · 发送后将重新生成其后的回答")
                            wrapMode: Text.Wrap
                            color: Theme.accent
                            font.family: Theme.font
                            font.pixelSize: 11
                        }
                        TButton {
                            objectName: "cancelChatEditButton"
                            text: qsTranslate("Tingmu", "取消编辑")
                            quiet: true
                            implicitHeight: 26
                            onClicked: root.cancelEdit()
                        }
                    }
                    Item {
                        id: composer
                        Layout.fillWidth: true
                        Layout.preferredHeight: root.compact ? (root.height < 300 ? 48 : 64) : 104
                    }
                    GridLayout {
                        id: toolbar
                        Layout.fillWidth: true
                        Layout.minimumWidth: 0
                        readonly property bool wide: chatColumn.width >= (interfaceLanguage.code === "en" ? 860 : 700)
                        columns: wide ? 3 : 2
                        columnSpacing: 6
                        rowSpacing: root.compact ? 2 : 5
                        RowLayout {
                            Layout.row: 0
                            Layout.column: 0
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            spacing: 3
                            Choice{objectName:"chatModelChoice";implicitHeight:root.compact ? 30 : Theme.controlHeight;Layout.fillWidth:true;Layout.minimumWidth:65;optionLabel:(i,value)=>i===0 ? qsTranslate("Tingmu",value) : value;model:[{id:"",label:"默认对话模型"}].concat(backend.modelProfiles || []);textRole:"label";currentIndex:Math.max(0,model.findIndex(function(p){return p.id===backend.settings.companion_profile_id}));onActivated:backend.command("selectModel",{role:"companion",id:model[currentIndex].id})}
                            TButton{text:qsTranslate("Tingmu", "模型");quiet:true;onClicked:backend.requestDialog("models",root,{})}
                        }
                        RowLayout {
                            Layout.row: toolbar.wide ? 0 : 1
                            Layout.column: toolbar.wide ? 1 : 0
                            Layout.columnSpan: toolbar.wide ? 1 : 2
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            spacing: 4
                            Text{text:qsTranslate("Tingmu", "思考强度");color:Theme.muted;font.family:Theme.font;font.pixelSize:11}
                            Item {
                                id: reasoningHost
                                Layout.fillWidth: true
                                Layout.minimumWidth: 50
                                implicitWidth: reasoningChoice.implicitWidth
                                implicitHeight: reasoningChoice.implicitHeight
                            }
                            TButton{objectName:"chatTeachingPreferencesButton";text:qsTranslate("Tingmu", "讲解偏好");quiet:true;onClicked:backend.requestDialog("teaching",root,{})}
                        }
                        RowLayout {
                            Layout.row: 0
                            Layout.column: toolbar.wide ? 2 : 1
                            Layout.alignment: Qt.AlignRight
                            spacing: 4
                            TButton{text:qsTranslate("Tingmu", "重试");quiet:true;enabled:backend.messageModel.count>0 && !root.busy;onClicked:{root.followTail=true;backend.command("retryChat",{})}}
                            TButton{objectName:"stopChatButton";danger:true;visible:root.busy;text:root.stopping ? qsTranslate("Tingmu", "停止中") : qsTranslate("Tingmu", "停止");enabled:!root.stopping;onClicked:backend.command("stopChat",{})}
                            TButton{objectName:"sendChatButton";visible:!root.busy;text:qsTranslate("Tingmu", "发送");primary:true;enabled:!root.busy && input.text.trim().length>0;onClicked:root.send()}
                        }
                    }
                    Text{objectName:"chatToolStatus";Layout.fillWidth:true;visible:!!backend.chatState.chatToolStatus;text:qsTranslate("TingmuStatus", backend.chatState.chatToolStatus || "");color:backend.chatState.chatToolPhase==="error" ? Theme.error : Theme.accent;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
                    Text{Layout.fillWidth:true;visible:!!backend.chatState.error;text:qsTranslate("TingmuStatus", backend.chatState.error || "");color:Theme.error;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
                }
            }
        }
    }
    SettingPopup{id:settingsPopup;contentItem:ColumnLayout{spacing:12;Text{text:qsTranslate("Tingmu", "对话设置");color:Theme.text;font.family:Theme.font;font.pixelSize:17}TextModelPolicy{Layout.fillWidth:true}TButton{text:qsTranslate("Tingmu", "管理模型连接");onClicked:backend.requestDialog("models",root,{})}Text{Layout.fillWidth:true;text:qsTranslate("Tingmu", "对话模型接收字幕、收藏和对话文本。外部语音识别可在字幕处理设置中单独启用。");color:Theme.muted;font.family:Theme.font;font.pixelSize:12;wrapMode:Text.Wrap}}}
    AppDialog {
        id: renameDialog
        objectName: "renameChatDialog"
        width: 380
        title: qsTranslate("Tingmu", "会话名称")
        anchors.centerIn: parent
        modal: true
        standardButtons: Dialog.Ok | Dialog.Cancel
        contentItem: Field {
            id: sessionTitle
            objectName: "chatSessionTitle"
            implicitWidth: 280
            placeholderText: qsTranslate("Tingmu", "输入名称")
            onAccepted: if (text.trim()) renameDialog.accept()
        }
        onOpened: {
            sessionTitle.forceActiveFocus()
            sessionTitle.selectAll()
        }
        onAccepted: if (sessionTitle.text.trim())
            backend.command("renameChat", {id: backend.activeSessionId, title: sessionTitle.text.trim()})
    }
    AppDialog {
        id: deleteDialog
        objectName: "deleteChatDialog"
        width: 380
        title: qsTranslate("Tingmu", "删除这个会话？")
        anchors.centerIn: parent
        modal: true
        destructiveAccept: true
        acceptText: qsTranslate("Tingmu", "删除")
        standardButtons: Dialog.Ok | Dialog.Cancel
        contentItem: Text {
            text: qsTranslate("Tingmu", "仅删除当前会话，收藏和学习笔记会保留。")
            wrapMode: Text.Wrap
            color: Theme.text
            font.family: Theme.font
        }
        onAccepted: backend.command("deleteChat", {id: backend.activeSessionId})
    }
}
