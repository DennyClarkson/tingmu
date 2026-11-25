import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Layouts
Window {
    id:root
    objectName:"modelManagerWindow"
    title:qsTranslate("Tingmu", "听幕 · 全局文本模型")
    width:840;height:720;minimumWidth:680;minimumHeight:580
    color:Theme.bg;visible:false
    property string profileId:""
    property string provider:"openai"
    property string protocol:"openai"
    property bool advanced:false
    property bool showRoles:false
    function roleStatus(role) {
        var fallback = role === "annotation" ? qsTranslate("Tingmu", "词典") : qsTranslate("Tingmu", "已停用")
        if (!backend.settings.text_models_enabled || (role === "review" && !backend.settings.review_enabled &&
                backend.settings.segmentation_mode !== "llm_regroup"))
            return fallback
        var id = backend.settings[role + "_profile_id"]
        var profile = (backend.modelProfiles || []).find(function(p){ return p.id === id })
        if (id && !profile) return qsTranslate("Tingmu", "待选择")
        if (profile && profile.is_loopback) return qsTranslate("Tingmu", "本机 API")
        if (role === "annotation" && !id && backend.settings.provider === "local") return qsTranslate("Tingmu", "词典")
        return backend.settings.cloud_text_enabled ? qsTranslate("Tingmu", "远程 API") : fallback
    }
    readonly property var discoveredChoices:{var profile=(backend.modelProfiles || []).find(function(p){return p.id===root.profileId});return profile ? (profile.models || []) : []}
    function open() {
        form.active = true
        backend.command("refreshModels", {})
        if (!profileId && form.item.blank) populate((backend.modelPresets || [])[0] || {})
        show(); raise(); requestActivate()
    }
    function populate(profile) { form.active = true; form.item.populate(profile) }
    onClosing: if (form.item) form.item.clearSecret()
    Connections {
        target: Theme
        function onClosingChanged() {
            if (Theme.closing) { if (form.item) form.item.clearSecret(); root.hide() }
        }
    }
    FirstUseLoader {
        id: form
        anchors.fill: parent
        anchors.margins: 20
        requested: root.visible
        sourceComponent: RowLayout {
            id:editor
            spacing:20
            readonly property bool blank: !label.text
            property string savedFields: ""
            property string formError: ""
            property string incompleteProfileId: ""
            readonly property bool dirty: JSON.stringify(fields()) !== savedFields || !!secret.text
            readonly property bool valid: !!label.text.trim() && !!endpoint.text.trim() && maxTokens.acceptableInput
            function fields() {
                return {label:label.text.trim(), provider:root.provider, protocol:root.protocol,
                        base_url:endpoint.text.trim(), model:modelName.text.trim(),
                        key_source:["environment", "manual", "none"][keySource.currentIndex],
                        env_var:envName.text.trim(), max_tokens:Number(maxTokens.text),
                        reasoning_effort:reasoning.values[reasoning.currentIndex]}
            }
            function saveConnection() {
                formError = ""
                if (!valid) {
                    formError = qsTranslate("Tingmu", "请补全显示名称、API 地址和有效的输出上限。")
                    return false
                }
                const payload = fields()
                payload.id = root.profileId
                if (secret.text) payload.apiKey = secret.text
                const savedId = backend.command("saveModel", payload)
                if (!savedId) return false
                root.profileId = savedId
                secret.clear()
                savedFields = JSON.stringify(fields())
                return true
            }
            function clearSecret() { secret.clear() }
            function populate(p){
                showRoles=false;advanced=false
                profileId=p.id || "";provider=p.provider || "openai";protocol=p.protocol || "openai"
                label.text=p.label || "";endpoint.text=p.base_url || "";modelName.text=p.model || ""
                envName.text=p.env_var || "";keySource.currentIndex=p.key_source==="manual" ? 1 : p.key_source==="none" ? 2 : 0
                maxTokens.text=String(p.max_tokens || 4096);reasoning.currentIndex=Math.max(0,reasoning.values.indexOf(p.reasoning_effort || ""))
                secret.clear()
                formError="";incompleteProfileId="";savedFields=JSON.stringify(fields())
            }

            ColumnLayout {
                Layout.preferredWidth:root.width < 760 ? 180 : 210;Layout.maximumWidth:222;Layout.fillHeight:true;spacing:10
                Text{Layout.fillWidth:true;wrapMode:Text.Wrap;text:qsTranslate("Tingmu", "全局文本模型");color:Theme.text;font.family:Theme.serif;font.pixelSize:22}
                NavButton{objectName:"globalModelRoles";text:!root.showRoles && editor.dirty ? qsTranslate("Tingmu", "保存并分配任务") : qsTranslate("Tingmu", "任务分配");Layout.fillWidth:true;checked:root.showRoles;enabled:!backend.modelState.modelBusy;onClicked:{if(!editor.dirty || editor.saveConnection())root.showRoles=true}}
                Text{objectName:"modelManagerError";Layout.fillWidth:true;visible:!!text;text:editor.formError || qsTranslate("TingmuStatus", backend.modelState.error || "");color:Theme.error;font.family:Theme.font;font.pixelSize:12;wrapMode:Text.Wrap}
                Text{text:qsTranslate("Tingmu", "连接");color:Theme.muted;font.family:Theme.font;font.pixelSize:13}
                TButton{objectName:"newModelButton";text:qsTranslate("Tingmu", "＋ 新建连接");Layout.fillWidth:true;onClicked:root.populate((backend.modelPresets || [])[0] || {})}
                ListView {
                    Layout.fillWidth:true;Layout.fillHeight:true;clip:true;spacing:6;model:backend.modelProfiles || []
                    ScrollBar.vertical: AppScrollBar {}
                    delegate: ItemDelegate {
                        id: profileRow
                        required property var modelData
                        readonly property bool selectedProfile: !root.showRoles && root.profileId === modelData.id
                        width: Math.max(0, ListView.view.width - 9)
                        height: 62; padding: 10; leftPadding: 14
                        Accessible.name: modelData.label || qsTranslate("Tingmu", "未命名")
                        background: Rectangle {
                            radius: Theme.radiusSm
                            color: profileRow.selectedProfile ? Theme.accentSoft : profileRow.hovered ? Theme.hover : Theme.panel
                            border.color: profileRow.visualFocus ? Theme.accent : Theme.line
                            Rectangle {
                                visible: profileRow.selectedProfile
                                width: 2; height: 24; radius: 1; color: Theme.accent
                                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                            }
                        }
                        contentItem: Column {
                            spacing: 3
                            Text { width:parent.width; text:profileRow.Accessible.name; textFormat:Text.PlainText; color:Theme.text; font.family:Theme.font; font.pixelSize:13; elide:Text.ElideRight }
                            Text { width:parent.width; text:profileRow.modelData.model || qsTranslate("Tingmu", "未选择模型"); textFormat:Text.PlainText; color:Theme.muted; font.family:Theme.latin; font.pixelSize:12; elide:Text.ElideRight }
                        }
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                        onClicked: root.populate(modelData)
                    }
                    Text{anchors.centerIn:parent;width:parent.width;visible:parent.count===0;text:qsTranslate("Tingmu", "添加一个模型连接，\n供讲解、复核或对话使用。");color:Theme.muted;font.family:Theme.font;font.pixelSize:12;wrapMode:Text.Wrap;lineHeight:1.5}
                }
                TextModelPolicy { Layout.fillWidth:true }
                Text{Layout.fillWidth:true;text:qsTranslate("Tingmu", "密钥受本机保护。将便携目录复制到另一台电脑后，需重新输入密钥或配置环境变量。");color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap;lineHeight:1.5}
            }
            Rectangle{Layout.fillHeight:true;width:1;color:Theme.line}
            AppScrollView {
                id:formScroll;visible:!root.showRoles
                padding:16;rightPadding:20
                background:Rectangle{radius:Theme.radiusMd;color:Theme.panel;border.color:Theme.line}
                Layout.fillWidth:true;Layout.fillHeight:true;clip:true;contentWidth:availableWidth
                ColumnLayout {
                    width:formScroll.availableWidth;spacing:8
                    Text{text:root.profileId ? qsTranslate("Tingmu", "编辑连接") : qsTranslate("Tingmu", "新建连接");color:Theme.text;font.family:Theme.serif;font.pixelSize:22}
                    Text{text:qsTranslate("Tingmu", "服务预设");color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                    Choice{objectName:"modelPresetChoice";Layout.fillWidth:true;translateOptions:true;model:backend.modelPresets || [];textRole:"label";currentIndex:model.findIndex(function(p){return p.provider===root.provider});onActivated:{var p=model[currentIndex];root.populate(Object.assign({},p,{id:"",label:displayText}))}}
                    Text{visible:protocolChoice.visible;text:qsTranslate("Tingmu", "接口协议");color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                    Choice{id:protocolChoice;objectName:"modelProtocolChoice";visible:["openai","custom","lmstudio","local_compatible"].includes(root.provider);Layout.fillWidth:true;translateOptions:true;model:["Chat Completions","Responses（思考摘要）"];currentIndex:root.protocol==="responses" ? 1 : 0;onActivated:root.protocol=currentIndex===1 ? "responses" : "openai"}
                    Text{visible:protocolChoice.visible;Layout.fillWidth:true;text:qsTranslate("Tingmu", "由服务和模型决定是否返回思考内容。Responses 可请求思考摘要；兼容服务需明确支持该接口。已有连接不会自动切换。");color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
                    Rectangle { Layout.fillWidth:true; Layout.topMargin:6; implicitHeight:1; color:Theme.line }
                    Text{text:qsTranslate("Tingmu", "显示名称");color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                    Field{id:label;objectName:"modelLabel";Layout.fillWidth:true;placeholderText:qsTranslate("Tingmu", "例如：学习搭档 / 本地 Ollama")}
                    Text{text:qsTranslate("Tingmu", "API 地址（Base URL）");color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                    Field{id:endpoint;objectName:"modelBaseUrl";Layout.fillWidth:true;placeholderText:"https://api.openai.com/v1"}
                    Text{Layout.fillWidth:true;text:["ollama","lmstudio","local_compatible"].includes(root.provider) ? qsTranslate("Tingmu", "先启动本机服务并加载模型。填写服务的 /v1 基础地址，可选无需密钥、环境变量或本机加密密钥。") : qsTranslate("Tingmu", "填写服务的 API 基础地址与实际模型 ID。");color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
                    Rectangle { Layout.fillWidth:true; Layout.topMargin:6; implicitHeight:1; color:Theme.line }
                    Text{text:qsTranslate("Tingmu", "凭据来源");color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                    Choice{id:keySource;objectName:"modelKeySource";Layout.fillWidth:true;translateOptions:true;model:["环境变量","输入密钥 · 本机保护","无需密钥 · 本地服务"]}
                    Field{id:envName;objectName:"modelEnvName";Layout.fillWidth:true;visible:keySource.currentIndex===0;placeholderText:["ollama","lmstudio","local_compatible"].includes(root.provider) ? qsTranslate("Tingmu", "本机服务的环境变量名") : "OPENAI_API_KEY"}
                    Field{id:secret;objectName:"modelSecret";Layout.fillWidth:true;visible:keySource.currentIndex===1;echoMode:TextInput.Password;placeholderText:root.profileId ? qsTranslate("Tingmu", "输入新密钥以替换；留空保留原密钥") : qsTranslate("Tingmu", "API 密钥（保存后不再显示）")}
                    Text{text:qsTranslate("Tingmu", "模型名称（可先留空，保存后加载列表）");color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                    Field{id:modelName;objectName:"modelName";Layout.fillWidth:true;placeholderText:qsTranslate("Tingmu", "手动输入模型 ID")}
                    RowLayout {
                        Layout.fillWidth:true
                        Choice{objectName:"discoveredModelChoice";Layout.fillWidth:true;model:root.discoveredChoices;currentIndex:model.indexOf(modelName.text);displayText:currentIndex < 0 ? qsTranslate("Tingmu", "选择模型") : currentText;onActivated:(index)=>modelName.text=model[index]}
                        TButton{text:backend.modelState.modelBusy ? qsTranslate("Tingmu", "加载中…") : qsTranslate("Tingmu", "加载列表");enabled:!!root.profileId && !backend.modelState.modelBusy;onClicked:backend.command("discoverModels",{id:root.profileId})}
                    }
                    Text{Layout.fillWidth:true;text:!root.profileId ? qsTranslate("Tingmu", "先保存连接，再加载模型列表；也可直接手填模型。") : root.discoveredChoices.length ? qsTranslate("Tingmu", "此连接已缓存 ")+root.discoveredChoices.length+qsTranslate("Tingmu", " 个模型；可选择或手动输入模型 ID。") : qsTranslate("Tingmu", "此连接还没有模型列表，可加载列表或直接手填模型。");color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
                    RowLayout{
                        TButton{objectName:"saveModelButton";text:qsTranslate("Tingmu", "保存连接");primary:true;enabled:editor.valid && !backend.modelState.modelBusy;onClicked:editor.saveConnection()}
                        TButton{danger:true;text:qsTranslate("Tingmu", "删除连接");enabled:!!root.profileId && !backend.modelState.modelBusy;onClicked:deleteConfirm.open()}
                    }
                    TButton{text:root.advanced ? qsTranslate("Tingmu", "收起高级选项") : qsTranslate("Tingmu", "高级选项");quiet:true;onClicked:root.advanced=!root.advanced}
                    ColumnLayout{visible:root.advanced;Layout.fillWidth:true;spacing:10
                        Text{text:qsTranslate("Tingmu", "最大输出 Token 数");color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                        Field{id:maxTokens;objectName:"modelMaxTokens";Layout.fillWidth:true;text:"4096";validator:IntValidator{bottom:256;top:128000}}
                        Text{text:qsTranslate("Tingmu", "推理强度");color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                        Choice{id:reasoning;objectName:"modelReasoningEffort";Layout.fillWidth:true;translateOptions:true;model:["自动","none","minimal","low","medium","high","xhigh"];property var values:["","none","minimal","low","medium","high","xhigh"]}
                        Text{Layout.fillWidth:true;text:qsTranslate("Tingmu", "仅支持该参数的模型会使用推理强度；输出上限可按服务能力设置为 256–128000。");color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
                    }
                    TButton{text:qsTranslate("Tingmu", "复制为新模型连接");enabled:!!root.profileId;onClicked:{root.profileId="";label.text=label.text+qsTranslate("Tingmu", " · 副本");secret.clear()}}
                }
            }
            ColumnLayout {
                visible:root.showRoles;Layout.fillWidth:true;Layout.fillHeight:true;spacing:16
                Text{text:qsTranslate("Tingmu", "全局任务分配");color:Theme.text;font.family:Theme.serif;font.pixelSize:22}
                Text{Layout.fillWidth:true;text:qsTranslate("Tingmu", "所有工作区共用以下分配。每个工作区可单独设置是否允许远程处理。");color:Theme.muted;font.family:Theme.font;wrapMode:Text.Wrap}
                Repeater {
                    model:[{role:"annotation",label:qsTranslate("Tingmu", "批量讲解")},{role:"review",label:qsTranslate("Tingmu", "全文复核")},{role:"companion",label:qsTranslate("Tingmu", "学习对话")}]
                    delegate:RowLayout{
                        required property var modelData
                        Layout.fillWidth:true
                        Text{Layout.preferredWidth:98;wrapMode:Text.Wrap;text:modelData.label;color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                        Choice{
                            objectName:"globalRole_"+modelData.role;Layout.fillWidth:true
                            optionLabel:(i,value)=>i===0 ? qsTranslate("Tingmu",value) : value
                            model:[{id:"",label:"沿用默认设置"}].concat(backend.modelProfiles || []);textRole:"label"
                            function assignedIndex(){return Math.max(0,model.findIndex(p=>p.id===backend.settings[modelData.role+"_profile_id"]))}
                            currentIndex:assignedIndex()
                            onActivated:function(index){
                                const profile=model[index]
                                editor.incompleteProfileId="";editor.formError=""
                                if(profile.id && !(profile.model || "").trim()){
                                    editor.incompleteProfileId=profile.id
                                    editor.formError=qsTranslate("Tingmu", "请先为连接选择或填写模型 ID 并保存，再分配用途。")
                                }else backend.command("selectModel",{role:modelData.role,id:profile.id})
                                currentIndex=Qt.binding(assignedIndex)
                            }
                        }
                        Text{Layout.preferredWidth:84;wrapMode:Text.Wrap;text:root.roleStatus(modelData.role);color:Theme.muted;font.family:Theme.font;font.pixelSize:11}
                    }
                }
                TButton{objectName:"editIncompleteModel";visible:!!editor.incompleteProfileId;text:qsTranslate("Tingmu", "编辑连接");onClicked:{const p=(backend.modelProfiles || []).find(p=>p.id===editor.incompleteProfileId);if(p)root.populate(p)}}
                Item{Layout.fillHeight:true}
            }
        }
    }
    AppDialog{width:380;id:deleteConfirm;objectName:"deleteModelDialog";destructiveAccept:true;acceptText:qsTranslate("Tingmu", "删除");anchors.centerIn:parent;title:qsTranslate("Tingmu", "删除模型连接？");modal:true;standardButtons:Dialog.Ok | Dialog.Cancel;onAccepted:{backend.command("deleteModel",{id:root.profileId});root.populate({})}contentItem:Text{wrapMode:Text.Wrap;text:qsTranslate("Tingmu", "所有工作区都会移除此连接，对应用途将恢复默认设置。");color:Theme.text;font.family:Theme.font}}
}
