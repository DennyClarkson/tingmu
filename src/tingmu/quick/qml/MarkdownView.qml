import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
ColumnLayout {
    id:root
    property var blocks:[]
    property int textSize:14
    spacing:12
    Repeater {
        model:root.blocks
        delegate:ColumnLayout {
            id:block
            required property var modelData
            Layout.fillWidth:true
            spacing:0
            Rectangle {
                visible:block.modelData.kind==="code"
                Layout.fillWidth:true;implicitHeight:34;color:Theme.raised;radius:5
                RowLayout{anchors.fill:parent;anchors.leftMargin:12;anchors.rightMargin:6
                    Text{text:block.modelData.language || "代码";textFormat:Text.PlainText;color:Theme.muted;font.family:Theme.latin;font.pixelSize:12;Layout.fillWidth:true}
                    TButton{objectName:"copyCodeButton";text:"复制代码";quiet:true;implicitHeight:26;onClicked:backend.command("copyText",{text:block.modelData.text || ""})}
                }
            }
            Rectangle {
                Layout.fillWidth:true
                implicitHeight:body.implicitHeight+(block.modelData.kind==="code" ? 24 : 0)
                color:block.modelData.kind==="code" ? Theme.bg : "transparent"
                radius:4
                TextEdit {
                    id:body
                    objectName:"markdownBody"
                    function formatDocument(){if(block.modelData.kind!=="code")formatTimer.restart()}
                    Timer{id:formatTimer;interval:0;onTriggered:backend.formatMarkdown(body.textDocument,String(Theme.accent),String(Theme.line))}
                    Component.onCompleted:formatDocument()
                    onTextChanged:formatDocument()
                    Connections{target:Theme;function onNameChanged(){body.formatDocument()}}
                    anchors.left:parent.left;anchors.right:parent.right;anchors.top:parent.top
                    anchors.margins:block.modelData.kind==="code" ? 12 : 0
                    text:block.modelData.text || ""
                    textFormat:block.modelData.kind==="code" ? TextEdit.PlainText : TextEdit.MarkdownText
                    readOnly:true;selectByMouse:true;wrapMode:TextEdit.Wrap
                    color:Theme.text;selectionColor:Theme.selection;selectedTextColor:Theme.text
                    font.family:block.modelData.kind==="code" ? Theme.mono : Theme.font
                    font.pixelSize:block.modelData.kind==="code" ? root.textSize-1 : root.textSize
                    onLinkActivated:function(link){backend.command("openLink",{url:link})}
                }
            }
        }
    }
}
