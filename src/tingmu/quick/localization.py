"""Installation-wide interface language. Study languages and stored content are independent."""

from functools import lru_cache
import json
from pathlib import Path
import re

from PySide6.QtCore import QCoreApplication, Property, QTranslator, Signal, Slot

from tingmu.locale_catalog import display_language, language_choices
from tingmu.runtime import confined_path
from tingmu.storage import atomic_json


LOCALES = ("zh-CN", "zh-TW", "en")
_ARGUMENT = re.compile(r"%([1-9][0-9]*)")
_STANDARD = {
    "OK": "确定", "Cancel": "取消", "Close": "关闭", "Discard": "放弃", "Yes": "是",
    "No": "否", "Open": "打开", "Save": "保存", "Apply": "应用", "Reset": "重置",
}
# These arguments are themselves application messages. All other arguments, including
# filenames, model identifiers and words quoted from a subtitle, stay verbatim.
_MESSAGE_ARGS = {
    "运行环境验证失败：%1": ("1",),
    "%1 已完成步骤已保存，重试会继续处理。": ("1",),
    "第 %1 句（%2） · %3": ("3",),
    "%1 服务建议等待 %2 秒。": ("1",),
    "%1未完成，可重试": ("1",),
    "逐句译文核验 · %1": ("1",),
    "字幕已保存。请检查工作区文本模型设置并重试。%1": ("1",),
}


def ui_text(source):
    """Translate an application-owned label for a native Qt control."""
    return QCoreApplication.translate("Tingmu", source)


@lru_cache(maxsize=2)
def _catalog(code):
    return json.loads((Path(__file__).with_name("i18n") / f"{code}.json").read_text("utf-8"))


@lru_cache(maxsize=2)
def _status_patterns(code):
    # Older queue records already contain rendered messages. Match whole templates
    # only and interpolate the original paths, IDs and quoted words verbatim.
    patterns = []
    catalog = _catalog(code)
    # Match outer diagnostics first, then the most specific complete template.
    sources = [*_MESSAGE_ARGS, *sorted(catalog.keys() - _MESSAGE_ARGS.keys(), key=len, reverse=True)]
    for source in sources:
        pieces = _ARGUMENT.split(source)
        if len(pieces) > 1:
            pattern = [re.escape(pieces[0])]
            seen = set()
            for number, literal in zip(pieces[1::2], pieces[2::2]):
                pattern.extend((f"(?P=p{number})" if number in seen else f"(?P<p{number}>.+?)", re.escape(literal)))
                seen.add(number)
            patterns.append((re.compile("".join(pattern), re.DOTALL), catalog[source], _MESSAGE_ARGS.get(source, ())))
    return patterns


@lru_cache(maxsize=2)
def _language_labels(code):
    converter = None
    if code == "zh-TW":
        from opencc import OpenCC
        converter = OpenCC("s2twp")
    labels = {}
    for row in language_choices():
        name = display_language(row["code"])
        label = _catalog(code).get(name) or (converter.convert(name) if converter else row["english"]) or name
        labels[name] = label
        labels[row["code"]] = label
    return labels


class InterfaceLanguage(QTranslator):
    changed = Signal()

    def __init__(self, root, parent=None):
        super().__init__(parent)
        self.path = confined_path(root, "interface-language.json")
        try:
            value = json.loads(self.path.read_text("utf-8"))
            selected = value.get("language") if isinstance(value, dict) else None
        except (OSError, ValueError):
            selected = None
        self._code = selected if selected in LOCALES else LOCALES[0]

    @Property(str, notify=changed)
    def code(self):
        return self._code

    @Slot(str, result=bool)
    def select(self, code):
        if code not in LOCALES:
            return False
        if code != self._code:
            atomic_json(self.path, {"language": code})
            self._code = code
            self.changed.emit()
        return True

    def isEmpty(self):
        return False

    def translate(self, context, source, disambiguation=None, n=-1):
        if context in ("QPlatformTheme", "QDialogButtonBox") and self._code != "en":
            label = _STANDARD.get(source.replace("&", ""))
            if label:
                return _catalog("zh-TW").get(label, label) if self._code == "zh-TW" else label
        if self._code == "zh-CN" or not source:
            return None
        if context == "TingmuLanguage":
            labels = _language_labels(self._code)
            return " → ".join(labels.get(name, name) for name in source.split(" → "))
        if context == "TingmuEvidence":
            # Candidate names may be arbitrary filenames. Translate only the generated
            # track label and the reason following the full-width separator.
            name, separator, reason = source.rpartition("：")
            if separator:
                if name == "指定字幕":
                    name = _catalog(self._code)["指定字幕"]
                elif re.fullmatch(r"内嵌字幕轨 \d+", name):
                    name = self.translate("TingmuStatus", name)
                reason = self.translate("TingmuStatus", reason) or reason
                return name + (": " if self._code == "en" else "：") + reason
            context = "TingmuStatus"
        if context not in ("Tingmu", "TingmuStatus"):
            return None
        catalog = _catalog(self._code)
        result = catalog.get(source)
        if result is not None or context != "TingmuStatus":
            return result
        for pattern, template, nested in _status_patterns(self._code):
            match = pattern.fullmatch(source)
            if match:
                def argument(arg):
                    value = match.group(f"p{arg[1]}")
                    if arg[1] in nested:
                        return self.translate("TingmuStatus", value) or value
                    return value
                return _ARGUMENT.sub(argument, template)
        return None
