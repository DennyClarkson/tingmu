"""Stable preparation stage identities and legacy presentation adapters."""

STAGE_LABELS = {
    "planning": "字幕评估",
    "transcript": "字幕识别",
    "review": "文本复核",
    "teaching": "逐句讲解",
    "local": "本地分词",
    "verification": "译稿核验",
    "publish": "保存字幕",
}

_LEGACY_CODES = {label: code for code, label in STAGE_LABELS.items()}
_LEGACY_CODES.update({"读取原字幕": "transcript", "学习讲解": "teaching", "译文核验": "verification"})


def stage_code(label: str) -> str:
    """Decode older progress records only; new events carry their own code."""
    return _LEGACY_CODES.get(label, "")


def stage_label(code: str) -> str:
    return STAGE_LABELS.get(code, "")
