import QtQuick
import QtQuick.Window
Item {
    id:root
    property var player
    property bool active:true
    readonly property bool ownsKeyboard:backend.focusWindow===root.Window.window
    readonly property bool acceptsKeys:active && ownsKeyboard && !!player && !player.settingsOpen && !player.typing(root.Window.window)
    Shortcut{sequence:"Space";context:Qt.WindowShortcut;enabled:root.acceptsKeys;onActivated:backend.command("playPause",{})}
    Shortcut{sequence:"Left";context:Qt.WindowShortcut;enabled:root.acceptsKeys;onActivated:backend.command("seekRelative",{delta:-5000})}
    Shortcut{sequence:"Right";context:Qt.WindowShortcut;enabled:root.acceptsKeys;onActivated:backend.command("seekRelative",{delta:5000})}
    Shortcut{sequence:"Up";context:Qt.WindowShortcut;enabled:root.acceptsKeys;onActivated:backend.command("previousCue",{})}
    Shortcut{sequence:"Down";context:Qt.WindowShortcut;enabled:root.acceptsKeys;onActivated:backend.command("nextCue",{})}
    Shortcut{sequence:"F";context:Qt.WindowShortcut;enabled:root.acceptsKeys;onActivated:root.player.toggleFullscreen()}
    Shortcut{sequence:"M";context:Qt.WindowShortcut;enabled:root.acceptsKeys;onActivated:backend.command("setMuted",{value:!backend.playbackDetails.muted})}
    Shortcut{sequence:"Escape";context:Qt.WindowShortcut;enabled:root.active && root.ownsKeyboard && !!root.player && root.player.fullscreen && !root.player.settingsOpen;onActivated:root.player.toggleFullscreen()}
}
