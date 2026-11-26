"""An app-local catalog of independent study folders, including the original data store."""

from dataclasses import replace
import json
import os
from pathlib import Path, PureWindowsPath
import re
import tempfile
import uuid

from .models import AppSettings
from .global_models import workspace_preferences
from .learning_notes import language_name
from .portable_paths import _migration_lock
from .storage import atomic_json

MARKER = "tingmu-workspace.json"
DEFAULT_ID = "default"


def language_code(value):
    from .locale_catalog import resolve_language
    code = resolve_language(value) or str(value).strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", code):
        raise ValueError("请输入有效的语言/方言代码，如 zh-CN、yue 或 en")
    return code


def ensure_folders(path):
    for name in ("media", "documents", "notebooks", "exports"):
        folder = path / name
        if not folder.resolve().is_relative_to(path):
            raise ValueError("工作区内的学习资料文件夹必须位于当前工作区")
        folder.mkdir(exist_ok=True)


def _check_data_links(path, *, legacy):
    """Check persisted output paths before SQLite, notebooks or loggers can open them."""
    pending = [path]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                item = Path(entry.path)
                if legacy and item == path / "workspaces":
                    continue  # Other stores are validated independently when opened.
                if entry.is_symlink() or entry.is_junction():
                    resolved = item.resolve()
                    if not resolved.is_relative_to(path) or (legacy and resolved.is_relative_to(path / "workspaces")):
                        raise ValueError("学习资料包含指向当前工作区外的链接，请检查工作区文件夹")
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(item)


def pair_label(source, target):
    return " → ".join("普通话" if code == "zh-CN" else language_name(code) for code in (source, target))


class WorkspaceCatalog:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "workspaces.json"

    def _read(self):
        if not self.path.exists():
            return {"version": 1, "active": DEFAULT_ID, "items": [
                {"id": DEFAULT_ID, "name": "默认工作区", "path": "."}]}
        data = json.loads(self.path.read_text("utf-8"))
        if data.get("version") != 1 or not isinstance(data.get("items"), list):
            raise ValueError("工作区索引格式无效")
        return data

    @property
    def items(self):
        return [{**item, "path": str(self.root / item["path"])}
                for item in self._read()["items"]]

    @property
    def parent(self):
        path = self.root / "workspaces"
        if path.resolve() != path:
            raise ValueError("应用工作区目录不能指向其他位置")
        return path

    def _managed(self, path):
        if path.parent != self.parent or path.resolve() != path:
            raise ValueError("学习工作区必须直接存放在本应用的 workspaces 文件夹内")
        return path

    @property
    def active_id(self):
        return self._read()["active"]

    def summaries(self):
        rows = self.items
        for row in rows:
            try:
                path = self.root if row["id"] == DEFAULT_ID else self._managed(Path(row["path"]))
                prefs = json.loads((path / "settings.json").read_text("utf-8"))
            except (OSError, ValueError):
                prefs = {}
            if not isinstance(prefs, dict):
                prefs = {}
            source = str(prefs.get("source_language", "zh-CN"))
            target = str(prefs.get("target_language", "yue"))
            row["languages"] = pair_label(source, target)
        return rows

    @property
    def active_dir(self):
        return self.directory(self.active_id)

    def directory(self, workspace_id):
        entry = next((row for row in self.items if row["id"] == workspace_id), None)
        if entry is None:
            raise ValueError("工作区不存在")
        path = Path(entry["path"])
        if workspace_id == DEFAULT_ID:
            _check_data_links(self.root, legacy=True)
            return self.root
        self._managed(path)
        if not (path / MARKER).is_file():
            raise ValueError("找不到工作区。请将文件夹放回本应用的 workspaces 目录，并刷新工作区列表")
        if self._marker(path)["id"] != workspace_id:
            raise ValueError("此文件夹属于另一个工作区")
        _check_data_links(path, legacy=False)
        return path

    def select(self, workspace_id):
        self.directory(workspace_id)
        with _migration_lock(self.root):
            data = self._read()
            data["active"] = workspace_id
            atomic_json(self.path, data)

    def _marker(self, path):
        try:
            data = json.loads((path / MARKER).read_text("utf-8"))
            if not re.fullmatch("[0-9a-f]{32}", data["id"]) or not isinstance(data["name"], str):
                raise ValueError
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise ValueError("请选择包含 tingmu-workspace.json 的听幕工作区文件夹") from error
        return data

    def register(self, directory):
        path = Path(directory).absolute()
        if path == self.root:
            return DEFAULT_ID
        item = self._marker(self._managed(path))
        item = {"id": item["id"], "name": item["name"],
                "path": str(path.relative_to(self.root))}
        with _migration_lock(self.root):
            data = self._read()
            for row in data["items"]:
                if row["id"] == item["id"]:
                    # Reconnect a moved workspace without confusing a live copy for the original.
                    original = (self.root / row["path"]).resolve()
                    if original != path and (original / MARKER).is_file():
                        raise ValueError("此工作区的另一份副本仍在记录的位置，请选择原文件夹")
                    row.update(item)
                    break
            else:
                data["items"].append(item)
            atomic_json(self.path, data)
        return item["id"]

    def discover(self):
        if self.parent.is_dir():
            for path in self.parent.iterdir():
                if path.resolve() == path and (path / MARKER).is_file():
                    self.register(path)

    def create(self, name, source, target, parent=None, *, initial_settings=None, configure=None):
        name = str(name).strip()
        if (not name or len(name) > 80 or name in (".", "..") or name.endswith(".")
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or PureWindowsPath(name).is_reserved()):
            raise ValueError("请输入可用作文件夹名称的工作区名称（最多 80 字）")
        settings = replace(initial_settings or AppSettings(cloud_text_enabled=False),
                           source_language=language_code(source), target_language=language_code(target))
        base = self.parent
        if parent and Path(parent).absolute() != base:
            raise ValueError("学习工作区必须直接存放在本应用的 workspaces 文件夹内")
        base.mkdir(parents=True, exist_ok=True)
        path = base / name
        workspace_id = uuid.uuid4().hex
        with _migration_lock(self.root):
            if path.exists():
                raise ValueError("同名文件夹已经存在，请更换名称或打开已有工作区")
            with tempfile.TemporaryDirectory(dir=base, prefix=".tingmu-create-") as tmp:
                staging = Path(tmp) / name
                staging.mkdir()
                ensure_folders(staging)
                if configure:
                    configure(staging, settings)
                atomic_json(staging / "settings.json", workspace_preferences(settings))
                atomic_json(staging / MARKER, {"id": workspace_id, "name": name})
                staging.rename(path)
        return self.register(path)
