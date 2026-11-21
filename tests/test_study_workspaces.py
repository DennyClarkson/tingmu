import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest
import pytest

from tingmu.library import MediaLibrary
from tingmu.models import Cue, LanguagePair, StudyDocument
from tingmu.quick.main import create_qml_app
from tingmu.storage import Store
from tingmu.workspaces import DEFAULT_ID, WorkspaceCatalog


def test_media_import_move_and_reconnect(tmp_path):
    catalog = WorkspaceCatalog(tmp_path / "catalog")
    identity = catalog.create("葡语听力", "pt-BR", "pt-BR")
    directory = catalog.directory(identity)
    store = Store(directory)
    assert store.load_settings().language == LanguagePair("pt-BR", "pt-BR")
    library = MediaLibrary(store)
    sources = []
    for name in ("first", "second"):
        original = tmp_path / name / "01.mp4"
        original.parent.mkdir()
        original.write_bytes(name.encode())
        original.with_suffix(".pt-BR.srt").write_text(name)
        sources.append(original)
    imported = library.copy_files(sources, lambda _: None, lambda: False)
    assert len(set(imported)) == 2
    assert library.copy_files(sources, lambda _: None, lambda: False) == imported
    for original, copy in zip(sources, imported):
        assert copy.is_relative_to(directory / "media")
        assert copy.read_bytes() == original.read_bytes()
        assert copy.with_suffix(".pt-BR.srt").read_bytes() == original.with_suffix(".pt-BR.srt").read_bytes()
    library.add(imported + sources[:1])
    pair = store.load_settings().language
    document = StudyDocument(str(imported[0]), pair, [Cue("a", 0, 2, "texto")], metadata={"stage": "ready"})
    old_document = store.save_document(document)
    old_srt = old_document.with_suffix(".srt")
    assert old_srt.exists()
    old_document.with_suffix(".pending").touch()
    old_srt.write_text("incomplete")
    store.save_note(str(imported[0]), document.cues[0], "lembrar", pair)
    moved = directory.with_name("新位置")
    directory.rename(moved)
    with pytest.raises(ValueError, match="找不到工作区"):
        catalog.directory(identity)
    catalog.discover()
    assert catalog.directory(identity) == moved
    restored = Store(catalog.directory(identity))
    expected_path = moved / imported[0].relative_to(directory)
    assert str(expected_path) in MediaLibrary(restored).paths
    assert str(sources[0]) in MediaLibrary(restored).paths
    assert restored.entries()[0]["media_path"] == str(expected_path)
    assert restored.load_document(str(expected_path), pair).cues[0].text == "texto"
    _, subtitle = restored.material_paths(str(expected_path), pair)
    assert "texto" in subtitle.read_text("utf-8-sig")
    assert not (moved / old_srt.relative_to(directory)).exists()


