"""Owned model, conversation and notebook state, separate from Qt presentation."""

from dataclasses import dataclass, field

from .list_models import MessageMarkdownCache


@dataclass
class ChatState:
    messages: list = field(default_factory=list)
    sessions: list = field(default_factory=list)
    active_session_id: str = ""
    worker_session_id: str = ""
    selected_media: list = field(default_factory=list)
    markdown: MessageMarkdownCache = field(default_factory=MessageMarkdownCache)


@dataclass
class ModelState:
    profiles: list = field(default_factory=list)
    presets: list = field(default_factory=list)
    discovered: list = field(default_factory=list)
    status: str = ""


@dataclass
class LearningState:
    language: str = ""
    notebooks: dict = field(default_factory=dict)


@dataclass
class FeatureState:
    chat: ChatState = field(default_factory=ChatState)
    models: ModelState = field(default_factory=ModelState)
    learning: LearningState = field(default_factory=LearningState)
