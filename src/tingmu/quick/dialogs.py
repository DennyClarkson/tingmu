"""Native dialog adapter; trusted UI requests keep their originating QML host."""

from dataclasses import dataclass
from typing import Any, Callable
from pathlib import Path
from tingmu.media_files import MEDIA_EXTENSIONS
from tingmu.lesson_material import translated_subtitles
from tingmu.storage import canonical_path
from .localization import ui_text


class QtDialogs:
    def __init__(self, files, inputs, messages):
        self.files = files
        self.inputs = inputs
        self.messages = messages

    def getOpenFileNames(self, *args, **kwargs):
        return self.files.getOpenFileNames(*args, **kwargs)

    def getExistingDirectory(self, *args, **kwargs):
        return self.files.getExistingDirectory(*args, **kwargs)

    def getOpenFileName(self, *args, **kwargs):
        return self.files.getOpenFileName(*args, **kwargs)

    def getSaveFileName(self, *args, **kwargs):
        return self.files.getSaveFileName(*args, **kwargs)

    def getText(self, *args, **kwargs):
        return self.inputs.getText(*args, **kwargs)

    def question(self, *args, **kwargs):
        return self.messages.question(*args, **kwargs)


@dataclass(frozen=True)
class DialogPorts:
    sources: Any
    add_paths: Callable
    copy_media: Callable
    cues: Callable
    dialogs: Any
    document: Callable
    enqueue: Callable
    export_destination: Callable
    import_subtitle: Callable
    media_path: Callable
    open_media: Callable
    set_state: Callable
    settings: Callable
    start_job: Callable
    store: Any
    timeline: Callable


class DialogService:
    def __init__(self, ports: DialogPorts):
        self.ports = ports

    def command_openFiles(self, name, p):
        selected, _ = self.ports.dialogs.getOpenFileNames(
            None,
            ui_text("选择视频（可多选）"),
            self.ports.settings().library_path,
            ui_text("音视频")
            + " ("
            + " ".join("*" + ext for ext in sorted(MEDIA_EXTENSIONS))
            + ");;"
            + ui_text("所有文件")
            + " (*)",
        )
        if selected:
            if name == "copyFiles":
                self.ports.copy_media(selected)
            else:
                self.ports.add_paths(selected)
            self.ports.settings().library_path = str(Path(selected[0]).parent)
            self.ports.store.save_settings(self.ports.settings())
            if name == "openFiles" and len(selected) == 1:
                self.ports.open_media(selected[0])

    def command_openFolder(self, name, p):
        selected = self.ports.dialogs.getExistingDirectory(
            None, ui_text("添加视频文件夹"), self.ports.settings().library_path
        )
        if selected:
            self.ports.add_paths([selected])
            self.ports.settings().library_path = selected
            self.ports.store.save_settings(self.ports.settings())

    def command_importSubtitle(self, name, p):
        if not self.ports.media_path():
            raise ValueError("请先打开字幕对应的视频")
        path = p.get("path")
        if not path:
            title = "导入播放字幕" if name == "importSubtitle" else "从字幕建立学习文档"
            if (
                name == "importStudySubtitle"
                and self.ports.settings().preparation_workflow == "subtitle_agent"
            ):
                title = "选择字幕并加入 Agent 队列…"
            path, _ = self.ports.dialogs.getOpenFileName(
                None,
                ui_text(title),
                str(Path(self.ports.media_path()).parent),
                ui_text("字幕") + " (*.srt *.ass *.ssa *.vtt)",
            )
        if path:
            if (
                name == "importStudySubtitle"
                and self.ports.settings().preparation_workflow == "subtitle_agent"
            ):
                self.ports.enqueue(
                    [self.ports.media_path()], {canonical_path(self.ports.media_path()): canonical_path(path)}
                )
            elif name == "importStudySubtitle":
                self.ports.import_subtitle(path)
            else:
                media = self.ports.media_path()

                def imported(track):
                    if self.ports.media_path() == media:
                        self.ports.sources.install(track)
                        self.ports.set_state(status=f"已导入播放字幕：{track.name}")

                self.ports.start_job(
                    "display-import", lambda *_: self.ports.sources.import_file(media, path), imported
                )

    def command_exportSubtitle(self, name, p):
        if not self.ports.cues():
            raise ValueError("当前没有字幕")
        translation = name == "exportTranslation"
        timeline = self.ports.timeline()
        cues = translated_subtitles(self.ports.document(), timeline=timeline) if translation else timeline.export_cues()
        if not cues:
            raise ValueError("请先补全逐句讲解与译稿，完成复核后可导出整片译稿。")
        filename = (
            f"{Path(self.ports.media_path()).stem}.{self.ports.settings().source_language}.srt"
            if translation
            else "字幕.srt"
        )
        path = self.ports.export_destination(p.get("path"), filename, (".srt",))
        if path:
            from tingmu.media import save_srt
            save_srt(cues, path)
            self.ports.set_state(status=f"字幕已导出：{path}")