def test_qml_switch_reopens_isolated_sessions_and_legacy_data(qapp, qtbot, tmp_path):
    def click(window, item):
        point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)
        qtbot.wait(50)

    root = tmp_path / "data"
    legacy = Store(root)
    movie = tmp_path / "legacy.mp4"
    movie.touch()
    MediaLibrary(legacy).add([movie])
    settings = legacy.load_settings()
    settings.teaching_preferences = "原工作区偏好"
    legacy.save_settings(settings)
    app, first, engine = create_qml_app(root)
    try:
        first_window = engine._tingmu_window
        qtbot.wait(70)
        assert not first_window.findChild(QObject, "workspaceSetup").property("opened")
        assert first.workspaces.info["id"] == DEFAULT_ID
        assert first.library[0]["path"] == str(movie)
        old_session = first.activeSessionId
        first.queue.enqueue([movie], first._settings)
        first.store.save_position(str(movie), 800)
        first.store.save_note(str(movie), Cue("old", 1, 2, "原文"), "旧笔记", first._settings.language)
        first.command("saveModel", {"provider": "ollama", "model": "first-workspace-model"})
        private_model = first.modelProfiles[0]
        first.command("selectModel", {"role": "companion", "id": private_model["id"]})
        first.saveLayout({"preferences": {"privateDraft": "旧草稿"}})
        click(first_window, first_window.findChild(QObject, "workspacePicker"))
        assert first_window.findChild(QObject, "workspacePickerPopup").property("opened")
        click(first_window, first_window.findChild(QObject, "workspacePickerCreate"))
        assert first_window.findChild(QObject, "workspaceSetup").property("creating")
        first_window.findChild(QObject, "workspaceName").setProperty("text", "第二空间")
        first_window.findChild(QObject, "knownLanguage").setProperty("code", "ja")
        first_window.findChild(QObject, "learningLanguage").setProperty("code", "ja")
        click(first_window, first_window.findChild(QObject, "workspaceContinue"))
        second, second_engine = app._tingmu_quick
        qtbot.waitUntil(lambda: second_engine.rootObjects()[0].findChild(QObject, "workspaceSetup").property("opened"))
        assert not second.library and not second.jobs
        assert not second.store.entries() and second.store.load_position(str(movie)) == 0
        assert second.activeSessionId != old_session and second.layout == {}
        assert second.settings["teaching_preferences"] == ""
        assert second.modelProfiles == [] and second.settings["companion_profile_id"] == ""
        assert second.settings["source_language"] == second.settings["target_language"] == "ja"
        assert not second.settings["allow_audio_upload"]
        second.setting("asr_backend", "external")
        second.setting("teaching_preferences", "第二空间")
        assert second.workspaces.action("languages", {"source": "en", "target": "pt-BR"})
        assert second.settings["target_language"] == "pt-BR"
        window = second_engine.rootObjects()[0]
        assert window.findChild(QObject, "workspaceSetup").property("section") == "models"
        click(window, window.findChild(QObject, "workspaceTextTab"))
        assert window.findChild(QObject, "workspaceSetup").property("section") == "text"
        assert not second_engine._tingmu_warnings
        click(window, window.findChild(QObject, "workspaceContinue"))
        assert not window.findChild(QObject, "workspaceSetup").property("opened")
        click(window, window.findChild(QObject, "workspacePicker"))
        assert [row["languages"] for row in second.workspaces.info["items"]] == ["普通话 → 粤语", "英语 → 巴西葡萄牙语"]
        rows = window.findChild(QObject, "workspacePickerList").property("contentItem").childItems()
        click(window, next(row for row in rows if row.objectName() == "workspaceRow_default"))
        restored, last_engine = app._tingmu_quick
        qtbot.wait(70)
        assert restored.store.data_dir == root
        assert restored.library[0]["path"] == str(movie)
        assert restored.activeSessionId == old_session
        assert restored.settings["asr_backend"] == "local"
        assert restored.settings["teaching_preferences"] == "原工作区偏好"
        assert restored.settings["companion_profile_id"] == private_model["id"]
        assert restored.layout["preferences"]["privateDraft"] == "旧草稿"
        assert len(restored.jobs) == 1 and restored.store.entries()[0]["note"] == "旧笔记"
        assert restored.store.load_position(str(movie)) == 800
        assert not last_engine._tingmu_warnings
        assert not last_engine._tingmu_window.findChild(QObject, "workspaceSetup").property("opened")
        assert WorkspaceCatalog(root).active_dir == root
        final_window = last_engine._tingmu_window
        click(final_window, final_window.findChild(QObject, "workspacePicker"))
        rows = final_window.findChild(QObject, "workspacePickerList").property("contentItem").childItems()
        second_id = next(row["id"] for row in restored.workspaces.info["items"] if row["id"] != DEFAULT_ID)
        click(final_window, next(row for row in rows if row.objectName() == "workspaceRow_" + second_id))
        selected, selected_engine = app._tingmu_quick
        assert selected.settings["target_language"] == "pt-BR"
        assert not selected_engine._tingmu_window.findChild(QObject, "workspaceSetup").property("opened")
        selected_engine._tingmu_window.close()
        selected.shutdown()
        selected_engine.deleteLater()
        qtbot.wait(25)
        _, reopened, reopened_engine = create_qml_app(root)
        qtbot.wait(60)
        assert reopened.workspaces.info["id"] == second_id
        assert reopened.settings["asr_backend"] == "external"
        assert not reopened_engine._tingmu_window.findChild(QObject, "workspaceSetup").property("opened")
    finally:
        current_bridge, current_engine = app._tingmu_quick
        current_bridge.shutdown()
        for child in app.topLevelWindows():
            child.hide()
        current_engine.deleteLater()
        qtbot.wait(30)


