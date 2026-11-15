"""Render a real prepared lesson using Qt offscreen; no desktop interaction."""

import argparse
from dataclasses import replace
import os
from pathlib import Path
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

from tingmu.models import LanguagePair
from tingmu.storage import Store
from tingmu.ui import MainWindow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--time", type=float, help="Preview the real cue at this media time")
    parser.add_argument("--output", type=Path, default=Path("reports/screenshots/companion-v2.png"))
    args = parser.parse_args()
    source = Store(args.data_dir)
    document = source.load_document(str(args.media.resolve()), LanguagePair())
    if document is None:
        raise ValueError("No prepared lesson exists")
    app = QApplication([])
    # The Windows offscreen platform does not discover native fonts itself.
    font_file = Path("C:/Windows/Fonts/msyh.ttc")
    if font_file.is_file():
        QFontDatabase.addApplicationFont(str(font_file))
    app.setFont(QFont("Microsoft YaHei UI", 10))
    with tempfile.TemporaryDirectory(prefix="tingmu-visual-") as folder:
        store = Store(Path(folder))
        store.save_settings(replace(source.load_settings(), library_path=""))
        window = MainWindow(store)
        window.media_path = args.media.resolve()
        window.set_document(document)
        cue = next((c for c in document.cues if "嘅" in c.text and c.explanation and len(c.text) < 30), document.cues[0])
        if args.time is not None:
            cue = next((c for c in document.cues if c.start <= args.time < c.end), cue)
        window.companion.set_cue(cue, force=True)
        window.companion.resize(640, 820)
        window.companion.show()
        app.processEvents()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        window.companion.grab().save(str(args.output.resolve()))
        window.close()
        app.processEvents()
    print(args.output.resolve())


if __name__ == "__main__":
    main()
