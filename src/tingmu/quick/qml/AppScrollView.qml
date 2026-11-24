import QtQuick
import QtQuick.Controls.Basic

ScrollView {
    id: view
    rightPadding: 10
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ScrollBar.vertical: AppScrollBar {
        objectName: "viewportScrollBar"
        parent: view
        x: view.width - width
        y: view.topPadding
        height: Math.max(0, view.availableHeight)
    }
}
