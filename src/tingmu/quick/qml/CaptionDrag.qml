import QtQuick

MouseArea {
    id: drag
    required property Item viewport
    required property point center
    property point pressPosition
    property point originalCenter
    property bool moved: false
    signal reposition(real centerX, real centerY)
    signal committed()
    cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
    preventStealing: true
    propagateComposedEvents: true
    onClicked: function(mouse) { mouse.accepted = moved }
    onDoubleClicked: function(mouse) { mouse.accepted = false }
    onPressed: function(mouse) {
        pressPosition = mapToItem(viewport, mouse.x, mouse.y)
        originalCenter = center
        moved = false
    }
    onPositionChanged: function(mouse) {
        if (!pressed || viewport.width <= 0 || viewport.height <= 0) return
        const point = mapToItem(viewport, mouse.x, mouse.y)
        const dx = point.x - pressPosition.x
        const dy = point.y - pressPosition.y
        if (!moved && Math.abs(dx) + Math.abs(dy) < Qt.styleHints.startDragDistance) return
        moved = true
        reposition(Math.max(0, Math.min(1, (originalCenter.x + dx) / viewport.width)),
                   Math.max(0, Math.min(1, (originalCenter.y + dy) / viewport.height)))
    }
    onReleased: if (moved) committed()
    onCanceled: if (moved) committed()
}
