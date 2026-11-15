"""Written-script conversion is separate from recognizing spoken wording."""

from dataclasses import replace
from functools import lru_cache
import re

from .models import Cue, LanguagePair


@lru_cache(maxsize=1)
def _traditional_converter():
    from opencc import OpenCC
    return OpenCC("s2hk")


def learning_text(text: str, language: LanguagePair, target_script: str = "traditional") -> str:
    if target_script == "traditional" and language.target.lower() in ("yue", "zh-hk", "cantonese"):
        # OpenCC's general Chinese phrase table can turn 仲系好想 into 仲繫好想
        # (interpreting 系好 as "tie securely"). Normalize only familiar Cantonese
        # grammatical spellings first. Never globally replace 系 (系統/系列 etc.).
        text = text.replace("系咪", "係咪")
        text = re.sub(r"(梗|但|仲|唔|就|都|真)系(?![統统列數数譜谱組组所內内外主友學学])", r"\1係", text)
        return _traditional_converter().convert(text)
    return text


def normalize_cues(cues: list[Cue], language: LanguagePair, target_script: str = "traditional") -> list[Cue]:
    """Preserve raw text/timing; align display tokens if contextual conversion differs."""
    result = []
    for cue in cues:
        text = learning_text(cue.text, language, target_script)
        tokens = [replace(t, text=learning_text(t.text, language, target_script)) for t in cue.tokens]
        if tokens and re.sub(r"\s+", "", "".join(t.text for t in tokens)) != re.sub(r"\s+", "", text):
            # Conversion may depend on adjacent characters. Never leave clickable tokens
            # inconsistent with the sentence; dictionary-only rebuilding is deterministic.
            from .agent import annotate_local
            tokens = annotate_local([replace(cue, text=text)], language)[0].tokens
        result.append(replace(cue, text=text, tokens=tokens))
    return result
