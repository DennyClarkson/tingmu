"""Small serializable domain objects shared by UI, providers and storage."""

from dataclasses import asdict, dataclass, field, fields
import math


@dataclass
class Token:
    text: str
    reading: str = ""
    meaning: str = ""
    learn: bool = False


@dataclass
class Cue:
    id: str
    start: float
    end: float
    text: str
    original_text: str = ""
    translation: str = ""
    explanation: str = ""
    tokens: list[Token] = field(default_factory=list)
    uncertain: bool = False
    # Text-only review boundaries; source identity, raw text and acoustic times stay above.
    segmented_texts: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not math.isfinite(self.start) or not math.isfinite(self.end) or self.start < 0 or self.end < self.start:
            raise ValueError("字幕时间范围无效")
        if not self.original_text:
            self.original_text = self.text


@dataclass
class LanguagePair:
    source: str = "zh-CN"
    target: str = "yue"
    level: str = "beginner"


@dataclass
class StudyDocument:
    media_path: str
    language: LanguagePair
    cues: list[Cue]
    provider: str = "local"
    model: str = ""
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "StudyDocument":
        cues = []
        valid_cue = {f.name for f in fields(Cue)}
        valid_token = {f.name for f in fields(Token)}
        for raw in data["cues"]:
            cue = {k: v for k, v in raw.items() if k in valid_cue}
            cue["tokens"] = [Token(**{k: v for k, v in t.items() if k in valid_token}) for t in cue.get("tokens", [])]
            cues.append(Cue(**cue))
        return cls(media_path=data["media_path"], language=LanguagePair(**data["language"]), cues=cues,
                   provider=data.get("provider", "local"), model=data.get("model", ""), metadata=data.get("metadata", {}))


@dataclass
class AppSettings:
    library_path: str = ""
    source_language: str = "zh-CN"
    target_language: str = "yue"
    level: str = "beginner"
    provider: str = "openai"
    model: str = "gpt-5.4-mini"
    asr_model: str = "large-v3-turbo"
    device: str = "auto"
    audio_track: int = 0
    subtitle_offset_ms: int = 0
    cloud_text_enabled: bool = True
    companion_provider: str = "openai"
    companion_model: str = "gpt-6-astra"
    target_script: str = "traditional"
    review_enabled: bool = True
    review_model: str = "gpt-6-astra"
    review_provider: str = "openai"
    subtitle_min_ms: int = 1200
    subtitle_gap_ms: int = 500
    companion_hold_ms: int = 2200
    overlay_opacity: float = 0.86
    overlay_font_size: int = 20
    developer_mode: bool = False
    theme: str = "forest"
    annotation_profile_id: str = ""
    review_profile_id: str = ""
    companion_profile_id: str = ""
    companion_reasoning_effort: str = "default"
    teaching_preferences: str = ""

    @property
    def language(self) -> LanguagePair:
        return LanguagePair(self.source_language, self.target_language, self.level)
