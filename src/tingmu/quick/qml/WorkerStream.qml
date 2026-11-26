import QtQuick

Item {
    id: stream
    required property bool motionAllowed
    required property bool processing
    required property bool compact
    property string requestKey
    property string displayed: ""
    property var pending: []
    property int receivedSeq: 0
    property int epoch: -1
    property bool snapOnNext: false
    property real highlight: 0
    readonly property bool revealing: reveal.running
    readonly property bool cursorBlinking: blink.running
    readonly property int lines: compact ? 1 : 2
    implicitHeight: Math.ceil(fontMetrics.height) * lines + 2
    clip: true

    function characters(text) {
        const result = []
        for (let i = 0; i < text.length;) {
            const length = text.codePointAt(i) > 0xffff ? 2 : 1
            result.push(text.slice(i, i + length))
            i += length
        }
        return result
    }
    function clearStream() {
        fade.stop()
        pending = []
        epoch = -1
        receivedSeq = 0
        displayed = ""
        highlight = 0
    }
    onRequestKeyChanged: clearStream()
    function accept(sample) {
        if (!sample) return
        if (epoch !== sample.stream_epoch) {
            clearStream()
            epoch = sample.stream_epoch
        }
        const seq = sample.stream_seq
        if (seq <= receivedSeq) {
            if (motionAllowed) snapOnNext = false
            return
        }
        const tail = characters(sample.stream_tail)
        const delta = seq - receivedSeq
        receivedSeq = seq
        if (!motionAllowed || snapOnNext) {
            displayed = tail.join("")
            pending = []
            snapOnNext = false
            return
        }
        // A suspended UI can miss an entire tail. Resume at known text.
        if (delta > tail.length) {
            displayed = ""
            pending = tail
        } else {
            pending = pending.concat(tail.slice(-delta)).slice(-tail.length)
        }
    }
    onMotionAllowedChanged: {
        if (!motionAllowed) {
            displayed = characters(displayed).concat(pending).slice(-160).join("")
            pending = []
            fade.stop()
            highlight = 0
            snapOnNext = true
        }
    }
    function advance() {
        // A single very large burst may outrun the Qt display clock. Catch up
        // the old prefix once, then animate its recent tail one character at a time.
        if (pending.length > (processing ? 112 : 64)) {
            const keep = processing ? 36 : 16
            displayed = characters(displayed).concat(pending.slice(0, -keep)).slice(-160).join("")
            pending = pending.slice(-keep)
        }
        const glyph = pending[0]
        pending = pending.slice(1)
        displayed = characters(displayed).concat(glyph).slice(-160).join("")
        // Qt cursor offsets are UTF-16; the queue itself contains code points.
        const end = displayed.length
        output.select(end - glyph.length, end)
        fade.restart()
    }
    Timer {
        id: reveal
        interval: !stream.processing || stream.pending.length > 48 ? 10 : stream.pending.length > 20 ? 24 : 32
        repeat: true
        running: stream.motionAllowed && stream.pending.length > 0
        onTriggered: stream.advance()
    }
    NumberAnimation {
        id: fade
        target: stream; property: "highlight"
        from: 1; to: 0; duration: 200; easing.type: Easing.OutQuad
    }
    FontMetrics { id: fontMetrics; font: output.font }
    Text {
        anchors.verticalCenter: parent.verticalCenter
        visible: !stream.processing && !stream.displayed
        text: "—"; font: output.font; color: Theme.scrollThumb
    }
    TextEdit {
        id: output
        objectName: "studioStreamOutput"
        width: Math.max(1, stream.width - 3)
        readonly property rect endRect: {
            // Query again after rewrap, even when the text is unchanged.
            const geometry = contentHeight + width
            return positionToRectangle(text.length)
        }
        y: Math.min(0, stream.height - endRect.y - endRect.height)
        text: stream.displayed
        textFormat: TextEdit.PlainText
        wrapMode: TextEdit.WrapAnywhere
        color: Theme.muted
        selectedTextColor: Theme.tint(Theme.muted, Theme.text, stream.highlight)
        selectionColor: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.48 * stream.highlight)
        font.family: Theme.font; font.pixelSize: 12
        readOnly: true; persistentSelection: true
        selectByMouse: false; activeFocusOnPress: false
        cursorVisible: false
    }
    Rectangle {
        id: caret
        visible: stream.processing
        color: Theme.accent
        x: Math.min(stream.width - width, Math.max(0, output.endRect.x))
        y: output.y + output.endRect.y + 2
        width: 2; height: Math.max(2, output.endRect.height - 4)
        opacity: 0.75
        SequentialAnimation on opacity {
            id: blink
            running: stream.motionAllowed && stream.processing
            loops: Animation.Infinite
            NumberAnimation { from: 0.9; to: 0.25; duration: 470; easing.type: Easing.InOutSine }
            NumberAnimation { from: 0.25; to: 0.9; duration: 470; easing.type: Easing.InOutSine }
        }
    }
}
