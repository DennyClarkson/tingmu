from pathlib import Path

from PySide6.QtCore import QSize
from PySide6.QtGui import QImageReader, QPixmap

from tingmu.quick import system_tray
from test_qml_v4 import quick as quick


def test_window_and_tray_render_the_packaged_icon_frames(quick):
    app, _, _, window = quick
    icons = (app.windowIcon(), window.icon(), app._tingmu_tray._tray.icon())
    reader = QImageReader(str(Path(system_tray.__file__).parent / "brand" / "app-icon.ico"))
    sizes = set()
    for index in range(reader.imageCount()):
        assert reader.jumpToImage(index)
        frame = reader.read()
        assert not frame.isNull()
        size = frame.size()
        sizes.add(size.width())
        assert size == QSize(size.width(), size.width())
        alphas = {frame.pixelColor(x, y).alpha() for y in range(size.height()) for x in range(size.width())}
        assert 0 in alphas and 255 in alphas
        expected = QPixmap.fromImage(frame).toImage()
        for icon in icons:
            assert size in icon.availableSizes()
            assert icon.pixmap(size, 1.0).toImage() == expected
    assert {16, 24, 32, 48, 64, 256} <= sizes
