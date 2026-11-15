"""Readable preview typography without HTML interpolation or Markdown mutations."""
from PySide6.QtGui import QColor, QTextBlockFormat, QTextCursor, QTextLength, QTextTable


def format_document(document, accent="#8bbda1", border="#30393c"):
    """Style a parsed read-only document once per content/format revision."""
    doc = document.textDocument() if hasattr(document, "textDocument") else document
    if doc is None or doc.property("tingmu_format_busy"):
        return
    stamp = f"{doc.revision()}|{accent}|{border}"
    if doc.property("tingmu_format_stamp") == stamp:
        return
    doc.setProperty("tingmu_format_busy", True)
    try:
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        block = doc.begin()
        while block.isValid():
            cursor.setPosition(block.position())
            fmt = block.blockFormat()
            heading = fmt.headingLevel()
            fmt.setLineHeight(145 if not heading else 125, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            fmt.setTopMargin(14 if heading else 2)
            fmt.setBottomMargin(9 if heading else 8)
            cursor.setBlockFormat(fmt)
            fragment = block.begin()
            while not fragment.atEnd():
                item = fragment.fragment()
                if item.isValid() and item.charFormat().isAnchor():
                    link = QTextCursor(doc)
                    link.setPosition(item.position())
                    link.setPosition(item.position()+item.length(), QTextCursor.MoveMode.KeepAnchor)
                    char_format = item.charFormat()
                    char_format.setForeground(QColor(accent))
                    link.mergeCharFormat(char_format)
                fragment += 1
            block = block.next()
        def tables(frame):
            for child in frame.childFrames():
                if isinstance(child, QTextTable):
                    fmt = child.format()
                    fmt.setCellPadding(7)
                    fmt.setCellSpacing(0)
                    fmt.setBorder(1)
                    fmt.setBorderBrush(QColor(border))
                    fmt.setWidth(QTextLength(QTextLength.Type.PercentageLength, 100))
                    fmt.setColumnWidthConstraints([QTextLength(QTextLength.Type.PercentageLength, 100/child.columns())
                                                   for _ in range(child.columns())])
                    child.setFormat(fmt)
                tables(child)
        tables(doc.rootFrame())
        cursor.endEditBlock()
        doc.setProperty("tingmu_format_stamp", f"{doc.revision()}|{accent}|{border}")
    finally:
        doc.setProperty("tingmu_format_busy", False)
