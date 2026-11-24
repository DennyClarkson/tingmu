"""Background-only media inbox observations and installed-model preparation checks."""

from __future__ import annotations

import os
from pathlib import Path
import time

from .media import MEDIA_EXTENSIONS, SUBTITLE_EXTENSIONS
from .pipeline import find_subtitle
from .storage import media_fingerprint


def _not_being_written(path):
    """Windows copy handles must close before the inbox accepts a finished file."""
    if os.name != "nt":
        return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    # Share reads only: an existing writer (including Explorer) prevents acquisition.
    handle = create(str(path), 0x80000000, 1, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        return False
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    close(handle)
    return True


class InboxScanner:
    """Require two matching observations separated by a quiet period, including sidecars.

    All recursive IO belongs on its caller's worker thread. No symlink or Windows
    junction is followed, even when it points back into the workspace.
    """

    def __init__(self, folder: Path, quiet_seconds=3.0):
        self.folder = Path(folder)
        self.quiet_seconds = quiet_seconds
        self.observed = {}
        self._stages = {}

    def document_stages(self, store, ready, language, cancelled):
        """Check only changed study files on a stable inbox scan."""
        stages, refreshed = {}, {}
        for media, fingerprint in ready.items():
            if cancelled():
                return {}
            document_path, _ = store.material_paths(media, language)
            pending = document_path.with_suffix(".pending").exists()
            try:
                stat = document_path.stat()
                version = (document_path, stat.st_mtime_ns, stat.st_size, stat.st_ino, fingerprint)
            except FileNotFoundError:
                version = None
            previous = self._stages.get(media)
            if version is not None and not pending and previous and previous[0] == version:
                stage = previous[1]
                refreshed[media] = previous
            elif version is not None or pending:
                document = store.load_document(media, language)
                stage = document.metadata.get("stage", "") if document else ""
                if document is not None and not pending:
                    refreshed[media] = (version, stage)
            else:
                stage = ""
            stages[media] = stage
        self._stages = refreshed
        return stages

    def scan(self, *, now=None, cancelled=lambda: False, agent_workflow=False):
        now = time.monotonic() if now is None else now
        files = {}
        errors = []
        root = self.folder
        if root.resolve() != root or root.is_symlink() or root.is_junction():
            return {"ready": {}, "present": [], "waiting": [], "error": "媒体目录不能是链接"}
        def walk_error(error):
            errors.append(type(error).__name__)
        for directory, folders, names in os.walk(root, followlinks=False, onerror=walk_error):
            if cancelled():
                return None
            parent = Path(directory)
            if parent.resolve() != parent:
                folders[:] = []
                continue
            folders[:] = sorted(name for name in folders if not name.startswith(".")
                                and not (parent / name).is_symlink() and not (parent / name).is_junction())
            for name in sorted(names):
                path = parent / name
                if name.startswith(".") or path.suffix.lower() not in MEDIA_EXTENSIONS | SUBTITLE_EXTENSIONS:
                    continue
                if path.is_symlink() or path.is_junction() or path.resolve() != path:
                    continue
                fingerprint = media_fingerprint(path)
                if fingerprint and path.is_file():
                    files[str(path)] = fingerprint
        ready, waiting, present, next_observed = {}, [], [], {}
        subtitles = {}
        for path, fingerprint in files.items():
            item = Path(path)
            if item.suffix.lower() in SUBTITLE_EXTENSIONS:
                subtitles.setdefault(item.parent, []).append((path, item.stem, fingerprint))
        for path, fingerprint in files.items():
            media = Path(path)
            if media.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            present.append(path)
            companions = {p: fp for p, stem, fp in subtitles.get(media.parent, [])
                          if stem == media.stem or stem.startswith(media.stem + ".")}
            if agent_workflow and subtitles.get(media.parent):
                from .subtitle_sources import discover_sidecars
                companions.update({str(p): files[str(p)] for p in discover_sidecars(media) if str(p) in files})
            signature = (fingerprint, companions)
            previous, since = self.observed.get(path, (None, now))
            since = since if signature == previous else now
            next_observed[path] = (signature, since)
            if (fingerprint["size"] > 0 and now - since >= self.quiet_seconds
                    and all(_not_being_written(Path(p)) for p in [path, *companions])):
                ready[path] = fingerprint
            else:
                waiting.append(path)
        self.observed = next_observed
        return {"ready": ready, "present": present, "waiting": waiting,
                "error": "媒体目录暂时无法读取，稍后重试" if errors else ""}


def preparation_block_reason(settings, paths):
    """Return actionable reason without starting inference or downloading anything."""
    paths = [Path(path) for path in paths]
    # Embedded and bilingual captions can eliminate ASR entirely. Inspect them once
    # in the queue worker; automatic jobs still prohibit downloads and enforce consent.
    if settings.preparation_workflow == "subtitle_agent":
        return ""
    if not paths or all(find_subtitle(path, settings.target_language) for path in paths):
        return ""
    if settings.asr_backend == "external":
        return "" if settings.allow_audio_upload is True else "请先明确允许将音轨发送到已配置的转写服务"
    if settings.asr_backend == "local_api":
        return ""
    if settings.asr_backend != "local":
        return "请先选择有效的语音识别方式"
    from .model_advisor import model_supports_language
    if not model_supports_language(settings.asr_model, settings.target_language):
        return (f"所选识别模型 {settings.asr_model} 无法识别 {settings.target_language}。"
                "请添加与视频同名的字幕文件，或在工作区设置中选择支持该语言的模型或转写 API。")
    from .workspace_models import model_readiness
    if model_readiness(settings.asr_model).ready:
        return ""
    return f"等待安装本地识别模型 {settings.asr_model}；请在聊天中请求下载并确认，或在工作区设置中下载"
