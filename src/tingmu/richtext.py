"""Conservative Markdown presentation: no HTML, images, local or active resources."""

import html
import re
from urllib.parse import unquote, urlsplit


def safe_link(url):
    """The same allowlist can be used again when the UI handles an explicit click."""
    if not isinstance(url, str) or not url or len(url) > 8192:
        return False
    decoded = unquote(html.unescape(url))
    if any(c.isspace() or ord(c) < 32 or c in "<>\\\"'" for c in decoded):
        return False
    try:
        parsed = urlsplit(decoded)
        if parsed.scheme.lower() in ("http", "https"):
            return bool(parsed.hostname) and not parsed.username and not parsed.password
        return bool(re.fullmatch(r"tingmu://entry/[1-9][0-9]*", decoded))
    except ValueError:
        return False


def _destination(text):
    # A Markdown destination may be <url> or url followed by an optional quoted title.
    match = re.fullmatch(r'\s*(?:<([^<>]*)>|([^\s]+?))(?:\s+[\"\x27](.*)[\"\x27])?\s*', text, re.S)
    if not match:
        return ""
    return match.group(1) or match.group(2) or ""


def _sanitize(text):
    # Disable image syntax, including reference and nested images. The visible label remains.
    text = re.sub(r"!\[", r"\\!\\[", text)
    # Reference definitions need validation separately from inline destinations.
    def reference(match):
        label, destination = match.group(1), match.group(2)
        url = _destination(destination)
        return f"[{label}]: {url}" if safe_link(url) else ""
    text = re.sub(r"(?m)^ {0,3}\[([^\]\n]+)\]:[ \t]*(?:\r?\n[ \t]*)?([^\r\n]+)$", reference, text)

    # Scan balanced inline destinations. Regex alone mishandles nested parentheses.
    out, index = [], 0
    while index < len(text):
        if text.startswith("](", index):
            cursor, depth = index + 2, 1
            while cursor < len(text) and depth:
                char = text[cursor]
                if char == "\\" and cursor + 1 < len(text):
                    cursor += 2
                    continue
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                cursor += 1
            if depth == 0:
                destination = _destination(text[index + 2:cursor - 1])
                out.append("](" + destination + ")" if safe_link(destination) else "]")
                index = cursor
                continue
            # Incomplete destination should not become a Qt link.
            out.append("]\\(")
            index += 2
            continue
        out.append(text[index])
        index += 1
    # Escape arbitrary inline/block HTML. Existing entities are safe Markdown text,
    # but escaping & too prevents encoded tags/URLs acquiring a second interpretation.
    # Keep blockquote markers: a raw '>' cannot open an HTML tag on its own.
    return html.escape("".join(out), quote=False).replace("&gt;", ">")


def markdown_blocks(text):
    """Split fenced code verbatim and sanitize only prose; no resource fetching occurs."""
    if not isinstance(text, str):
        raise TypeError("Markdown 必须是文本。")
    blocks, prose, code = [], [], []
    fence, language = None, ""

    def flush_prose():
        if prose:
            blocks.append({"kind": "markdown", "text": _sanitize("".join(prose))})
            prose.clear()

    for line in text.splitlines(keepends=True):
        if fence is None:
            match = re.match(r"^ {0,3}(`{3,}|~{3,})([^\r\n]*)[\r\n]*$", line)
            if match and not (match[1][0] == "`" and "`" in match[2]):
                flush_prose()
                fence = match[1]
                language = re.sub(r"[^A-Za-z0-9_+.#-]", "", match[2].strip().split(" ")[0])[:40]
            else:
                prose.append(line)
        elif re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*[\r\n]*", line):
            blocks.append({"kind": "code", "text": "".join(code), "language": language})
            code, fence = [], None
        else:
            code.append(line)
    if fence is not None:
        blocks.append({"kind": "code", "text": "".join(code), "language": language})
    flush_prose()
    return blocks
