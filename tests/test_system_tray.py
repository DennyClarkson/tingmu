import json

from tingmu.quick.system_tray import SystemTray
from test_qml_v4 import find, quick as quick


def test_close_to_tray_keeps_session_and_restores_its_windows(quick, monkeypatch):
    app, bridge, _, window = quick
    tray = app._tingmu_tray
    # CI runs offscreen. Exercise the same close/restore path as a desktop with
    # a notification area without registering an icon with the host shell.
    monkeypatch.setattr(SystemTray, "available", True)
    models = find(window, "modelManagerWindow")
    models.show()
    assert models.isVisible()
    assert not tray.shouldHideOnClose(window)
    tray._close_to_tray.trigger()
    assert tray.shouldHideOnClose(window)
    assert json.loads((bridge.store.data_dir / "tray-settings.json").read_text("utf-8"))["close_to_tray"]
    window.close()
    assert not window.isVisible() and not models.isVisible()
    assert not bridge._closed
    tray.restore()
    assert window.isVisible() and models.isVisible()
    assert not tray.shouldHideOnClose(models)
    tray._close_to_tray.trigger()
    assert not tray.shouldHideOnClose(window)
