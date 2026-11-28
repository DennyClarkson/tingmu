"""Simple entry points into the existing workspace, preparation queue and player."""
import json
import hashlib
from pathlib import Path

from PySide6.QtCore import QObject, Property, QUrl, Signal, Slot
from PySide6.QtWidgets import QFileDialog

from tingmu.diagnostics import media_id
from tingmu.media import MediaError, probe_media
from tingmu.media_files import MEDIA_EXTENSIONS
from tingmu.storage import atomic_json, canonical_path
from tingmu.runtime import installation_root
from tingmu.locale_catalog import display_language
from tingmu.workspaces import language_code

from .catalog_media import catalog_path, fetch_catalog_video, install_bundled_lesson
from .demo_source import DemoSource, open_local_url


class StudyHome(QObject):
    changed = Signal()
    watch = Signal()
    directionAccepted = Signal()

    def __init__(self, bridge, workspaces, *, prompt_on_launch=False):
        super().__init__(bridge)
        self.bridge = bridge
        self.workspaces = workspaces
        self.marker = workspaces.catalog.root / "home-onboarding.json"
        self.history = bridge.store.data_dir / "home-history.json"
        self.items = json.loads(Path(__file__).with_name("study_catalog.json").read_text("utf-8"))
        self.demo_source = None
        self.last = ""
        if self.history.is_file():
            try:
                self.last = json.loads(self.history.read_text("utf-8")).get("path", "")
            except (OSError, ValueError):
                pass
        if not self.last or not Path(self.last).is_file():
            positions = bridge.store.load_positions(bridge.catalog.paths)
            paths = {media_id(p): p for p in bridge.catalog.paths if Path(p).is_file()}
            self.last = ""
            for event in reversed(bridge.log.read(2000) if paths else []):
                if event.get("event") == "media_open":
                    self.last = paths.get(json.loads(event["details"]).get("media_id"), "")
                    if self.last:
                        break
            if not self.last:
                self.last = next((p for p in reversed(list(paths.values())) if positions.get(canonical_path(p))), "")
        initialized = (bridge.catalog.paths or len(workspaces.catalog.items) > 1
                       or (bridge.store.data_dir / "settings.json").is_file() or bridge.queue.snapshot())
        self.recent_directions = {}
        saved = {}
        if self.marker.is_file():
            try:
                saved = json.loads(self.marker.read_text("utf-8"))
                initialized = (saved["completed"] or bool(bridge.catalog.paths)
                               or workspaces.workspace_id != "default")
                if isinstance(saved.get("workspaces"), dict):
                    self.recent_directions = saved["workspaces"]
            except (OSError, KeyError, ValueError, TypeError):
                saved = {}
        self.pending = saved.get("processing") or ({} if initialized else {"mode": "", "workspace": ""})
        self.setup_required = bool(self.pending)
        self.verifying = False
        self.settings_revision = 0
        self.verified_speech = None
        self.page = "direction" if prompt_on_launch else "home"
        if self.setup_required:
            self._save_marker()
            self.page = ("method" if not self.pending["mode"] else
                         "speechSetup" if self.pending["workspace"] == workspaces.workspace_id else "direction")
            if self.page == "speechSetup":
                self._apply_method()
                if (self.pending["mode"] == "api" and self.pending.get("speech") == self._speech_signature()
                        and (bridge.store.data_dir / "audio-credential.json").is_file()):
                    self.verified_speech = self.pending["speech"]
                    self.page = "textSetup"
        bridge.processing_setup_pending = self.setup_required
        self.path = ""
        self.job = ""
        self.message = ""
        self.repair = ""
        self.source_id = ""
        self.audio_track = None
        bridge.jobsChanged.connect(self._progress)

        bridge.mediaStateChanged.connect(self._remember)
        bridge.settingsChanged.connect(self.changed)
        bridge.settingsChanged.connect(self._settings_changed)
        bridge.modelsChanged.connect(self._settings_changed)
        bridge.speechCredentialChanged.connect(self._settings_changed)

    def _save_marker(self):
        atomic_json(self.marker, {"completed": not self.setup_required, "workspaces": self.recent_directions,
                                  "processing": self.pending if self.setup_required else {}})

    def _complete_setup(self):
        self.setup_required = False
        try:
            self._save_marker()
        except OSError:
            self.setup_required = True
            raise
        self.pending = {}
        self.bridge.processing_setup_pending = False

    def _apply_method(self):
        backend = "external" if self.pending["mode"] == "api" else "local"
        if self.bridge.settings_snapshot().asr_backend != backend:
            self.bridge.setting("asr_backend", backend)

    def _speech_signature(self):
        from tingmu.external_asr import api_options
        settings = self.bridge.settings_snapshot()
        path = self.bridge.store.data_dir / "audio-credential.json"
        stamp = path.stat().st_mtime_ns if path.is_file() else None
        fields = [settings.target_language, settings.asr_backend, settings.allow_audio_upload, settings.asr_model,
                  settings.device, api_options(settings), stamp]
        return hashlib.sha256(json.dumps(fields).encode()).hexdigest()

    def _settings_changed(self):
        self.settings_revision += 1
        if self.setup_required and self.page == "textSetup" and self.verified_speech != self._speech_signature():
            self.verified_speech = None
            self._show("speechSetup", "语音设置已变更，请重新验证。")

    @Slot(str)
    def chooseMethod(self, mode):
        if self.page != "method" or self.verifying or mode not in ("local", "api", "skip"):
            return
        if mode == "skip" and self.pending["workspace"] == self.workspaces.workspace_id:
            try:
                self._complete_setup()
            except OSError as exc:
                self._show("method", f"无法保存首次设置：{exc}")
                return
            self._show("home")
            self.directionAccepted.emit()
            return
        self.pending["mode"] = mode
        self._save_marker()
        if self.pending["workspace"] == self.workspaces.workspace_id:
            self._apply_method()
            self._show("speechSetup")
        else:
            self._show("direction")

    @Slot()
    def changeMethod(self):
        if self.setup_required and not self.verifying:
            self.pending["mode"] = ""
            self.pending.pop("speech", None)
            self.verified_speech = None
            self._save_marker()
            self._show("method")

    @Slot()
    def verifySetup(self):
        if not self.setup_required or self.verifying or self.page not in ("speechSetup", "textSetup"):
            return
        from .processing_setup import check_speech, check_text
        speech = self.page == "speechSetup"
        mode = self.pending["mode"]
        settings = self.bridge.settings_snapshot()
        data_dir = self.bridge.store.data_dir
        registry = self.bridge.store.model_registry
        revision = self.settings_revision
        self.verifying = True
        self._show(self.page, "正在验证…")

        def verify(report, cancelled):
            def stopped():
                return cancelled() or revision != self.settings_revision
            try:
                if speech:
                    return check_speech(mode, settings, data_dir, stopped)
                check_text(settings, registry, stopped)
                return "", ""
            except Exception as exc:
                return ("speech" if speech else "text"), str(exc)

        def done(result):
            self.verifying = False
            if revision != self.settings_revision:
                self._show(self.page, "验证期间设置已变更，请重新验证。")
                return
            repair, error = result
            if error:
                self._show(self.page, error, repair)
                return
            if speech:
                self.verified_speech = self._speech_signature()
            if speech and mode == "api":
                self.pending["speech"] = self.verified_speech
                self._save_marker()
                self._show("textSetup")
                return
            if mode == "local":
                # Local first-run prepares original subtitles without a text service.
                self.bridge.command("textModelPolicy", {"mode": "off"})
            try:
                self._complete_setup()
            except OSError as exc:
                self._show(self.page, f"无法保存首次设置：{exc}")
                return
            self._show("home")
            self.directionAccepted.emit()

        try:
            self.bridge.start_task("processing-setup", verify, done)
        except (ValueError, RuntimeError) as exc:
            self.verifying = False
            self._show(self.page, str(exc))

    def _remember(self):
        path = self.bridge.mediaState.get("mediaPath", "")
        if path and path != self.last:
            self.last = path
            atomic_json(self.history, {"path": path})
            self.changed.emit()

    @Property("QVariantMap", notify=changed)
    def state(self):
        return dict(page=self.page, path=self.path, name=Path(self.path).name, last=Path(self.last).name,
                    position=self.bridge.store.load_position(self.last) if self.last and not self.bridge.is_closed else 0,
                    message=self.message, repair=self.repair, setupRequired=self.setup_required,
                    method=self.pending.get("mode", ""), verifying=self.verifying,
                    sourceUrl=self.demo_source.url if self.demo_source else "")

    @Property("QVariantList", notify=changed)
    def sources(self):
        language = self.bridge.settings_snapshot().target_language.split("-")[0]
        return [item for item in self.items if item["language"] == language]

    def _show(self, page, message="", repair=""):
        self.page, self.message, self.repair = page, message, repair
        self.changed.emit()

    @Slot(str, str)
    def direction(self, source, target):
        if self.page != "direction":
            return
        try:
            source, target = language_code(source), language_code(target)
            catalog = self.workspaces.catalog
            current = self.workspaces.workspace_id
            pair_key = f"{source}:{target}"
            previous = self.recent_directions.get(pair_key)
            candidates = sorted(catalog.summaries(), key=lambda item: (item["id"] != current, item["id"] != previous))
            match = next((item for item in candidates
                          if (item["source_language"], item["target_language"]) == (source, target)
                          and (Path(item["path"]) / "settings.json").is_file()), None)
            if match:
                ident = match["id"]
            else:
                name = f"{display_language(source)[:32]}学{display_language(target)[:32]}"
                candidate = name
                suffix = 2
                while (catalog.parent / candidate).exists():
                    candidate = f"{name} ({suffix})"
                    suffix += 1
                ident = catalog.create(candidate, source, target)
            self.recent_directions[pair_key] = ident
            if self.pending.get("mode") == "skip":
                self._complete_setup()
            else:
                if self.setup_required:
                    self.pending["workspace"] = ident
                self._save_marker()
            if ident != current:
                self.workspaces._ui_switch({"id": ident})
                return
            if self.setup_required:
                self._apply_method()
                self._show("speechSetup")
            else:
                self._show("home")
                self.directionAccepted.emit()
        except (ValueError, OSError, RuntimeError) as exc:
            self._show("direction", str(exc))

    @Slot()
    def browse(self):
        if self.setup_required or self.page in ("direction", "download", "working"):
            return
        filters = "音视频 (" + " ".join("*" + ext for ext in sorted(MEDIA_EXTENSIONS)) + ")"
        path, _ = QFileDialog.getOpenFileName(None, "选择学习视频", "", filters)
        if path:
            self.select(path)

    @Slot(str)
    def select(self, raw):
        if self.setup_required or self.page in ("direction", "working", "download"):
            return
        path = Path(QUrl(raw).toLocalFile() if raw.startswith("file:") else raw)
        if not path.is_file() or path.suffix.lower() not in MEDIA_EXTENSIONS:
            self._show("home", "请选择可读取的音视频文件。")
            return
        self.path = str(path.resolve())
        self.source_id = ""
        try:
            row = self._queue_entry(lookup_only=True)
            if row and row["status"] == "completed":
                self._open()
                return
            self._show("selected")
        except (OSError, ValueError) as exc:
            self._show("error", str(exc), "queue")

    @Slot()
    def home(self):
        if not self.setup_required and self.page not in ("direction", "working", "download"):
            self._show("home")

    @Slot()
    def refresh(self):
        self.bridge.save_position()
        self.changed.emit()

    @Slot()
    def catalog(self):
        if self.setup_required:
            return
        root = installation_root() / "demo-source"
        if (root / "catalog.json").is_file():
            try:
                if self.demo_source is None:
                    self.demo_source = DemoSource(root)
                    self.destroyed.connect(self.demo_source.close)
                with open_local_url(self.demo_source.url) as response:
                    self.items = json.load(response)
            except (OSError, ValueError, KeyError) as exc:
                self._show("error", "无法读取本地视频源：" + str(exc))
                return
        self._show("catalog")

    @Slot()
    def resume(self):
        if not self.setup_required and self.last:
            self.path = self.last
            self.audio_track = self._audio_track(self.bridge.settings_snapshot())
            self._open()

    def _open(self):
        try:
            self.bridge.open_media(self.path, study_track=self.audio_track)
            self._show("home")
            self.watch.emit()
        except (ValueError, OSError) as exc:
            self._show("home", str(exc))

    def _audio_track(self, settings):
        try:
            import av
        except ImportError:
            av = None
        try:
            if av is None:
                tags = [stream.get("tags", {}) for stream in probe_media(Path(self.path))["audio_tracks"]]
            else:
                with av.open(self.path, options={"protocol_whitelist": "file,crypto,data"}) as media:
                    tags = [dict(stream.metadata) for stream in media.streams.audio]
        except (OSError, ValueError, MediaError):
            return settings.audio_track
        aliases = {"yue": {"yue", "zh-yue", "zh-hk", "cantonese", "粤语", "粵語", "廣東話", "广东话"},
                   "en": {"en", "eng", "english", "英语", "英語"}}
        language = settings.target_language.split("-")[0]
        matches = aliases.get(language, {language})
        for index, track in enumerate(tags):
            code = track.get("language", "").casefold().replace("_", "-")
            title = track.get("title", "").casefold()
            if code in matches or code.split("-")[0] in matches or any(word in title for word in matches if len(word) > 2 or not word.isascii()):
                return index
        return settings.audio_track if settings.audio_track < len(tags) else 0

    def _queue_entry(self, lookup_only=False):
        settings = self.bridge.settings_snapshot()
        self.audio_track = self._audio_track(settings)
        settings.audio_track = self.audio_track
        path = Path(self.path)
        document = self.bridge.store.load_document(str(path), settings.language)
        subtitle = document.metadata.get("subtitle_path") if document else None
        subtitles = {canonical_path(path): subtitle} if subtitle and Path(subtitle).is_file() else None
        rows = self.bridge.queue.enqueue([path], settings, subtitles, include_existing=True, lookup_only=lookup_only)
        return rows[0] if rows else None

    @Slot()
    def prepare(self):
        if self.setup_required:
            return
        try:
            if "queue" in self.bridge.task_keys():
                raise ValueError("队列正在处理其他任务，完成后可继续。")
            if not Path(self.path).is_file():
                raise ValueError("找不到所选视频，请重新选择。")
            queue = self.bridge.queue
            self.bridge.add_paths([self.path])
            row = self._queue_entry()
            self.job = row["id"]
            if row["status"] == "completed":
                self._open()
                return
            if row["status"] in ("failed", "paused", "cleared"):
                queue.retry(self.job)
            self._show("working", "正在准备视频")
            ident = self.job
            def process(report, cancel):
                try:
                    queue.run(report, cancel, job_ids=[ident],
                              on_error=lambda exc: self.bridge.log.exception(exc, "queue_error"),
                              log_event=self.bridge.log.event)
                except Exception as exc:
                    self.bridge.log.exception(exc, "queue_error")
                    return str(exc)
                return ""
            self.bridge.start_task("queue", process, self._finished)
        except (ValueError, RuntimeError, OSError) as exc:
            self._show("error", str(exc), "queue")

    def _progress(self):
        if self.page != "working":
            return
        row = next((j for j in self.bridge.jobs if j["id"] == self.job), {})
        stage = {"transcript": "正在生成字幕", "review": "正在核对字幕", "teaching": "正在准备讲解",
                 "local": "正在整理字幕词句", "verification": "正在核对译文", "publish": "正在保存学习材料",
                 "planning": "正在检查可用字幕"}.get(row.get("stage_code"), row.get("stage_name") or "正在准备视频")
        if self.message != stage:
            self.message = stage
            self.changed.emit()

    def _finished(self, error):
        row = next((j for j in self.bridge.queue.snapshot() if j["id"] == self.job), {})
        if row.get("status") == "completed":
            self._open()
        else:
            detail = error or row.get("error_message") or row.get("error") or "准备已中断，可以继续。"
            code = row.get("stage_code")
            if "ffmpeg" in detail.casefold():
                repair = "runtime"
            elif code == "transcript" or any(word in detail for word in ("语音", "音轨", "转写", "ASR", "Whisper")):
                repair = "speech"
            elif any(word in detail for word in ("文本授权", "远程文本", "文本权限")):
                repair = "text"
            else:
                repair = "models"
            self._show("error", " · ".join(filter(None, [row.get("stage_name"), detail])), repair)

    @Slot(str)
    def download(self, ident):
        item = next((i for i in self.sources if i["id"] == ident), None)
        if not item or self.page not in ("catalog", "error"):
            return
        self.source_id = ident
        root = self.bridge.store.data_dir
        try:
            self.path = str(catalog_path(root, item))
            self._show("download", "正在获取片源与字幕")
            language = self.bridge.settings_snapshot().language

            def fetch(report, cancel):
                try:
                    path = fetch_catalog_video(root, item, cancel)
                    if self.demo_source and not cancel():
                        install_bundled_lesson(installation_root() / "demo-source", item, path,
                                               self.bridge.store, language)
                except Exception as exc:
                    self.bridge.log.exception(exc, "catalog_download")
                    return str(exc)
                return ""

            def complete(error):
                if error:
                    self._show("error", "视频下载失败：" + error,
                               "runtime" if "ffmpeg" in error.casefold() else "download")
                else:
                    self.source_id = ""
                    document = self.bridge.store.load_document(self.path, self.bridge.settings_snapshot().language)
                    if document and document.metadata.get("stage") == "ready":
                        self.audio_track = document.metadata.get("transcript_spec", {}).get("audio_track", 0)
                        self.bridge.add_paths([self.path])
                        self._open()
                        return
                    self.prepare()

            self.bridge.start_task("home-download", fetch, complete)
        except (ValueError, OSError) as exc:
            self._show("error", str(exc), "download")

    @Slot()
    def retry(self):
        if self.source_id:
            self.download(self.source_id)
        else:
            self.prepare()
