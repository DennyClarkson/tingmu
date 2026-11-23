from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

import pytest

from tingmu.models import AppSettings
from tingmu.pipeline import prepare_media
from tingmu.providers import ModelRegistry, ProviderError, validate_base_url
from test_quick_bridge import bridge as bridge
from test_qml_v4 import find, invoke, quick as quick


@pytest.fixture
def release_text_response():
    event = threading.Event()
    event.set()
    yield event
    event.set()


@pytest.fixture
def text_server(release_text_response):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def respond(self, data, media_type="application/json"):
            self.send_response(200)
            self.send_header("Content-Type", media_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            calls.append((self.path, "discover", self.headers.get("Authorization")))
            self.respond(b'{"data":[{"id":"local-test"}]}')

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if body.get("stream"):
                calls.append((self.path, "companion", self.headers.get("Authorization")))
                event = {"model": "local-test", "choices": [{"delta": {"content": "本机回复"}, "finish_reason": "stop"}]}
                self.respond(("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode(), "text/event-stream")
                return
            data = json.loads(body["messages"][-1]["content"])
            role = "review" if "document_context" in data else "annotation"
            if "segments" in data["cues"][0] and "translation" in data["cues"][0]:
                role = "translation_review"
            calls.append((self.path, role, self.headers.get("Authorization")))
            if not release_text_response.wait(timeout=15):
                self.send_error(503)
                return
            rows = []
            for cue in data["cues"]:
                text = cue["text"]
                if role == "review":
                    row = {**cue, "uncertain": False, "reason": "", "segmented_texts": [text]}
                elif role == "translation_review":
                    row = {"id": cue["id"], "translation": "经复核的译文", "segments": []}
                else:
                    row = {**cue, "translation": "测试译文", "explanation": "测试讲解", "uncertain": False,
                           "tokens": [{"text": text, "reading": "", "meaning": "释义", "learn": False}]}
                rows.append(row)
            answer = json.dumps({"cues": rows})
            self.respond(json.dumps({"model": "local-test", "choices": [{"finish_reason": "stop",
                "message": {"role": "assistant", "content": answer}}]}).encode())

        def log_message(self, *unused):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_loopback_roles_survive_restart_with_remote_text_off(bridge, text_server, tmp_path, monkeypatch, qtbot):
    url, calls = text_server
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("LOCAL_TEST_BEARER", "synthetic-loopback-key")
    bridge._feature_command("textModelPolicy", {"mode": "local"})
    profile = bridge.model_registry.save_profile({"provider": "lmstudio", "label": "本机测试", "base_url": url,
        "key_source": "environment", "env_var": "LOCAL_TEST_BEARER"})
    assert bridge.model_registry.discover_models(profile["id"]) == ["local-test"]
    bridge.model_registry.save_profile({"id": profile["id"], "model": "local-test"})
    bridge._refresh_models()
    for role in ("review", "annotation", "companion"):
        bridge._feature_command("selectModel", {"role": role, "id": profile["id"]})
    settings = bridge.store.load_settings()
    assert settings.text_models_enabled and not settings.cloud_text_enabled
    persisted = ModelRegistry(bridge.store.data_dir)
    assert all(persisted.text_role(settings, role)[1]["model"] == "local-test"
               for role in ("review", "annotation", "companion"))
    media = tmp_path / "test.mkv"
    media.touch()
    sidecar = media.with_suffix(".srt")
    sidecar.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    result = prepare_media(media, settings, bridge.store, subtitle_path=sidecar)
    assert result.cues[0].explanation == "测试讲解"
    assert result.cues[0].translation == "经复核的译文"
    bridge._v4_send_chat("请解释这个词。")
    qtbot.waitUntil(lambda: not bridge.state["chatBusy"])
    assert bridge.messages[-1]["content"] == "本机回复"
    assert [c[1] for c in calls] == ["discover", "review", "annotation", "translation_review", "companion"]
    assert all(c[2] == "Bearer synthetic-loopback-key" for c in calls)
    assert not any("synthetic-loopback-key" in p.read_text(encoding="utf-8")
                   for p in bridge.store.data_dir.rglob("*.json"))
    # Disabling text models suppresses every configured role, including local ones.
    bridge._feature_command("textModelPolicy", {"mode": "off"})
    assert all(bridge.model_registry.text_role(bridge._settings, role) == (False, None)
               for role in ("review", "annotation", "companion"))
    with pytest.raises(ValueError, match="停用"):
        bridge._v4_send_chat("hello")
    assert len(calls) == 5


def test_preparation_queue_shows_simultaneous_model_requests(quick, qtbot, tmp_path, text_server, release_text_response):
    from tingmu.media import save_srt
    from tingmu.models import Cue

    _, bridge, _, window = quick
    url, calls = text_server
    bridge.set_languages("zh-CN", "en")
    bridge._feature_command("textModelPolicy", {"mode": "local"})
    profile = bridge.model_registry.save_profile({"provider": "lmstudio", "label": "本机测试",
                                                  "model": "local-test", "base_url": url, "key_source": "none"})
    for role in ("annotation", "review"):
        bridge._feature_command("selectModel", {"role": role, "id": profile["id"]})
    bridge.setting("llm_max_concurrency", 2)
    movie = tmp_path / "dialogue.mkv"
    movie.touch()
    save_srt([Cue(str(n), n, n + 1, "Hello.") for n in range(84)], movie.with_suffix(".srt"))
    invoke(window, "changeView", "prepare")
    release_text_response.clear()
    try:
        bridge._enqueue([str(movie)])
        bridge.command("startQueue", {})
        qtbot.waitUntil(lambda: len(calls) >= 2, timeout=10000)
        # Repeater delegates are visual children of the queue layout.
        def waiting_bars():
            todo = [window.contentItem()]
            bars = []
            while todo:
                item = todo.pop()
                if item.objectName() == "queueBatchProgressBar" and item.isVisible():
                    if item.property("indeterminate"):
                        bars.append(item)
                todo.extend(item.childItems())
            return bars
        qtbot.waitUntil(lambda: len(waiting_bars()) >= 2, timeout=5000)
        assert len({bar.property("batch")["id"] for bar in waiting_bars()}) == 2
    finally:
        release_text_response.set()
    qtbot.waitUntil(lambda: not bridge.state["queueRunning"], timeout=20000)
    assert bridge.jobs[0]["status"] == "completed", bridge.jobs[0]
    doc = bridge.store.load_document(str(movie), bridge._settings.language)
    assert len(doc.cues) == 84
    assert all(cue.translation == "经复核的译文" for cue in doc.cues)


def test_loopback_authentication_and_remote_role_boundaries(tmp_path):
    registry = ModelRegistry(tmp_path)
    settings = AppSettings(cloud_text_enabled=False)
    remote = registry.save_profile({"provider": "custom", "base_url": "https://example.invalid/v1", "model": "one"})
    settings.annotation_profile_id = remote["id"]
    assert registry.text_role(settings, "annotation") == (False, None)
    assert registry.text_role(settings, "review") == (False, None)
    for address in ("http://localhost:1234/v1", "http://[::1]:8000/v1"):
        assert validate_base_url(address, key_source="manual", local_connection=True) == address
    for address in ("http://10.0.0.1:1234/v1", "http://localhost.example.com/v1"):
        with pytest.raises(ProviderError):
            validate_base_url(address, key_source="environment", local_connection=True)
    with pytest.raises(ProviderError):
        registry.save_profile({"provider": "lmstudio", "model": "one", "base_url": "https://example.invalid/v1"})


def test_model_manager_local_policy_and_presets_are_editable(quick):
    from PySide6.QtCore import QMetaObject, Qt, Q_ARG
    _, bridge, engine, window = quick
    manager = find(window, "modelManagerWindow")
    invoke(manager, "open")
    policy = find(manager, "textModelPolicy")
    assert policy.property("currentIndex") == 2
    assert QMetaObject.invokeMethod(policy, "activated", Qt.ConnectionType.DirectConnection, Q_ARG("int", 1))
    assert not bridge.settings["cloud_text_enabled"]
    assert bridge.settings["text_models_enabled"] and policy.property("currentText") == "仅本机 API"
    for provider in ("ollama", "lmstudio", "local_compatible"):
        preset = next(p for p in bridge.modelPresets if p["provider"] == provider)
        invoke(manager, "populate", preset)
        assert find(manager, "modelBaseUrl").property("text") == preset["base_url"]
        assert find(manager, "modelKeySource").property("currentIndex") == 2
    find(manager, "modelName").setProperty("text", "my-loaded-model")
    invoke(find(manager, "saveModelButton"), "clicked")
    assert bridge.modelProfiles[-1]["model"] == "my-loaded-model" and bridge.modelProfiles[-1]["is_loopback"]
    assert QMetaObject.invokeMethod(policy, "activated", Qt.ConnectionType.DirectConnection, Q_ARG("int", 0))
    assert not bridge.settings["text_models_enabled"]
    assert policy.property("currentText") == "停用文本模型"
    assert not engine._tingmu_warnings
