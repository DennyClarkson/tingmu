import QtQuick

// Keep drafts, selection and scroll position after the first visit.
Loader {
    property bool requested: false
    active: false
    onRequestedChanged: if (requested) active = true
    Component.onCompleted: if (requested) active = true
}
