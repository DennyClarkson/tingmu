from copy import deepcopy

import pytest
from PySide6.QtCore import QMetaObject, QObject, QPointF, Qt
from PySide6.QtTest import QTest

from tingmu.app_actions import ActionContext, AppActionExecutor
from tingmu.models import AppSettings
from tingmu.providers import ModelRegistry
from tingmu.quick.main import create_qml_app
from tingmu.setup_chat import setup_intent
from tingmu.workspace_text import clone_active_text_roles


@pytest.mark.parametrize(("request_text", "expected"), [
    ("我要学英语了。", {"source": "zh-CN", "target": "en"}),
    ("我要用英语学日语", {"source": "en", "target": "ja"}),
    ("I want to learn English!", {"source": "zh-CN", "target": "en"}),
    ("我要用斯瓦希里语学冰岛语", {"source": "sw", "target": "is"}),
    ("用 pt-BR 学 x-example", {"source": "pt-BR", "target": "x-example"}),
    ("I want to study Finnish using Japanese.", {"source": "ja", "target": "fi"}),
    ("我要用冰岛语学冰岛语", {"source": "is", "target": "is"}),
    ("帮我创建一个叫「北欧剧」的工作区，用斯瓦希里语学冰岛语。",
     {"source": "sw", "target": "is", "create": True, "name": "北欧剧"}),
    ("我要学英语了，用本机 API 讲解", None), ("我不想学英语", None),
    ("“我要学英语了”怎么翻译？", None),
])
def test_only_explicit_study_intents_use_offline_route(request_text, expected):
    assert setup_intent(request_text, "zh-CN") == expected


def test_language_id_survives_windows_reserved_name_and_restart(tmp_path):
    from tingmu.notebooks import NotebookManager
    from tingmu.storage import Store
    from tingmu.workspaces import WorkspaceCatalog
    catalog = WorkspaceCatalog(tmp_path)
    ident = catalog.create("语言练习", "x-source", "con")
    store = Store(catalog.directory(ident))
    assert store.load_settings().target_language == "con"
    notebook = NotebookManager(store)
    state = notebook.sync("con")
    assert "~con" in state["path"] and state["language"] == "con"
    assert NotebookManager(store).snapshot("con")["revision"] == state["revision"]


def test_clone_selected_roles_and_protected_connection(tmp_path, monkeypatch):
    monkeypatch.setattr("tingmu.workspace_text.provider_status", lambda: {})
    a, b = tmp_path / "one", tmp_path / "two"
    src, dst = ModelRegistry(a), ModelRegistry(b)
    local = src.save_profile({"provider": "ollama", "model": "test"})
    remote = src.save_profile({"provider": "custom", "base_url": "https://text.example/v1", "model": "tutor",
                               "key_source": "none"})
    src.save_profile({"provider": "ollama", "model": "unrelated"})
    source = AppSettings(annotation_profile_id=local["id"], companion_profile_id=remote["id"])
    target = AppSettings(cloud_text_enabled=False)
    roles = clone_active_text_roles(a, b, source, target)
    assert [r["model"] for r in roles] == ["test"]
    assert not target.cloud_text_enabled and len(dst._read()) == 1
    # Ciphertext from a specific saved connection stays protected during transfer.
    saved = src._read()
    saved[remote["id"]].update(key_source="manual", _protected_key="protected-test-marker")
    src._write(saved)
    roles = clone_active_text_roles(a, b, source, target, allow_remote=True)
    assert "protected-test-marker" not in str(roles) and len(dst._read()) == 2
    assert dst._read()[target.companion_profile_id]["_protected_key"] == "protected-test-marker"
    assert target.cloud_text_enabled and target.review_provider == "local"


def test_real_consent_buttons_and_workspace_api_actions(qapp, qtbot, tmp_path, monkeypatch):
    _, bridge, engine = create_qml_app(tmp_path / "catalog")
    window, controller = engine._tingmu_window, bridge.workspaces
    context = ActionContext(bridge.activeSessionId, "", bridge._settings.language, workspace_id="default")
    tool = AppActionExecutor(bridge.store, bridge.notebook_manager, context, None, bridge._request_app_action)
    calls = []
    monkeypatch.setattr("tingmu.workspace_text.provider_status", lambda: {"openai": {"available": True, "endpoint_host": "text.example"}})
    monkeypatch.setattr("tingmu.quick.workspace_controller.model_inventory", lambda _: {"installed": bool(calls)})
    monkeypatch.setattr("tingmu.quick.workspace_controller.download_model", lambda model, *_: calls.append(model))

    def click(name):
        button = window.findChild(QObject, name)
        assert button and button.isVisible() and button.isEnabled()
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, point)

    try:
        target = tool("create_study_workspace", {"name": "English", "source": "zh-CN", "target": "en"})["workspace"]["id"]
        prefs = controller._settings_for(target)
        candidate = {"model": "small", "name": "Whisper small", "available": True, "installed": False,
                     "device": "cpu", "download_bytes": 486000000, "estimated_download_bytes": 486000000}
        controller._save_recommendation(target, {"recommendation": candidate, "candidates": [candidate]},
                                        controller._fingerprint(prefs))
        tool("propose_workspace_model", {"workspace_id": target, "model": "small"})
        chat_panel = window.findChild(QObject, "chatPanel")
        chat_input = chat_panel.findChild(QObject, "chatInput")
        chat_input.setProperty("text", "取消下载")
        assert QMetaObject.invokeMethod(chat_panel, "send")
        assert controller.proposal["status"] == "declined" and not calls
        tool("propose_workspace_model", {"workspace_id": target, "model": "small"})
        tool("reuse_workspace_text_models", {"workspace_id": target})
        modal = window.findChild(QObject, "agentModelDownload")
        text_modal = window.findChild(QObject, "workspaceTextConsent")
        qtbot.waitUntil(lambda: modal.property("opened"))
        assert not calls and not text_modal.property("opened")
        click("approveAgentDownload")
        qtbot.waitUntil(lambda: controller.proposal["status"] == "ready")
        qtbot.waitUntil(lambda: text_modal.property("opened"))
        qtbot.wait(100)
        assert calls == ["small"] and not controller._settings_for(target).cloud_text_enabled
        click("skipTextReuse")
        qtbot.waitUntil(lambda: controller.text_proposal["status"] == "declined")
        assert not controller._settings_for(target).cloud_text_enabled
        assert prefs.asr_model != controller._settings_for(target).asr_model

        tool("reuse_workspace_text_models", {"workspace_id": target})
        qtbot.waitUntil(lambda: text_modal.property("opened"))
        click("allowTextReuse")
        qtbot.waitUntil(lambda: controller.text_proposal["status"] == "ready")
        assert controller._settings_for(target).cloud_text_enabled
        opts = {"workspace_id": target, "provider": "ollama", "endpoint": "http://localhost:11434/v1",
                "model": "example", "role": "all"}
        assert tool("assign_local_text_model", opts)["roles"] == ["annotation", "review", "companion"]
        requested = {"workspace_id": target, "backend": "external", "endpoint": "https://audio.example/transcribe",
                     "model": "test"}
        assert tool("configure_speech_api", requested)["audio_permission_required"]
        before = deepcopy(controller._settings_for(target))
        with pytest.raises(ValueError):
            tool("configure_speech_api", {**requested, "backend": "local_api"})
        assert controller._settings_for(target) == before
        assert before.asr_key_env == "" and not before.allow_audio_upload
        assert not engine._tingmu_warnings
    finally:
        window.close()
        bridge.shutdown()
        engine.deleteLater()
        qtbot.wait(30)
