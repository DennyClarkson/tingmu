import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
ColumnLayout {
    id:root
    property var blocks:[]
    property int textSize:14
    signal sourceOpened()
    spacing:12
    ListModel { id: blockRows }
    function syncBlocks() {
        const source = blocks || []
        if (blockRows.count > source.length)
            blockRows.remove(source.length, blockRows.count - source.length)
        for (let i = 0; i < source.length; ++i) {
            const b = source[i]
            const language = b.language || ""
            const content = b.text || ""
            if (i === blockRows.count) {
                blockRows.append({kind: b.kind, bodyText: content, languageLabel: language})
            } else {
                const row = blockRows.get(i)
                if (row.kind !== b.kind) blockRows.setProperty(i, "kind", b.kind)
                if (row.languageLabel !== language) blockRows.setProperty(i, "languageLabel", language)
                if (row.bodyText !== content) blockRows.setProperty(i, "bodyText", content)
            }
        }
    }
    onBlocksChanged: syncBlocks()
    Component.onCompleted: syncBlocks()
    Repeater {
        model:blockRows
        delegate:Rectangle {
            id:block
            required property string kind
            required property string bodyText
            required property string languageLabel
            Layout.fillWidth:true
            readonly property bool isCode: kind === "code"
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
                    Text{text:block.languageLabel || qsTranslate("Tingmu", "代码");textFormat:Text.PlainText;color:Theme.muted;font.family:Theme.latin;font.pixelSize:12;Layout.fillWidth:true}
                    TButton {
                        objectName: "copyCodeButton"
                        text: codeCopied.running ? qsTranslate("Tingmu", "已复制") : qsTranslate("Tingmu", "复制代码")
                        quiet: true
                        implicitHeight: 26
                        onClicked: {
                            backend.command("copyText", {text: block.bodyText})
                            codeCopied.restart()
                        }
                        Timer { id: codeCopied; interval: 1400 }
                    }
                }
            }
            TextEdit {
                id:body
                objectName:"markdownBody"
                function formatDocument(){if(!block.isCode)formatTimer.restart()}
                Timer{id:formatTimer;interval:0;onTriggered:backend.formatMarkdown(body.textDocument,String(Theme.accent),String(Theme.line))}
                Component.onCompleted:formatDocument()
                onTextChanged:formatDocument()
                onTextFormatChanged:formatDocument()
                Connections{target:Theme;function onNameChanged(){body.formatDocument()}}
                anchors.left:parent.left;anchors.right:parent.right;anchors.top:parent.top
                anchors.leftMargin: block.isCode ? 12 : 0
                anchors.rightMargin: block.isCode ? 12 : 0
                anchors.topMargin: block.isCode ? codeHeader.height + 12 : 0
                text:block.bodyText
                textFormat:block.isCode ? TextEdit.PlainText : TextEdit.MarkdownText
                readOnly:true;selectByMouse:true;wrapMode:TextEdit.Wrap
                color:Theme.text;selectionColor:Theme.selection;selectedTextColor:Theme.text
                font.family:block.isCode ? Theme.mono : Theme.font
                font.pixelSize:block.isCode ? root.textSize-1 : root.textSize
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
