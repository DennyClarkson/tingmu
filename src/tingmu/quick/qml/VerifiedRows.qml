import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: rows
    required property var studio
    readonly property var samples: studio.samples
    readonly property string jobId: studio.job.id || ""
    property string owner: ""
    Layout.fillWidth: true
    spacing: 8
    onSamplesChanged: Qt.callLater(reconcile)
    onJobIdChanged: Qt.callLater(reconcile)
    Component.onCompleted: reconcile()
    ListModel { id: entries }
    function reconcile() {
        const switched = owner !== jobId
        if (switched) {
            entries.clear()
            owner = jobId
        }
        const ids = samples.map(s => s.id)
        for (let i = entries.count - 1; i >= 0; --i) {
            if (!ids.includes(entries.get(i).cueId)) entries.remove(i)
        }
        for (let i = 0; i < samples.length; ++i) {
            const s = samples[i]
            let old = i
            while (old < entries.count && entries.get(old).cueId !== s.id) ++old
            if (old === entries.count) {
                entries.insert(i, {cueId: s.id, sourceText: s.source, translation: s.translation, fresh: !switched})
            } else {
                if (old !== i) entries.move(old, i, 1)
                entries.set(i, {sourceText: s.source, translation: s.translation})
            }
        }
    }
    Repeater {
        model: entries
        delegate: RowLayout {
            id: row
            required property string cueId
            required property string sourceText
            required property string translation
            required property bool fresh
            objectName: "studioVerifiedRow:" + cueId
            readonly property bool permitted: rows.studio.onScreen(row)
            property real arrival: 1
            Layout.fillWidth: true; spacing: 9
            opacity: 0.4 + 0.6 * arrival
            transform: Translate { y: (1 - row.arrival) * 8 }
            function enter() {
                if (fresh && permitted) rise.start()
            }
            Component.onCompleted: Qt.callLater(enter)
            onPermittedChanged: if (!permitted) { rise.stop(); arrival = 1 }
            NumberAnimation {
                id: rise; target: row; property: "arrival"
                from: 0; to: 1; duration: 320; easing.type: Easing.OutCubic
            }
            Rectangle {
                Layout.fillHeight: true
                implicitWidth: 2 + (1 - row.arrival) * 3
                color: Theme.tint(Theme.accent, Theme.text, (1 - row.arrival) * 0.55)
            }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 2
                Text {
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    textFormat: Text.PlainText; font.family: Theme.font; font.pixelSize: 12
                    text: row.sourceText; font.weight: Font.DemiBold; wrapMode: Text.WrapAnywhere; color: Theme.text
                }
                Text {
                    Layout.fillWidth: true; Layout.minimumWidth: 0
                    textFormat: Text.PlainText; font.family: Theme.font; font.pixelSize: 12
                    text: row.translation; color: Theme.muted; wrapMode: Text.WrapAnywhere
                }
            }
        }
    }
}
