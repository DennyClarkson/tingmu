"""Small serializable domain objects shared by UI, providers and storage."""

from dataclasses import InitVar, asdict, dataclass, field, fields
import math


MAX_LLM_CONCURRENCY = 16
DEFAULT_LLM_CONCURRENCY = 4


def preparation_identity_settings(settings: dict) -> dict:
    """Scheduling changes can resume the same material and queue entry."""
    return {key: value for key, value in settings.items()
            if key not in ("llm_high_concurrency", "llm_max_concurrency")
            and not (key == "preparation_workflow" and value == "classic")}


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
    speaker: str = ""

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
    auto_prepare_media: bool = False
    source_language: str = "zh-CN"
    target_language: str = "yue"
    level: str = "beginner"
    provider: str = "openai"
    model: str = "gpt-5.4-mini"
    asr_model: str = "large-v3-turbo"
    device: str = "auto"
    audio_track: int = 0
    audio_output_id: str = ""
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
    asr_backend: str = "local"
    asr_endpoint: str = "https://api.openai.com/v1/audio/transcriptions"
    external_asr_model: str = "whisper-1"
    asr_key_env: str = "TINGMU_ASR_API_KEY"
    asr_response_format: str = "verbose_json"
    allow_audio_upload: bool = False
    suppress_bgm: bool = True
    speaker_turns: bool = True
    local_asr_endpoint: str = "http://127.0.0.1:8000/v1/audio/transcriptions"
    local_asr_api_model: str = "whisper-1"
    local_asr_key_env: str = ""
    local_asr_response_format: str = "verbose_json"
    text_models_enabled: bool = True
    llm_high_concurrency: bool = True
    llm_max_concurrency: int = DEFAULT_LLM_CONCURRENCY
    preparation_workflow: str = "classic"

    _loaded_roles: InitVar[dict | None] = None

    def __post_init__(self, _loaded_roles):
        self._loaded_roles = dict(_loaded_roles or {})
        if self.preparation_workflow not in ("classic", "subtitle_agent"):
            self.preparation_workflow = "classic"
        if not isinstance(self.llm_high_concurrency, bool):
            self.llm_high_concurrency = str(self.llm_high_concurrency).lower() in ("true", "1")
        try:
            limit = int(self.llm_max_concurrency)
        except (TypeError, ValueError, OverflowError):
            limit = DEFAULT_LLM_CONCURRENCY
        self.llm_max_concurrency = max(1, min(MAX_LLM_CONCURRENCY, limit))

    @property
    def llm_workers(self) -> int:
        return self.llm_max_concurrency if self.llm_high_concurrency else 1

    @property
    def language(self) -> LanguagePair:
        return LanguagePair(self.source_language, self.target_language, self.level)
