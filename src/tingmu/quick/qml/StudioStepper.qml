import QtQuick
import QtQuick.Layouts
import "Icons.js" as Icons

GridLayout {
    id: stepper
    objectName: "studioStepper"
    required property var studio
    Layout.fillWidth: true
    columns: Math.min(Math.max(1, studio.plan.length), Math.max(2, Math.floor(width / 80)))
    columnSpacing: 0; rowSpacing: 7
    Repeater {
        model: stepper.studio.plan.length
        delegate: Item {
            id: step
            required property int index
            readonly property string modelData: stepper.studio.plan[index] || ""
            readonly property var studio: stepper.studio
            readonly property bool current: studio.stageIndex === index && !studio.complete
            readonly property bool done: !current && (studio.job.stage_history ? studio.job.stage_history.includes(modelData)
                        : studio.complete || index < studio.stageIndex)
            Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.alignment: Qt.AlignTop
            implicitHeight: caption.implicitHeight + 26
            objectName: "studioStep:" + index
            property alias leadingFraction: leading.value
            property alias trailingFraction: trailing.value
            Rectangle {
                visible: step.index % stepper.columns !== 0
                x: 0; y: 10; width: parent.width / 2; height: 2
                color: Theme.line
                Rectangle {
                    height: parent.height; width: parent.width * leading.value; color: Theme.accent
                    StudioValue {
                        id: leading; destination: step.done || step.current ? 1 : 0
                        permitted: step.studio.onScreen(step)
                    }
                }
            }
            Rectangle {
                visible: (step.index + 1) % stepper.columns !== 0 && step.index < step.studio.plan.length - 1
                x: parent.width / 2; y: 10; width: parent.width / 2; height: 2
                color: Theme.line
                Rectangle {
                    height: parent.height; width: parent.width * trailing.value; color: Theme.accent
                    StudioValue {
                        id: trailing; destination: step.done ? 1 : 0
                        permitted: step.studio.onScreen(step)
                    }
                }
            }
            Rectangle {
                id: halo
                anchors.horizontalCenter: parent.horizontalCenter
                width: 23; height: 23; radius: 12; color: Theme.accent
                visible: step.current && step.studio.running; opacity: 0.24
                SequentialAnimation on opacity {
                    objectName: "studioStagePulse"
                    running: step.studio.running && step.current && step.studio.onScreen(halo)
                    loops: Animation.Infinite
                    NumberAnimation { from: 0.14; to: 0.46; duration: 800; easing.type: Easing.InOutSine }
                    NumberAnimation { from: 0.46; to: 0.14; duration: 800; easing.type: Easing.InOutSine }
                }
            }
            Rectangle {
                id: dot
                objectName: "studioStepDot:" + step.index
                anchors.horizontalCenter: parent.horizontalCenter; y: 11.5 - height / 2
                width: 13 + 5 * activeSize.value; height: width; radius: width / 2
                color: Theme.tint(Theme.surfaceElevated, Theme.accent, filled.value)
                border.color: Theme.tint(Theme.line, Theme.accent, filled.value)
                StudioValue {
                    id: activeSize; destination: step.current ? 1 : 0
                    permitted: step.studio.onScreen(step)
                }
                StudioValue {
                    id: filled; destination: step.done || step.current ? 1 : 0
                    permitted: step.studio.onScreen(step)
                }
                Image {
                    visible: step.done; anchors.centerIn: parent; width: 11; height: 11
                    source: Icons.source("check", Theme.onAccent); sourceSize.width: 16; sourceSize.height: 16
                }
            }
            Text {
                id: caption; y: 26; width: parent.width - 4; anchors.horizontalCenter: parent.horizontalCenter
                textFormat: Text.PlainText; font.family: Theme.font; font.pixelSize: 12
                horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap
                font.weight: step.current ? Font.DemiBold : Font.Normal
                color: step.current ? Theme.accent : step.done ? Theme.text : Theme.muted
                text: qsTranslate("TingmuStatus", step.modelData)
            }
        }
    }
}
