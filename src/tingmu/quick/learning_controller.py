"""Learning domain operations with owned state and explicit callback ports."""

import re

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices


from pathlib import Path
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class LearningPorts:
    collect_word: Callable
    collect_sentence: Callable
    find_cue: Callable
    store: Callable
    media_path: Callable
    settingsChanged: Any
    open_media: Callable
    load_document: Callable
    load_personal: Callable
    seek: Callable
    command: Callable
    error: Callable
    export_destination: Callable
    is_closed: Callable
    model_registry: Callable
    notebook_manager: Callable
    notebooksChanged: Any
    refresh_entries: Callable
    set_state: Callable
    setting: Callable
    settings: Callable
    start_job: Callable
    task_workers: Callable
    tutor_transport: Callable
    v4_send_chat: Callable


class LearningController:
    def __init__(self, state, ports):
        self.state, self.ports = state, ports

    def set_language(self, target):
        self.state.language = target

    def refresh(self):
        return self._refresh_notebooks()

    def _refresh_notebooks(self, snapshot=None):
        if self.ports.is_closed():
            return
        manager = self.ports.notebook_manager()
        with manager.database.session():
            try:
                from tingmu.richtext import markdown_blocks

                previous = self.state.notebooks
                if previous.get("language") != self.state.language:
                    previous = {}
                snapshot = snapshot or manager.snapshot(self.state.language)
                if previous.get("revision") == snapshot["revision"] and "blocks" in previous:
                    snapshot["blocks"] = previous["blocks"]
                else:
                    preview = re.sub(
                        r"(?m)^<!-- tingmu:(?:collected:(?:start|end)|note:[^:>]+:(?:start|end|sources)) -->[ \t]*$",
                        "", snapshot["markdown"],
                    )
                    snapshot["blocks"] = markdown_blocks(preview)
                old_notes = {note["id"]: note for note in previous.get("notes", [])}
                for note in snapshot["notes"]:
                    old = old_notes.get(note["id"], {})
                    text = note.get("markdown", note["body"])
                    note["blocks"] = (old["blocks"] if old.get("markdown", old.get("body")) == text and "blocks" in old
                                      else markdown_blocks(text))
                self.state.notebooks = snapshot
                self.ports.notebooksChanged.emit()
            except (OSError, ValueError) as exc:
                self.ports.error(exc, domain="learning")
            finally:
                self.ports.refresh_entries()

    def _organize_notebook(self, *, entry_ids=None, instructions=""):
        enabled, profile = self.ports.model_registry().text_role(self.ports.settings(), "companion")
        if not enabled:
            raise ValueError(
                "当前工作区未允许此模型。可在「任务分配」选择本机连接，或在「当前工作区的使用权限」选择「允许远程文本 API」。"
            )
        if {"chat", "summary", "notebook"} & self.ports.task_workers().keys():
            raise ValueError("请等待当前回复或笔记整理完成")
        target, source = self.state.language, self.ports.settings().source_language
        agent = self.ports.tutor_transport(profile=profile, provider=self.ports.settings().companion_provider)
        entry_ids = tuple(entry_ids) if entry_ids is not None else None

        def work(progress, cancelled):
            return self.ports.notebook_manager().organize(
                target,
                agent,
                source_language=source,
                cancelled=cancelled,
                entry_ids=list(entry_ids) if entry_ids is not None else None,
                instructions=instructions,
            )

        def completed(result):
            self._refresh_notebooks()
            self.ports.set_state(domain="learning", status="学习笔记已按主题整理", learningBusy=False)

        self.ports.start_job("notebook", work, completed)

    def command_open_link(self, name, p):
        from tingmu.richtext import safe_link

        url = str(p.get("url", ""))
        if not safe_link(url):
            raise ValueError("不支持此链接")
        match = re.fullmatch(r"tingmu://entry/(\d+)", url)
        if match:
            self.ports.command("openEntry", {"id": int(match[1])})
        elif QUrl(url).scheme().lower() in ("https", "http") and QUrl(url).host():
            QDesktopServices.openUrl(QUrl(url))
        else:
            raise ValueError("不支持此链接")

    def command_select_notebook(self, name, p):
        self.state.language = str(p.get("language", self.ports.settings().target_language))
        self._refresh_notebooks(self.ports.notebook_manager().sync(self.state.language))

    def command_sync_notebook(self, name, p):
        self._refresh_notebooks(self.ports.notebook_manager().sync(self.state.language))
        self.ports.set_state(domain="learning", status="收藏箱与笔记已刷新；可选择 AI 整理写入笔记")

    def command_save_notebook(self, name, p):
        language = str(p.get("language", self.state.language))
        saved = self.ports.notebook_manager().save(language, str(p["markdown"]), str(p["revision"]))
        self._refresh_notebooks(saved if language == self.state.language else None)
        self.ports.set_state(domain="learning", status="学习笔记已保存")

    def command_organize_notebook(self, name, p):
        self._organize_notebook(entry_ids=p.get("ids"), instructions=str(p.get("instructions", "")))

    def command_open_notebook(self, name, p):
        path = self.ports.notebook_manager().sync(self.state.language)["path"]
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def command_export_notebook(self, name, p):
        snapshot = self.ports.notebook_manager().snapshot(self.state.language)
        path = self.ports.export_destination(p.get("path"), f"{self.state.language}-学习笔记.md", (".md",))
        if path:
            path.write_text(snapshot["markdown"], encoding="utf-8")
            self.ports.set_state(domain="learning", status=f"Markdown 笔记已导出：{path}")

    def command_update_entry_note(self, name, p):
        self.ports.notebook_manager().update_entry_note(
            int(p["id"]),
            str(p.get("note", "")),
            expected_revision=str(p["revision"]),
            target_language=self.state.language,
        )
        self._refresh_notebooks()
        self.ports.set_state(domain="learning", status="个性化备注已保存")

    def command_review_entry(self, name, p):
        self.ports.notebook_manager().review(int(p["id"]), str(p["rating"]))
        self._refresh_notebooks()

    def command_practice_notebook(self, name, p):
        self.state.notebooks["practice"] = self.ports.notebook_manager().practice(self.state.language)
        self.ports.notebooksChanged.emit()

    def command_ask_notebook(self, name, p):
        if self.state.language != self.ports.settings().target_language:
            self.ports.setting("target_language", self.state.language)
        self.ports.v4_send_chat(str(p.get("text", "请根据我的学习笔记帮我练习今天需要复习的词语。")))

    def command_save_word(self, name, p):
        return self.ports.collect_word(name, p)

    def command_save_sentence(self, name, p):
        self.ports.collect_sentence(p)

    def command_save_note(self, name, p):
        cue = self.ports.find_cue(p.get("cueId", ""))
        if cue:
            self.ports.store().save_note(
                self.ports.media_path(), cue, str(p.get("note", "")), self.ports.settings().language
            )
            try:
                self.ports.notebook_manager().sync(self.ports.settings().target_language)
            finally:
                self._refresh_notebooks()
            self.ports.set_state(status="句子笔记已保存")

    def command_delete_entry(self, name, p):
        self.ports.store().delete_entry(int(p["id"]))
        try:
            self.ports.notebook_manager().sync(self.state.language)
        finally:
            self._refresh_notebooks()

    def command_open_entry(self, name, p):
        entry = self.ports.notebook_manager().source_entry(int(p["id"]))
        if entry:
            changed = (
                self.ports.settings().source_language != entry["source_language"]
                or self.ports.settings().target_language != entry["target_language"]
            )
            if changed:
                self.ports.settings().source_language = entry["source_language"]
                self.ports.settings().target_language = entry["target_language"]
                self.ports.store().save_settings(self.ports.settings())
                self.ports.settingsChanged.emit()
            if entry["media_path"] != self.ports.media_path():
                self.ports.open_media(entry["media_path"])
            elif changed:
                self.ports.load_document()
                self.ports.load_personal()
            position = round(entry["start"] * 1000)
            if entry["kind"] == "word":
                position += self.ports.settings().subtitle_offset_ms
            self.ports.seek(position)

    def command_export_notes(self, name, p):
        path = self.ports.export_destination(p.get("path"), "学习记录.md", (".md", ".csv"))
        if path:
            method = (
                self.ports.store().export_csv
                if Path(path).suffix.lower() == ".csv"
                else self.ports.store().export_markdown
            )
            method(Path(path), self.ports.media_path() or None)
            self.ports.set_state(status=f"学习记录已导出：{path}")
