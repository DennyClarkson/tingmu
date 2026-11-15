import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
Rectangle {
    id:root
    objectName:"chatReasoning"
    property var messageData:({})
    property bool expanded:false
    property bool stopping:false
    readonly property bool thinking:!stopping && messageData.phase==="thinking" && (messageData.status==="pending" || messageData.status==="streaming")
    property real observedAt:Date.now()
    property real now:observedAt
    readonly property int elapsedSeconds:Math.floor((Math.max(0,Number(messageData.reasoning_elapsed_ms || 0))+(thinking ? Math.max(0,now-observedAt) : 0))/1000)
    signal toggleRequested()
    onMessageDataChanged:{observedAt=Date.now();now=observedAt}
    Timer{interval:250;repeat:true;running:root.thinking;onTriggered:root.now=Date.now()}
    implicitHeight:body.implicitHeight+16
    color:Theme.bg;radius:8;border.color:Theme.line
    ColumnLayout {
        id:body
        anchors.fill:parent;anchors.margins:8;spacing:8
        RowLayout {
            Layout.fillWidth:true;spacing:4
            TButton{objectName:"toggleChatReasoning";Layout.fillWidth:true;quiet:true;implicitHeight:30;text:(root.expanded ? "▾ " : "▸ ")+(root.messageData.reasoning_kind==="summary" ? "思考摘要" : "思考过程")+" · "+root.elapsedSeconds+" 秒";onClicked:root.toggleRequested()}
            TButton{objectName:"copyChatReasoning";text:"复制";quiet:true;implicitHeight:30;onClicked:backend.command("copyText",{text:root.messageData.reasoning_text || ""})}
        }
        Text{objectName:"chatReasoningProgress";Layout.fillWidth:true;visible:root.thinking || root.stopping;text:root.stopping ? "正在停止…等待当前连接结束" : "思考中…";color:Theme.muted;font.family:Theme.font;font.pixelSize:11;wrapMode:Text.Wrap}
        ScrollView {
            id:scroll;objectName:"chatReasoningScroll"
            visible:root.expanded;Layout.fillWidth:true;Layout.preferredHeight:Math.min(220,reasoningText.implicitHeight+8)
            clip:true;contentWidth:availableWidth
            TextArea {
                id:reasoningText;objectName:"chatReasoningText"
                text:root.messageData.reasoning_text || "";textFormat:TextEdit.PlainText
                readOnly:true;selectByMouse:true;wrapMode:TextEdit.Wrap
                color:Theme.muted;selectionColor:Theme.selection;selectedTextColor:Theme.text
                font.family:Theme.font;font.pixelSize:12;padding:4;background:null
                Accessible.name:root.messageData.reasoning_kind==="summary" ? "模型返回的思考摘要" : "模型返回的思考过程"
            }
        }
    }
}
