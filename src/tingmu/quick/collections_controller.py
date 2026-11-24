"""Collection indexes, cue notes and frozen subtitle edits through finite ports."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from tingmu.models import AppSettings, Cue, StudyDocument, Token
from tingmu.storage import word_key

if TYPE_CHECKING:
    from tingmu.conversations import ConversationStore
    from tingmu.notebooks import NotebookManager
    from tingmu.storage import Store


@dataclass
class CollectionsState:
    entries: list[dict] = field(default_factory=list)
    entries_revision: int = 0
    note_media_path: str = ""
    cue_notes: dict[tuple[str, str, str], str] = field(default_factory=dict)
    saved_sentences: set[tuple[str, str, str, str]] = field(default_factory=set)
    word_states_scope: tuple[str, str, str] | None = None
    word_states: dict[tuple[str | None, str], str] = field(default_factory=dict)


@dataclass(frozen=True)
class CollectionsPorts:
    settings: Callable[[], AppSettings]
    media_path: Callable[[], str]
    store: Callable[[], Store]
    notebook_manager: Callable[[], NotebookManager]
    find_cue: Callable[[str], Cue | None]
    source_ids: Callable[[str], tuple[str, ...]]
    source_cue: Callable[[str], Cue | None]
    document: Callable[[], StudyDocument | None]
    imported_cue: Callable[[], dict]
    embedded_cue: Callable[[], dict]
    conversations: Callable[[], ConversationStore]
    active_session_id: Callable[[], str]
    messages: Callable[[], list[dict]]
    collect_word: Callable[[str, dict], str]
    refresh_notebooks: Callable[[], None]
    activate_context_session: Callable[[], None]
    send_chat: Callable[..., Any]
    command: Callable[[str, dict], Any]
    error: Callable[..., None]
    error_text: Callable[[], str]
    log_exception: Callable[[Exception, str], None]
    set_state: Callable[..., None]
    entries_changed: Callable[[], None]
    collection_feedback: Callable[[bool, str], None]
    word_key: Callable[[str, str], str] = word_key


class CollectionsController:
    def __init__(self, state: CollectionsState, ports: CollectionsPorts):
        self.state = state
        self.ports = ports

    def _refresh_entries(self):
        self.state.entries = (
            self.ports.store().entries(self.ports.media_path())
            if self.ports.media_path()
            else self.ports.store().entries()
        )
        self.state.entries_revision += 1
        self.state.note_media_path = self.ports.media_path()
        self.state.cue_notes.clear()
        self.state.saved_sentences.clear()
        if self.ports.media_path():
            for entry in self.state.entries:  # entries for the open video, in display order
                if entry["kind"] == "note":
                    key = (entry["source_language"], entry["target_language"], entry["cue_id"])
                    self.state.cue_notes.setdefault(key, entry["note"])
                    self.state.saved_sentences.add((*key, entry["text"]))
        self.state.word_states = self.ports.notebook_manager().playback_word_states(
            self.ports.media_path(), self.ports.settings().language
        )
        self.state.word_states_scope = (
            self.ports.media_path(),
            self.ports.settings().source_language,
            self.ports.settings().target_language,
        )
        self.ports.entries_changed()

    def cueNote(self, cue_id):
        if self.state.note_media_path != self.ports.media_path():
            return ""
        return self.state.cue_notes.get(
            (self.ports.settings().source_language, self.ports.settings().target_language, cue_id), ""
        )

    def sentenceCollected(self, cue_id, sentence):
        return (
            self.state.note_media_path == self.ports.media_path()
            and (
                self.ports.settings().source_language,
                self.ports.settings().target_language,
                cue_id,
                sentence,
            )
            in self.state.saved_sentences
        )

    def wordCollectionStates(self, cue_id, texts):
        scope = (
            self.ports.media_path(),
            self.ports.settings().source_language,
            self.ports.settings().target_language,
        )
        if not self.ports.media_path() or not cue_id or scope != self.state.word_states_scope:
            return {}
        states = {}
        for text in dict.fromkeys(texts):
            key = self.ports.word_key(text, self.ports.settings().target_language)
            states[text] = self.state.word_states.get(
                (cue_id, key), self.state.word_states.get((None, key), "unsaved")
            )
        return states

    def collectWord(self, payload):
        name = "saveSelection" if payload.get("tokenIndex") == -1 else "saveWord"
        try:
            return self.ports.collect_word(name, payload)
        except Exception as exc:
            self.ports.log_exception(exc, "command_error")
            self.ports.error(exc, domain="learning")
            self.ports.collection_feedback(False, self.ports.error_text())
            return ""

    def _collect_word(self, name, payload):
        cue = self.ports.find_cue(payload.get("cueId", ""))
        if (
            not self.ports.media_path()
            or not cue
            or payload.get("mediaPath", self.ports.media_path()) != self.ports.media_path()
            or payload.get("cueText", cue.text) != cue.text
            or payload.get("sourceLanguage", self.ports.settings().source_language)
            != self.ports.settings().source_language
            or payload.get("targetLanguage", self.ports.settings().target_language)
            != self.ports.settings().target_language
        ):
            raise ValueError("字幕已切换，请重新选择要收藏的词。")
        if name == "saveWord":
            index = int(payload["tokenIndex"])
            if not 0 <= index < len(cue.tokens):
                raise ValueError("字幕已切换，请重新选择要收藏的词。")
            token = cue.tokens[index]
            if payload.get("text", token.text) != token.text:
                raise ValueError("词卡已更新，请重新选择要收藏的词。")
        else:
            text = str(payload.get("text", ""))
            if not text.strip() or text not in cue.text or len(text) > 200:
                raise ValueError("请选择当前字幕中不超过 200 字的原文")
            token = next((item for item in cue.tokens if item.text == text), Token(text))
        outcome = self.ports.notebook_manager().collect_word(
            self.ports.media_path(),
            cue,
            token,
            self.ports.settings().language,
            confirm=payload.get("confirm") is True,
        )
        feedback = {
            "collected": f"已收藏「{token.text}」" if name == "saveWord" else "所选词句已加入收藏箱",
            "pending": f"「{token.text}」已收藏，正在等待整理。",
            "pending_source": f"「{token.text}」已收藏，已补充本句出处。",
            "confirm": f"「{token.text}」已收藏并整理。再次点击可重新收藏，并保留本句出处。",
            "referenced": f"已重新收藏「{token.text}」，本句出处已关联到原笔记。",
        }
        expected = {"pending": "pending", "confirm": "organized"}.get(outcome)
        if expected is None or self.wordCollectionStates(cue.id, [token.text]).get(token.text) != expected:
            # A second window/process may have changed this word since the last refresh.
            self.ports.refresh_notebooks()
        self._collection_saved(feedback[outcome])
        return outcome

    def _collect_sentence(self, payload):
        if not self.ports.media_path() or payload.get("mediaPath") != self.ports.media_path():
            raise ValueError("视频已切换，请重新选择要收藏的句子。")
        track = payload.get("track")
        cue_id = payload.get("cueId")
        cue = None
        if track == "learning":
            ids = self.ports.source_ids(cue_id)
            if ids:
                cue = self.ports.source_cue(ids[0])
        else:
            display = (
                self.ports.imported_cue()
                if track == "imported"
                else self.ports.embedded_cue()
                if track == "embedded"
                else {}
            )
            if display and display["id"] == cue_id:
                cue = Cue(cue_id, display["start"], display["end"], display["text"])
        if not cue or cue.text != payload.get("text"):
            raise ValueError("字幕已切换，请重新选择要收藏的句子。")
        self.ports.store().save_note(self.ports.media_path(), cue, None, self.ports.settings().language)
        self.ports.refresh_notebooks()
        self._collection_saved("整句已加入收藏箱")

    def _collection_saved(self, message):
        self.ports.set_state(status=message, error="")
        self.ports.collection_feedback(True, message)

    def _frozen_subtitle(self, cue_id):
        from tingmu.corrections import freeze_target

        ids = self.ports.source_ids(cue_id)
        if self.ports.document() and len(ids) == 1 and self.ports.source_cue(ids[0]) is not None:
            return freeze_target(self.ports.document(), self.ports.source_cue(ids[0]), cue_id)
        return None

    def captureSubtitle(self, cue_id):
        cue = self.ports.find_cue(cue_id) if cue_id else None
        frozen = self._frozen_subtitle(cue_id)
        if not cue or not frozen:
            return {}
        document, srt = self.ports.store().material_paths(
            self.ports.media_path(), self.ports.settings().language
        )
        return {
            "target": frozen,
            "text": cue.text,
            "start": cue.start,
            "media": self.ports.media_path(),
            "language": asdict(self.ports.settings().language),
            "documentPath": str(document),
            "srtPath": str(srt),
        }

    def submitSubtitleCorrection(self, captured, guidance):
        try:
            target = captured.get("target") or {}
            cue_id = target.get("display_id", "")
            if not target or captured != self.captureSubtitle(cue_id):
                return {"error": "字幕或学习材料已变化，请重新选择该句。"}
            if not guidance.strip():
                return {"error": "请填写你确认的说法或意思。"}
            session = self.ports.conversations().load(self.ports.active_session_id())
            if (
                session.get("media_path") != self.ports.media_path()
                or session.get("source_language") != self.ports.settings().source_language
                or session.get("target_language") != self.ports.settings().target_language
            ):
                self.ports.activate_context_session()
            cue = self.ports.find_cue(cue_id)
            self.ports.send_chat(
                "请按我确认的意思复核并保存本句的字幕、译文和讲解。\n" + guidance.strip(),
                request_context={
                    "media_path": self.ports.media_path(),
                    "language": asdict(self.ports.settings().language),
                    "cue": asdict(cue),
                    "correction": target,
                    "subtitle_editor": True,
                },
            )
            return {"messageId": self.ports.messages()[-1]["id"]}
        except Exception as exc:
            self.ports.log_exception(exc, "subtitle_correction_error")
            return {"error": str(exc) if isinstance(exc, ValueError) else "未能提交修订，请检查模型连接。"}

    def saveCueNote(self, cue_id, note):
        try:
            if not self.ports.find_cue(cue_id):
                raise ValueError("当前字幕已不存在，草稿已保留")
            self.ports.command("saveNote", {"cueId": cue_id, "note": note})
            return True
        except Exception as error:
            self.ports.log_exception(error, "cue_note_save_error")
            self.ports.error(error, domain="learning")
            return False