def test_bad_workspace_and_language_names_never_create_folders(tmp_path):
    catalog = WorkspaceCatalog(tmp_path / "catalog")
    for name in ("..", "CON", "bad/name", "bad\\name"):
        with pytest.raises(ValueError):
            catalog.create(name, "yue", "en")
    with pytest.raises(ValueError):
        catalog.create("ok", "en", "../elsewhere")
    with pytest.raises(ValueError, match="workspaces"):
        catalog.create("外部", "en", "ja", tmp_path)
    with pytest.raises(ValueError, match="workspaces"):
        catalog.register(tmp_path)
    assert catalog.active_dir == catalog.root
    assert not (catalog.root / "workspaces").exists()


def test_workspace_directory_link_cannot_redirect_new_workspace(tmp_path):
    catalog = WorkspaceCatalog(tmp_path / "catalog")
    outside = tmp_path / "other"
    outside.mkdir()
    link = catalog.root / "workspaces"
    if os.name == "nt":
        from _winapi import CreateJunction
        CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="目录"):
        catalog.create("案外", "en", "ja")
    assert list(outside.iterdir()) == []


def test_nested_notebook_junction_cannot_write_outside_active_workspace(tmp_path):
    catalog = WorkspaceCatalog(tmp_path / "catalog")
    workspace_id = catalog.create("英語", "yue", "en")
    directory = catalog.directory(workspace_id)
    outside = tmp_path / "private"
    outside.mkdir()
    link = directory / "notebooks" / "en"
    if os.name == "nt":
        from _winapi import CreateJunction
        CreateJunction(str(outside), str(link))
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="链接"):
        catalog.select(workspace_id)
    assert catalog.active_id == DEFAULT_ID
    assert list(outside.iterdir()) == []


def _simulate_shared_download(workspace, cache, start):
    from tingmu import workspace_models as models

    def fetch(model, repo, progress, cancelled):
        (cache / f"{repo.split('/')[-1]}-{os.getpid()}.fetch").touch()
        time.sleep(.12)
        directory = cache / "qwen-models" / ("models--" + repo.replace("/", "--"))
        revision = "1" * 40
        snapshot = directory / "snapshots" / revision
        snapshot.mkdir(parents=True, exist_ok=True)
        for file in ("config.json", "preprocessor_config.json", "tokenizer_config.json", "tokenizer.json", "model.safetensors"):
            (snapshot / file).write_bytes(b"test")
        (directory / "refs").mkdir(exist_ok=True)
        (directory / "refs/main").write_text(revision)

    model = Store(Path(workspace)).load_settings().asr_model
    with patch.object(models, "cache_root", return_value=cache), patch.object(models, "_qwen_python"), patch.object(models, "_download_repo", fetch):
        deadline = time.monotonic() + 20
        while not start.exists():
            assert time.monotonic() < deadline
            time.sleep(.05)
        models.download_model(model, lambda _: None, lambda: False)
        assert models.model_status(model) == "已检测到本地模型文件"


def test_processes_share_weights_and_qwen_aligner_across_workspaces(tmp_path):
    catalog = WorkspaceCatalog(tmp_path / "data")
    cache = tmp_path / ".cache"
    cache.mkdir()
    start = tmp_path / "start"
    processes = []
    for index, model in enumerate(("qwen3-asr-1.7b", "qwen3-asr-1.7b", "qwen3-asr-0.6b")):
        workspace = catalog.directory(catalog.create(str(index), "en", "yue"))
        store = Store(workspace)
        settings = store.load_settings()
        settings.asr_model = model
        store.save_settings(settings)
        process = subprocess.Popen([sys.executable, __file__, str(workspace), str(cache), str(start)])
        processes.append(process)
    start.touch()
    try:
        for process in processes:
            assert process.wait(timeout=25) == 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()
    assert len(list(cache.glob("Qwen3-ASR-1.7B-*.fetch"))) == 1
    assert len(list(cache.glob("Qwen3-ASR-0.6B-*.fetch"))) == 1
    assert len(list(cache.glob("Qwen3-ForcedAligner-0.6B-*.fetch"))) == 1
    assert len(list(cache.rglob("model.safetensors"))) == 3
    assert not list(catalog.root.rglob("*.safetensors"))


if __name__ == "__main__":
    _simulate_shared_download(*(Path(arg) for arg in sys.argv[1:]))
