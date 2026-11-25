"""Background-only media inbox observations and installed-model preparation checks."""

from __future__ import annotations

from functools import cache
import os
import stat
from pathlib import Path
import time

from .media_files import MEDIA_EXTENSIONS, SUBTITLE_EXTENSIONS, find_subtitle


_INBOX_EXTENSIONS = MEDIA_EXTENSIONS | SUBTITLE_EXTENSIONS


@cache
def _win_file_api():
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    return create, close, wintypes.HANDLE(-1).value


def _reparse(info):
    return bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _not_being_written(path):
    """Windows copy handles must close before the inbox accepts a finished file."""
    if os.name != "nt":
        return True
    create, close, invalid = _win_file_api()
    # Share reads only and open the reparse point itself in case the entry changed.
    handle = create(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle == invalid:
        return False
    try:
        # Recheck the accepted path with the leaf pinned against rename/delete.
        info = path.lstat()
        return stat.S_ISREG(info.st_mode) and not _reparse(info) and path.resolve(strict=True) == path
    except (OSError, RuntimeError):
        return False
    finally:
        close(handle)


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
        self._ready = set()
        self.directories = {}
        self._block_cache = {}
        self._block_context = None
        self._model_reason = None
        self._model_checked_at = 0.0

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
            self._ready.clear()
            self.observed.clear()
            self.directories.clear()
            return {"ready": {}, "present": [], "waiting": [], "error": "媒体目录不能是链接"}
        directories = {}
        stack = [root]
        while stack:
            if cancelled():
                return None
            parent = stack.pop()
            try:
                if parent.resolve() != parent:
                    continue
                parent_stat = parent.lstat()
                if not stat.S_ISDIR(parent_stat.st_mode) or _reparse(parent_stat):
                    continue
                with os.scandir(parent) as listing:
                    entries = sorted(listing, key=lambda entry: entry.name)
                children, local = [], {}
                for entry in entries:
                    if entry.name.startswith(".") or entry.is_symlink() or entry.is_junction():
                        continue
                    path = Path(entry.path)
                    if entry.is_dir(follow_symlinks=False):
                        children.append(path)
                    elif path.suffix.lower() in _INBOX_EXTENSIONS:
                        # Read fresh identity and attributes without following a replaced entry.
                        try:
                            info = path.lstat()
                        except FileNotFoundError:
                            continue
                        if stat.S_ISREG(info.st_mode) and not _reparse(info):
                            local[str(path)] = (info.st_size, info.st_mtime_ns, info.st_dev, info.st_ino)
                if parent.resolve() == parent:
                    directories[str(parent)] = (parent_stat.st_ino, parent_stat.st_mtime_ns)
                    files.update(local)
                    stack.extend(reversed(children))
            except FileNotFoundError:
                if parent == root:
                    errors.append("FileNotFoundError")
            except OSError as error:
                errors.append(type(error).__name__)
        self.directories = directories
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
            if (fingerprint[0] > 0 and now - since >= self.quiet_seconds
                    and ((signature == previous and path in self._ready)
                         or all(_not_being_written(Path(p)) for p in [path, *companions]))):
                ready[path] = {"size": fingerprint[0], "mtime_ns": fingerprint[1]}
            else:
                waiting.append(path)
        self._ready = set(ready)
        self.observed = next_observed
        return {"ready": ready, "present": present, "waiting": waiting,
                "error": "媒体目录暂时无法读取，稍后重试" if errors else ""}

    def preparation_reasons(self, settings, candidates, generation, cancelled):
        """Cache each path gate and share the local-model probe across candidates."""
        context = (settings.preparation_workflow, settings.target_language, settings.asr_backend,
                   settings.asr_model, settings.allow_audio_upload, settings.device, generation)
        now = time.monotonic()
        # Also detect manual changes to shared model files outside the application.
        if context != self._block_context or now - self._model_checked_at >= 60:
            self._block_cache.clear()
            self._model_reason = None
            self._model_checked_at = now
            self._block_context = context
        refreshed = {}
        for path in candidates:
            if cancelled():
                break
            media = Path(path)
            version = (self.observed.get(path), self.directories.get(str(media.parent)))
            previous = self._block_cache.get(path)
            if previous and version == previous[0]:
                refreshed[path] = previous
                continue
            if settings.preparation_workflow == "subtitle_agent" or find_subtitle(media, settings.target_language):
                reason = ""
            else:
                if self._model_reason is None:
                    self._model_reason = _asr_block_reason(settings)
                reason = self._model_reason
            refreshed[path] = version, reason
        self._block_cache = refreshed
        return {path: cached[1] for path, cached in refreshed.items()}


def preparation_block_reason(settings, paths):
    """Return actionable reason without starting inference or downloading anything."""
    paths = [Path(path) for path in paths]
    # Embedded and bilingual captions can eliminate ASR entirely. Inspect them once
    # in the queue worker; automatic jobs still prohibit downloads and enforce consent.
    if settings.preparation_workflow == "subtitle_agent":
        return ""
    if not paths or all(find_subtitle(path, settings.target_language) for path in paths):
        return ""
    return _asr_block_reason(settings)


def _asr_block_reason(settings):
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
    readiness = model_readiness(settings.asr_model)
    if readiness.code == "runtime_missing":
        return readiness.message
    if readiness.ready:
        from .runtime import portable_root
        if portable := portable_root():
            from .runtime_deps import cached_probe
            tools = cached_probe(portable, "ffmpeg")
            if not tools["ready"]:
                return tools["detail"]
            if settings.device == "cuda" and not settings.asr_model.startswith("qwen"):
                cuda = cached_probe(portable, "whisper-cuda")
                if not cuda["ready"]:
                    return cuda["detail"]
        return ""
    return f"等待安装本地识别模型 {settings.asr_model}；请在聊天中请求下载并确认，或在工作区设置中下载"
