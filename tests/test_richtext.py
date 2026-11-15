import pytest

from tingmu.richtext import markdown_blocks, safe_link


def test_markdown_structure_and_verbatim_code():
    raw = "# Title\n\n- list\n> quote\n\n| a | b |\n|---|---|\n| 1 | 2 |\n```python\n  <script>\r\nprint('a')\n```\n## End"
    blocks = markdown_blocks(raw)
    assert [b["kind"] for b in blocks] == ["markdown", "code", "markdown"]
    assert blocks[1] == {"kind": "code", "text": "  <script>\r\nprint('a')\n", "language": "python"}
    assert "# Title" in blocks[0]["text"] and "| 1 | 2 |" in blocks[0]["text"]
    assert "> quote" in blocks[0]["text"]


@pytest.mark.parametrize("link", ["file:///C:/private.txt", "javascript:alert(1)", "data:text/html,test",
                                 "qrc:/secret", "../secret", "//server/file", "https://x\\secret",
                                 "java%73cript:alert(1)", "http://user:pass@example.com", "tingmu://entry/0",
                                 "tingmu://entry/1?file=x"])
def test_unsafe_links_are_removed(link):
    assert not safe_link(link)
    assert "](" not in markdown_blocks(f"[click]({link})")[0]["text"]


def test_injection_and_images_disabled_but_user_links_preserved():
    text = '<img src="https://evil/pixel"><script>x()</script>\n![remote](https://evil/pixel)\n' \
           '[ok](https://example.com/a_(b)) [source](tingmu://entry/123)\n' \
           '[local][ref]\n[ref]: file:///C:/secret\n![refimage][pic]\n[pic]: https://evil/image'
    safe = markdown_blocks(text)[0]["text"]
    assert "<img" not in safe and "<script" not in safe
    assert "![" not in safe
    assert "file:///" not in safe
    assert "[ok](https://example.com/a_(b))" in safe
    assert "[source](tingmu://entry/123)" in safe


def test_fence_length_unclosed_and_tildes():
    assert markdown_blocks("~~~~js\nx\n```\ny\n~~~~\n")[0]["text"] == "x\n```\ny\n"
    assert markdown_blocks("```\na\n")[0]["text"] == "a\n"


def test_multiline_reference_cannot_link_to_local_file():
    safe = markdown_blocks("[secret][id]\n\n[id]:\n    file:///C:/secret.txt\n")[0]["text"]
    assert "file:///" not in safe


def test_qt_render_does_not_create_resource_images_or_unsafe_anchors(qapp):
    from PySide6.QtGui import QTextDocument, QTextFormat

    raw = ('![pixel](https://evil/image)\n![ref][pic]\n[pic]: https://evil/pixel\n'
           '<img src="file:///C:/secret">\n[x](javascript:alert(1))\n'
           '[file][id]\n[id]:\n  file:///C:/secret\n\n'
           '[safe](https://example.com) [source](tingmu://entry/2)\n')
    document = QTextDocument()
    document.setMarkdown(markdown_blocks(raw)[0]["text"])
    anchors = []
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            fmt = fragment.charFormat()
            assert fmt.objectType() != QTextFormat.ObjectTypes.ImageObject
            if fmt.isAnchor():
                anchors.append(fmt.anchorHref())
            iterator += 1
        block = block.next()
    assert anchors and all(safe_link(url) for url in anchors)
