from PySide6.QtGui import QTextDocument, QTextTable

from tingmu.quick.typography import format_document


def test_preview_typography_is_readable_idempotent_and_keeps_text(qapp):
    doc = QTextDocument()
    doc.setMarkdown("## 词语\n\n原句与讲解。\n\n[来源](tingmu://entry/1)\n\n| 词语 | 用法 |\n|---|---|\n| 唔該 | 请求帮助 |")
    original = doc.toPlainText()
    format_document(doc, "#39694c", "#d9d2c5")
    assert doc.toPlainText() == original
    assert doc.begin().blockFormat().lineHeight() == 125
    table = next(f for f in doc.rootFrame().childFrames() if isinstance(f, QTextTable))
    assert table.format().cellPadding() == 7
    revision = doc.revision()
    format_document(doc, "#39694c", "#d9d2c5")
    assert doc.revision() == revision
    format_document(doc, "#8bbda1", "#30393c")
    assert doc.toPlainText() == original
