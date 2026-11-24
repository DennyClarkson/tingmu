"""Local Unicode token boundaries without a graphical toolkit dependency."""

import unicodedata


def words(text):
    result = []
    cursor = 0
    while cursor < len(text):
        start = cursor
        char = text[cursor]
        cursor += 1
        if char.isspace():
            continue
        # CJK ideographs remain individual lookup units; alphabetic scripts keep
        # their combining marks and internal apostrophes without normalization.
        ideograph = "CJK" in unicodedata.name(char, "") and "IDEOGRAPH" in unicodedata.name(char, "")
        if char.isalnum() and not ideograph:
            while cursor < len(text):
                next_char = text[cursor]
                category = unicodedata.category(next_char)
                next_ideograph = "IDEOGRAPH" in unicodedata.name(next_char, "")
                if (next_char.isalnum() and not next_ideograph) or category.startswith("M"):
                    cursor += 1
                elif next_char in "'’" and cursor + 1 < len(text) and text[cursor + 1].isalpha():
                    cursor += 1
                elif next_char in ".," and text[cursor - 1].isdigit() and cursor + 1 < len(text) and text[cursor + 1].isdigit():
                    cursor += 1
                else:
                    break
        else:
            while cursor < len(text):
                next_char = text[cursor]
                if unicodedata.category(next_char).startswith("M") or 0x1F3FB <= ord(next_char) <= 0x1F3FF:
                    cursor += 1
                elif next_char == "\u200d" and cursor + 1 < len(text):
                    cursor += 2
                else:
                    break
        result.append(text[start:cursor])
    return result
