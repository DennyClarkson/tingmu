import QtQuick
import QtQuick.Layouts

Rectangle {
    id: slot
    required property var studio
    required property int index
    objectName: "studioWorkerSlot:" + index
    readonly property var batch: studio.slots[index] || ({})
    readonly property bool processing: studio.running && batch.status === "running"
    readonly property bool motionAllowed: studio.onScreen(slot)
    readonly property bool streamVisible: studio.onScreen(stream)
    readonly property bool repair: !!batch.repair || ["partial", "split"].includes(batch.status)
    readonly property string stateKey: JSON.stringify([studio.job.id, studio.job.matrix_stage,
                                                      batch.id, batch.status, repair])
    readonly property color ink: batch.status === "failed" ? Theme.danger
                               : repair ? Theme.warning : batch.status ? Theme.accent : Theme.muted
    property color displayedInk
    property real flash: 0
    property bool initialized: false
    property alias stream: stream
    Layout.fillWidth: true; Layout.preferredWidth: 1
    implicitHeight: slotHeight.value
    scale: 1 + 0.018 * flash
    radius: Theme.radiusSm
    clip: true
    color: Theme.tint(Theme.panel, displayedInk, 0.025 + activity.value * 0.09 + flash * 0.20)
    border.color: Theme.tint(Theme.line, displayedInk, activity.value * 0.49 + flash * 0.42)
    StudioValue {
        id: activity; destination: slot.processing ? 1 : slot.batch.status ? 0.48 : 0
        permitted: slot.motionAllowed
    }
    StudioValue {
        id: slotHeight; destination: contents.implicitHeight + 12
        permitted: slot.motionAllowed
    }
    NumberAnimation {
        id: wake; target: slot; property: "flash"; from: 1; to: 0
        duration: slot.repair ? 510 : 380; easing.type: Easing.OutCubic
    }
    ColorAnimation { id: inkChange; target: slot; property: "displayedInk"; duration: 200 }
    onInkChanged: {
        inkChange.stop()
        if (initialized && motionAllowed) {
            inkChange.from = displayedInk
            inkChange.to = ink
            inkChange.start()
        } else displayedInk = ink
    }
    onStateKeyChanged: if (initialized && motionAllowed && batch.status) wake.restart()
    onMotionAllowedChanged: {
        if (!motionAllowed) {
            wake.stop()
            inkChange.stop()
            displayedInk = ink
            flash = 0
        }
    }
    onStreamVisibleChanged: if (streamVisible && initialized) Qt.callLater(studio.refreshStreams)
    Component.onCompleted: { displayedInk = ink; initialized = true }
    component Label: Text {
        Layout.fillWidth: true; Layout.minimumWidth: 0
        textFormat: Text.PlainText
        font.family: Theme.font; font.pixelSize: 12
        color: Theme.muted; wrapMode: Text.Wrap
    }
    ColumnLayout {
        id: contents
        anchors { top: parent.top; left: parent.left; right: parent.right; margins: 6 }
        spacing: 3
        Label {
            text: (slot.batch.id !== undefined ? "#" + String(slot.batch.id).padStart(2, "0") : "·")
                  + "  " + slot.studio.outcome(slot.batch.status)
            font.weight: Font.DemiBold; color: slot.displayedInk
        }
        Label {
            visible: !!slot.batch.label
            text: (slot.batch.repair ? qsTranslate("Tingmu", "重试 · ") : "")
                + (/^分块 /.test(slot.batch.label || "") ? qsTranslate("Tingmu", "分块 %1").arg(slot.batch.label.slice(3))
                : qsTranslate("TingmuStatus", slot.batch.label || ""))
        }
        Label {
            visible: slot.batch.total !== undefined
            text: (slot.batch.done || 0) + " / " + (slot.batch.total || 0)
            color: Theme.text; font.family: Theme.mono
        }
        WorkerStream {
            id: stream
            objectName: "studioWorkerStream:" + slot.index
            Layout.fillWidth: true
            requestKey: JSON.stringify([slot.studio.job.id, slot.studio.job.matrix_stage || slot.studio.job.stage_name,
                                        slot.batch.id])
            compact: slot.studio.compact
            processing: slot.processing
            motionAllowed: slot.streamVisible
        }
        AppProgressBar {
            id: batchBar
            objectName: "queueBatchProgressBar"
            readonly property var batch: slot.batch
            Layout.fillWidth: true; implicitHeight: 3
            indeterminate: slot.processing
            animationEnabled: slot.studio.onScreen(batchBar)
            value: batch.total ? batch.done / batch.total : 0
        }
    }
}
