import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
ColumnLayout {
    id:root
    property var blocks:[]
    property int textSize:14
    signal sourceOpened()
    spacing:12
    Repeater {
        model:root.blocks
        delegate:Rectangle {
            id:block
            required property var modelData
            Layout.fillWidth:true
            readonly property bool isCode: modelData.kind === "code"
            Layout.minimumWidth: 0
            implicitHeight: body.implicitHeight + (isCode ? codeHeader.height + 24 : 0)
            color: isCode ? Theme.bg : Theme.transparent
            radius: Theme.radiusMd
            border.width: isCode ? 1 : 0
            border.color: Theme.line
            Item {
                id: codeHeader
                visible: block.isCode
                width: parent.width
                height: 34
                Rectangle {
                    anchors.bottom: parent.bottom
                    anchors.left: parent.left
                    anchors.right: parent.right
                    height: 1
                    color: Theme.line
                }
                RowLayout{anchors.fill:parent;anchors.leftMargin:12;anchors.rightMargin:6
                    Text{text:block.modelData.language || qsTranslate("Tingmu", "代码");textFormat:Text.PlainText;color:Theme.muted;font.family:Theme.latin;font.pixelSize:12;Layout.fillWidth:true}
                    TButton {
                        objectName: "copyCodeButton"
                        text: codeCopied.running ? qsTranslate("Tingmu", "已复制") : qsTranslate("Tingmu", "复制代码")
                        quiet: true
                        implicitHeight: 26
                        onClicked: {
                            backend.command("copyText", {text: block.modelData.text || ""})
                            codeCopied.restart()
                        }
                        Timer { id: codeCopied; interval: 1400 }
                    }
                }
            }
            TextEdit {
                id:body
                objectName:"markdownBody"
                function formatDocument(){if(block.modelData.kind!=="code")formatTimer.restart()}
                Timer{id:formatTimer;interval:0;onTriggered:backend.formatMarkdown(body.textDocument,String(Theme.accent),String(Theme.line))}
                Component.onCompleted:formatDocument()
                onTextChanged:formatDocument()
                Connections{target:Theme;function onNameChanged(){body.formatDocument()}}
                anchors.left:parent.left;anchors.right:parent.right;anchors.top:parent.top
                anchors.leftMargin: block.isCode ? 12 : 0
                anchors.rightMargin: block.isCode ? 12 : 0
                anchors.topMargin: block.isCode ? codeHeader.height + 12 : 0
                text:block.modelData.text || ""
                textFormat:block.modelData.kind==="code" ? TextEdit.PlainText : TextEdit.MarkdownText
                readOnly:true;selectByMouse:true;wrapMode:TextEdit.Wrap
                color:Theme.text;selectionColor:Theme.selection;selectedTextColor:Theme.text
                font.family:block.modelData.kind==="code" ? Theme.mono : Theme.font
                font.pixelSize:block.modelData.kind==="code" ? root.textSize-1 : root.textSize
                HoverHandler {
                    cursorShape: !block.isCode && body.linkAt(point.position.x, point.position.y)
                                 ? Qt.PointingHandCursor : Qt.IBeamCursor
                }
                onLinkActivated:function(link){
                    backend.command("openLink",{url:link})
                    if (String(link).startsWith("tingmu://entry/")) root.sourceOpened()
                }
            }
        }
    }
}
