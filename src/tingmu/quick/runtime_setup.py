"""Human-controlled setup for the optional components of a clean portable player."""

import json

from PySide6.QtCore import QObject, Property, Signal, Slot

from tingmu import runtime
from tingmu.portable_paths import _migration_lock
from tingmu.storage import atomic_json
from tingmu.task_progress import TransferMeter
from .localization import ui_text


KINDS = ("ffmpeg", "local-asr", "whisper-cuda", "qwen")
MISSING = {
    "local-asr": "请先在「运行环境与依赖管理」安装或选择本地 Whisper 引擎，再下载模型。",
    "whisper-cuda": "请在「运行环境与依赖管理」配置 Whisper CUDA，或将识别设备改为 cpu 或 auto。",
    "qwen": "请先在「运行环境与依赖管理」安装或选择 Qwen 识别环境，再下载模型。",
}


def _required(model, device):
    if model.lower().removeprefix("qwen/").startswith("qwen3-asr"):
        return ("qwen",)
    return ("local-asr", "whisper-cuda") if device == "cuda" else ("local-asr",)


class RuntimeSetup(QObject):
    changed = Signal()
    progressChanged = Signal()
    detectionFinished = Signal()
    _transfer = Signal(object)
    _detected = Signal(str, object)

    def __init__(self, bridge, data_dir):
        super().__init__(bridge)
        self.bridge = bridge
        self.root = runtime.portable_root() if runtime.is_clean_install() else None
        self.data_dir = runtime.confined_path(self.root, data_dir or "data") if self.root else None
        self._first = bool(self.root and self._read().get("status") not in ("completed", "skipped"))
        self.phase = ""
        self.message = ""
        self.results = {}
        self.progress = {}
        self._transfer.connect(self._show_transfer)
        self._detected.connect(self._show_component)

    def _file(self, name):
        directory = runtime.confined_path(self.root, self.data_dir)
        path = directory / name
        if path.is_symlink() or path.is_junction():
            raise ValueError("组件配置文件不能使用链接。")
        return runtime.confined_path(directory, name)

    def _read(self):
        try:
            value = json.loads(self._file("runtime-setup.json").read_text("utf-8"))
            return value if isinstance(value, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError, UnicodeError):
            return {}

    def _save(self, *, status=None, path=None):
        lock = self._file(".runtime-setup.lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        with _migration_lock(lock.parent, lock.name):
            state = self._read()
            if path:
                paths = state.get("paths")
                state["paths"] = {**(paths if isinstance(paths, dict) else {}), path[0]: path[1]}
            if status:
                state["status"] = status
            atomic_json(self._file("runtime-setup.json"), state)
        self._first = state.get("status") not in ("completed", "skipped")

    @Property(bool, constant=True)
    def available(self):
        return self.root is not None

    @Property(bool, notify=changed)
    def firstRun(self):
        return self._first

    @Property("QVariantMap", notify=changed)
    def state(self):
        return {"phase": self.phase, "message": self.message, "components": self.results,
                "playback": self.bridge.player.isAvailable()}

    @Property("QVariantMap", notify=progressChanged)
    def transfer(self):
        return self.progress

    @Slot(object)
    def _show_transfer(self, sample):
        self.progress = {**sample, "kind": sample["component"], "done": sample["completed"]}
        if self.phase == "cancelling":
            self.progress.update(stage="cancelling", rate=0.0, eta=None)
        self.progressChanged.emit()

    @Slot(str, object)
    def _show_component(self, kind, row):
        self.results[kind] = row
        self.changed.emit()

    def _start(self, phase, work, complete):
        if (not self.root and phase != "detect") or self.phase:
            return
        self.phase, self.message = phase, ""
        self.changed.emit()

        def run(_, cancelled):
            try:
                return work(cancelled)
            except InterruptedError:
                return {}, "已取消安装。可重新选择组件后继续。"
            except Exception as exc:
                return {}, str(exc)

        def finish(result):
            self.phase = ""
            rows, self.message = result
            self.results.update(rows)
            try:
                complete(rows)
            except (OSError, ValueError) as exc:
                self.message = str(exc)
            self.changed.emit()
            self.detectionFinished.emit()

        try:
            self.bridge.start_task("runtime-setup", run, finish)
        except (ValueError, RuntimeError) as exc:
            self.phase, self.message = "", str(exc)
            self.changed.emit()

    def _probe(self, kind, configured=None, *, cancelled=None):
        from tingmu.runtime_deps import probe
        from tingmu.runtime_install import download_bytes

        row = dict(probe(self.root or runtime.installation_root(), kind, configured, cancelled=cancelled))
        row["bytes"] = float(download_bytes(kind))
        return row

    @Slot()
    def detect(self):
        def work(cancelled):
            rows = {}
            paths = self._read().get("paths") if self.root else {}
            for kind in KINDS:
                if cancelled():
                    break
                selected = paths.get(kind) if isinstance(paths, dict) else None
                rows[kind] = self._probe(kind, selected, cancelled=cancelled)
                self._detected.emit(kind, rows[kind])
            return rows, ""

        self._start("detect", work, lambda rows: None)

    @Slot(str)
    def choosePath(self, kind):
        if not self.available or self.phase or kind not in KINDS:
            return
        if kind == "qwen":
            path, _ = self.bridge.native_dialogs.getOpenFileName(
                None, ui_text("选择 Qwen 环境的 Python 解释器"), "", "Python (python.exe)")
        else:
            title = {"ffmpeg": "选择同时包含 ffmpeg 和 ffprobe 的文件夹",
                     "local-asr": "选择 Whisper 引擎的 site-packages 文件夹",
                     "whisper-cuda": "选择 CUDA 动态库文件夹"}[kind]
            path = self.bridge.native_dialogs.getExistingDirectory(None, ui_text(title))
        if not path:
            return

        def work(cancelled):
            row = self._probe(kind, path, cancelled=cancelled)
            if row["ready"] and row["source"] == "configured":
                return {kind: row}, ""
            if row["ready"] and row["source"] == "portable":
                return {kind: row}, "已使用便携包中的组件。"
            return {}, "所选位置未通过组件检测。请检查路径和版本后重试。"

        def remember(rows):
            if kind in rows and rows[kind]["source"] == "configured":
                self._save(path=(kind, str(path)))
                self.detect()

        self._start("verify", work, remember)

    @Slot("QVariantList")
    def installSelected(self, selected):
        if self.phase or not self.available:
            return
        kinds = [kind for kind in KINDS if kind in selected]
        if (not kinds or any(kind not in KINDS for kind in selected)
                or any(kind not in self.results or self.results[kind]["ready"] for kind in kinds)
                or ("whisper-cuda" in kinds and not self.results["whisper-cuda"].get("nvidia_present"))):
            return
        self._show_transfer({"component": kinds[0], "completed": 0, "total": 0, "stage": "checking"})

        def work(cancelled):
            from tingmu.runtime_install import install

            rows = {}
            for kind in kinds:
                meter = TransferMeter(kind, lambda event: self._transfer.emit(event.details))
                try:
                    if cancelled():
                        raise InterruptedError
                    meter.stage("checking")
                    rows[kind] = self._probe(kind, cancelled=cancelled)
                    if rows[kind]["ready"]:
                        continue
                    if kind == "whisper-cuda" and not rows[kind].get("nvidia_present"):
                        return rows, "未检测到 NVIDIA 显卡，无需安装。"
                    if cancelled():
                        raise InterruptedError
                    install(self.root, kind, meter, cancelled)
                    meter.stage("testing")
                    rows[kind] = self._probe(kind, cancelled=cancelled)
                    if not rows[kind]["ready"]:
                        return rows, "已安装的组件未通过检测。请检查组件路径后重试。"
                    self._detected.emit(kind, rows[kind])
                except InterruptedError:
                    return rows, "已取消安装。可重新选择组件后继续。"
                except Exception as exc:
                    return rows, str(exc)
            return rows, ""

        def mark_completed(rows):
            if not self.message:
                self._save(status="completed")
                self.message = "所选组件已安装并通过检测。"

        self._start("install", work, mark_completed)

    @Slot()
    def cancel(self):
        if self.phase == "install":
            self.phase = "cancelling"
            self._show_transfer(self.progress)
            self.bridge.cancel_task("runtime-setup")
            self.changed.emit()

    @Slot(bool, result=bool)
    def dismiss(self, skip):
        if not self.root or self.phase in ("install", "cancelling", "verify"):
            return False
        try:
            if self._first:
                self._save(status="skipped" if skip else "completed")
            if self.phase == "detect":
                self.bridge.cancel_task("runtime-setup")
            self.changed.emit()
            return True
        except (OSError, ValueError) as exc:
            self.message = str(exc)
            self.changed.emit()
            return False

    def model_message(self, model, device):
        if self.root is None:
            return ""
        from tingmu.runtime_deps import last_probe

        for kind in _required(model, device):
            row = last_probe(self.root, kind)
            if row is None:
                if self.results.get(kind, {}).get("ready") is False:
                    return MISSING[kind]
                return "请在「运行环境与依赖管理」检测识别环境，再下载模型。"
            if not row["ready"]:
                return MISSING[kind]
        return ""

    def check_before_transfer(self, model, device):
        if self.root is not None:
            from tingmu.runtime_deps import cached_probe
            for kind in _required(model, device):
                if not cached_probe(self.root, kind)["ready"]:
                    raise ValueError(MISSING[kind])
