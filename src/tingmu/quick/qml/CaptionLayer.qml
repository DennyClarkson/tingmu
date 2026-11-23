import QtQuick
import "CaptionStyles.js" as Styles

Item {
    id: captionRoot
    property var appearance: Styles.base
    property var lines: []
    property real clearance: 0
    property real topClearance: 0
    property real fontScale: 1
    property bool movable: false
    property var placement: null
    signal reposition(real centerX, real centerY)
    signal committed()
    readonly property int columnIndex: (appearance.align - 1) % 3
    readonly property int rowIndex: Math.floor((appearance.align - 1) / 3)
    readonly property real insetX: width * appearance.horizontalMargin / 100
    readonly property real insetY: height * appearance.verticalMargin / 100
    clip: true

    Column {
        id: block
        width: Math.max(1, captionRoot.width - 2 * captionRoot.insetX)
        x: captionRoot.insetX
        y: {
            const top = Math.max(captionRoot.insetY, captionRoot.topClearance)
            const bottom = Math.max(top, captionRoot.height - Math.max(captionRoot.insetY, captionRoot.clearance) - height)
            if (captionRoot.placement)
                return Math.max(0, Math.min(captionRoot.height - captionRoot.clearance - height,
                                            captionRoot.placement.y * captionRoot.height - height / 2))
            return captionRoot.rowIndex === 2 ? top : captionRoot.rowIndex === 1 ? (top + bottom) / 2 : bottom
        }
        spacing: 3
        Repeater {
            model: captionRoot.lines
            delegate: Item {
                id: line
                required property string modelData
                width: Math.min(block.width, Math.ceil(mainText.implicitWidth) + 16)
                x: captionRoot.placement
                   ? Math.max(0, Math.min(captionRoot.width - width, captionRoot.placement.x * captionRoot.width - width / 2)) - block.x
                   : (block.width - width) * captionRoot.columnIndex / 2
                height: mainText.contentHeight + (captionRoot.appearance.box ? 7 : 4)
                readonly property int hAlign: [Text.AlignLeft, Text.AlignHCenter, Text.AlignRight][captionRoot.columnIndex]
                component Glyph: Text {
                    width: Math.max(1, line.width - 16)
                    text: line.modelData
                    textFormat: Text.PlainText
                    font.family: captionRoot.appearance.family
                    font.pixelSize: Math.max(8, Math.round(captionRoot.appearance.size * captionRoot.fontScale))
                    font.bold: captionRoot.appearance.bold
                    font.italic: captionRoot.appearance.italic
                    horizontalAlignment: line.hAlign
                    wrapMode: Text.Wrap
                }
                Rectangle {
                    visible: captionRoot.appearance.box
                    width: Math.min(line.width, mainText.contentWidth + 16)
                    height: line.height
                    x: (line.width - width) * captionRoot.columnIndex / 2
                    color: captionRoot.appearance.boxColor
                    opacity: captionRoot.appearance.boxOpacity / 100
                    radius: 3
                }
                Glyph {
                    visible: captionRoot.appearance.shadow > 0
                    x: mainText.x + captionRoot.appearance.shadow
                    y: mainText.y + captionRoot.appearance.shadow
                    color: "#a0000000"
                }
                Repeater {
                    model: captionRoot.appearance.outline > 0 ? 8 : 0
                    delegate: Glyph {
                        required property int index
                        x: mainText.x + Math.cos(index * Math.PI / 4) * captionRoot.appearance.outline
                        y: mainText.y + Math.sin(index * Math.PI / 4) * captionRoot.appearance.outline
                        color: captionRoot.appearance.edge
                    }
                }
                Glyph {
                    id: mainText
                    x: 8
                    y: (line.height - contentHeight) / 2
                    color: captionRoot.appearance.fill
                }
                CaptionDrag {
                    objectName: "textCaptionDrag"
                    anchors.fill: parent
                    enabled: captionRoot.movable
                    viewport: captionRoot
                    center: Qt.point(block.x + line.x + line.width / 2, block.y + block.height / 2)
                    onReposition: (cx, cy) => captionRoot.reposition(cx, cy)
                    onCommitted: captionRoot.committed()
                }
            }
        }
    }
}
