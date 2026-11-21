"""Offline language labels from Unicode CLDR. Custom language tags remain valid independently."""

from functools import lru_cache
import json
from pathlib import Path
import re


@lru_cache(maxsize=1)
def _labels():
    return json.loads(Path(__file__).with_name("locale_names.json").read_text(encoding="utf-8"))


# Friendly names already used by the app; CLDR supplies the general catalog.
_COMMON = {"zh-CN": "普通话", "yue": "粤语", "en": "英语", "ja": "日语", "ko": "韩语",
           "fr": "法语", "es": "西班牙语", "de": "德语", "zh-TW": "繁体中文"}


@lru_cache(maxsize=1)
def _names():
    lookup = {}
    for code, labels in _labels().items():
        for label in labels:
            lookup.setdefault(label.casefold(), code)
    lookup.update({label: code for code, label in _COMMON.items()})
    lookup.update({"英文": "en", "中文": "zh-CN", "简体中文": "zh-CN", "日文": "ja", "韩文": "ko",
                   "mandarin": "zh-CN", "cantonese": "yue"})
    return lookup


def resolve_language(value):
    raw = str(value).strip()
    match = _names().get(raw.casefold())
    if match:
        return match
    # Codes need not appear in CLDR or any installed transcription engine.
    if re.fullmatch(r"(?:[A-Za-z]{2,3}|[A-Za-z]{1,8}(?:[-_][A-Za-z0-9]{1,8})+)", raw):
        return raw
    return ""


def display_language(code):
    if code in _COMMON:
        return _COMMON[code]
    labels = _labels().get(code)
    return labels[0] if labels else code


@lru_cache(maxsize=1)
def language_choices():
    codes = dict.fromkeys([*_COMMON, *sorted(_labels())])
    return [{"code": code, "label": f"{display_language(code)} · {code}",
             "english": _labels().get(code, ["", ""])[1]} for code in codes]
