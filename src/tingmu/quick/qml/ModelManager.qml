import QtQuick
import QtQuick.Window
import QtQuick.Controls.Basic
import QtQuick.Layouts
Window {
    id:root
    objectName:"modelManagerWindow"
    title:"听幕 · 模型管理"
    width:840;height:720;minimumWidth:680;minimumHeight:580
    color:Theme.bg;visible:false
    property string profileId:""
    property string provider:"openai"
    property string protocol:"openai"
    property bool advanced:false
    readonly property var discoveredChoices:{var profile=(backend.modelProfiles || []).find(function(p){return p.id===root.profileId});return profile ? (profile.models || []) : []}
    function open(){backend.command("refreshModels",{});if(!root.profileId && !label.text)root.populate((backend.modelPresets || [])[0] || {});show();raise();requestActivate()}
    function populate(p){
        profileId=p.id || "";provider=p.provider || "openai";protocol=p.protocol || "openai"
        label.text=p.label || "";endpoint.text=p.base_url || "";modelName.text=p.model || ""
        envName.text=p.env_var || "";keySource.currentIndex=p.key_source==="manual" ? 1 : p.key_source==="none" ? 2 : 0
        maxTokens.text=String(p.max_tokens || 4096);reasoning.currentIndex=Math.max(0,reasoning.values.indexOf(p.reasoning_effort || ""))
        secret.clear()
    }
    onClosing:secret.clear()
    Connections{target:Theme;function onClosingChanged(){if(Theme.closing){secret.clear();root.hide()}}}
    Connections{target:backend;function onModelsChanged(){if(!root.profileId && label.text){var p=(backend.modelProfiles || []).find(function(p){return p.label===label.text.trim() && p.base_url.replace(/\/$/,"")===endpoint.text.trim().replace(/\/$/,"") && p.model===modelName.text.trim()});if(p)root.profileId=p.id}}}
    RowLayout {
        anchors.fill:parent;anchors.margins:20;spacing:20
        ColumnLayout {
            Layout.preferredWidth:200;Layout.fillHeight:true;spacing:12
            Text{text:"模型连接";color:Theme.text;font.family:Theme.serif;font.pixelSize:24}
            TButton{objectName:"newModelButton";text:"＋ 新建连接";Layout.fillWidth:true;onClicked:root.populate((backend.modelPresets || [])[0] || {})}
            ListView {
                Layout.fillWidth:true;Layout.fillHeight:true;clip:true;spacing:6;model:backend.modelProfiles || []
                delegate:Rectangle {
                    required property var modelData
                    width:ListView.view.width;height:68;radius:7;color:root.profileId===modelData.id ? Theme.selection : Theme.panel
                    Column{anchors.fill:parent;anchors.margins:12;spacing:5
                        Text{width:parent.width;text:modelData.label || "未命名";textFormat:Text.PlainText;color:Theme.text;font.family:Theme.font;font.pixelSize:13;elide:Text.ElideRight}
                        Text{width:parent.width;text:modelData.model || "未选择模型";textFormat:Text.PlainText;color:Theme.muted;font.family:Theme.latin;font.pixelSize:12;elide:Text.ElideRight}
                    }
                    MouseArea{anchors.fill:parent;onClicked:root.populate(modelData)}
                }
                Text{anchors.centerIn:parent;width:parent.width;visible:parent.count===0;text:"添加一个模型连接，\n供讲解、复核或对话使用。";color:Theme.muted;font.family:Theme.font;font.pixelSize:12;wrapMode:Text.Wrap;lineHeight:1.5}
            }
            Text{Layout.fillWidth:true;text:"密钥受本机保护。将便携目录复制到另一台电脑后，需重新输入密钥或配置环境变量。";color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap;lineHeight:1.5}
        }
        Rectangle{Layout.fillHeight:true;width:1;color:Theme.line}
        ScrollView {
            id:formScroll
            Layout.fillWidth:true;Layout.fillHeight:true;clip:true;contentWidth:availableWidth
            ColumnLayout {
                width:formScroll.availableWidth;spacing:12
                Text{text:root.profileId ? "编辑连接" : "新建连接";color:Theme.text;font.family:Theme.serif;font.pixelSize:24}
                Text{text:"服务预设";color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                Choice{objectName:"modelPresetChoice";Layout.fillWidth:true;model:backend.modelPresets || [];textRole:"label";currentIndex:model.findIndex(function(p){return p.provider===root.provider});onActivated:{var p=model[currentIndex];root.populate(Object.assign({},p,{id:""}))}}
                Text{visible:protocolChoice.visible;text:"接口协议";color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                Choice{id:protocolChoice;objectName:"modelProtocolChoice";visible:root.provider==="openai" || root.provider==="custom";Layout.fillWidth:true;model:["Chat Completions","Responses（思考摘要）"];currentIndex:root.protocol==="responses" ? 1 : 0;onActivated:root.protocol=currentIndex===1 ? "responses" : "openai"}
                Text{visible:protocolChoice.visible;Layout.fillWidth:true;text:"由服务和模型决定是否返回思考内容。Responses 可请求思考摘要；兼容服务需明确支持该接口。已有连接不会自动切换。";color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
                Text{text:"显示名称";color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                Field{id:label;objectName:"modelLabel";Layout.fillWidth:true;placeholderText:"例如：学习搭档 / 本地 Ollama"}
                Text{text:"API 地址（Base URL）";color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                Field{id:endpoint;objectName:"modelBaseUrl";Layout.fillWidth:true;placeholderText:"https://api.openai.com/v1"}
                Text{Layout.fillWidth:true;text:"支持兼容 OpenAI 的端点，也可以使用本机 Ollama。地址和模型名称请以服务方为准。";color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
                Text{text:"凭据来源";color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                Choice{id:keySource;objectName:"modelKeySource";Layout.fillWidth:true;model:["环境变量","输入密钥 · 本机保护","无需密钥 · 本地服务"]}
                Field{id:envName;objectName:"modelEnvName";Layout.fillWidth:true;visible:keySource.currentIndex===0;placeholderText:"OPENAI_API_KEY"}
                Field{id:secret;objectName:"modelSecret";Layout.fillWidth:true;visible:keySource.currentIndex===1;echoMode:TextInput.Password;placeholderText:root.profileId ? "输入新密钥以替换；留空保留原密钥" : "API 密钥（保存后不再显示）"}
                Text{Layout.fillWidth:true;visible:!!backend.state.error;text:backend.state.error || "";color:Theme.error;font.family:Theme.font;font.pixelSize:12;wrapMode:Text.Wrap}
                Text{text:"模型名称（可先留空，保存后加载列表）";color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                Field{id:modelName;objectName:"modelName";Layout.fillWidth:true;placeholderText:"手动输入模型 ID"}
                RowLayout {
                    Layout.fillWidth:true
                    Choice{objectName:"discoveredModelChoice";Layout.fillWidth:true;model:root.discoveredChoices;onActivated:modelName.text=currentText}
                    TButton{text:backend.state.modelBusy ? "加载中…" : "加载列表";enabled:!!root.profileId && !backend.state.modelBusy;onClicked:backend.command("discoverModels",{id:root.profileId})}
                }
                Text{Layout.fillWidth:true;text:!root.profileId ? "先保存连接，再加载模型列表；也可直接手填模型。" : root.discoveredChoices.length ? "此连接已缓存 "+root.discoveredChoices.length+" 个模型；可选择或手动输入模型 ID。" : "此连接还没有模型列表，可加载列表或直接手填模型。";color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
                RowLayout{
                    TButton{objectName:"saveModelButton";text:"保存连接";primary:true;enabled:label.text.trim() && endpoint.text.trim() && maxTokens.acceptableInput && !backend.state.modelBusy;onClicked:{var payload={id:root.profileId,label:label.text.trim(),provider:root.provider,protocol:root.protocol,base_url:endpoint.text.trim(),model:modelName.text.trim(),key_source:["environment","manual","none"][keySource.currentIndex],env_var:envName.text.trim(),max_tokens:Number(maxTokens.text),reasoning_effort:reasoning.values[reasoning.currentIndex]};if(secret.text)payload.apiKey=secret.text;backend.command("saveModel",payload);secret.clear()}}
                    TButton{text:"删除连接";enabled:!!root.profileId && !backend.state.modelBusy;onClicked:deleteConfirm.open()}
                }
                TButton{text:root.advanced ? "收起高级选项" : "高级选项";quiet:true;onClicked:root.advanced=!root.advanced}
                ColumnLayout{visible:root.advanced;Layout.fillWidth:true;spacing:10
                    Text{text:"最大输出 Token 数";color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                    Field{id:maxTokens;objectName:"modelMaxTokens";Layout.fillWidth:true;text:"4096";validator:IntValidator{bottom:256;top:128000}}
                    Text{text:"推理强度";color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                    Choice{id:reasoning;objectName:"modelReasoningEffort";Layout.fillWidth:true;model:["自动","none","low","medium","high","xhigh"];property var values:["","none","low","medium","high","xhigh"]}
                    Text{Layout.fillWidth:true;text:"仅支持该参数的模型会使用推理强度；输出上限可按服务能力设置为 256–128000。";color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
                }
                TButton{text:"复制为新模型连接";enabled:!!root.profileId;onClicked:{root.profileId="";label.text=label.text+" · 副本";secret.clear()}}
                Rectangle{Layout.fillWidth:true;height:1;color:Theme.line}
                Text{text:"用于哪些任务";color:Theme.text;font.family:Theme.font;font.pixelSize:14}
                Repeater {
                    model:[{role:"annotation",label:"批量讲解"},{role:"review",label:"全文复核"},{role:"companion",label:"学习对话"}]
                    delegate:RowLayout{
                        required property var modelData
                        Layout.fillWidth:true
                        Text{Layout.preferredWidth:80;text:modelData.label;color:Theme.muted;font.family:Theme.font;font.pixelSize:12}
                        Choice{Layout.fillWidth:true;model:[{id:"",label:"沿用默认设置"}].concat(backend.modelProfiles || []);textRole:"label";currentIndex:Math.max(0,model.findIndex(function(p){return p.id===backend.settings[modelData.role+"_profile_id"]}));onActivated:backend.command("selectModel",{role:modelData.role,id:model[currentIndex].id})}
                    }
                }
            }
        }
    }
    AppDialog{width:380;id:deleteConfirm;anchors.centerIn:parent;title:"删除模型连接？";modal:true;standardButtons:Dialog.Ok | Dialog.Cancel;background:Rectangle{color:Theme.panel;border.color:Theme.line;radius:8}onAccepted:{backend.command("deleteModel",{id:root.profileId});root.populate({})}contentItem:Text{text:"使用此连接的任务将需要重新选择模型。";color:Theme.text;font.family:Theme.font}}
}
