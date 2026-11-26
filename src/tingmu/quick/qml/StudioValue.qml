import QtQuick

// Snap immediately if the item leaves the viewport during a transition.
NumberAnimation {
    id: motion
    required property real destination
    required property bool permitted
    property real value: 0
    property bool initialized: false
    target: motion
    property: "value"
    duration: 230
    easing.type: Easing.OutCubic
    function settle() {
        stop()
        value = destination
    }
    onDestinationChanged: {
        stop()
        if (initialized && permitted && value !== destination) {
            from = value
            to = destination
            start()
        } else {
            value = destination
        }
    }
    onPermittedChanged: if (!permitted) settle()
    Component.onCompleted: { settle(); initialized = true }
}
