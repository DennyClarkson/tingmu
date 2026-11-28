import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPointF, Qt
from PySide6.QtTest import QTest

from tingmu import external_asr
from tingmu.quick import processing_setup
from tingmu.quick.main import create_qml_app


@pytest.fixture
def session(tmp_path, qtbot, qapp):
    root = tmp_path / "data"
    app = None

    def close():
        if app:
            bridge, engine = app._tingmu_quick
            window = engine.rootObjects()[0]
            window.setProperty("closingSession", True)
            window.close()
            bridge.shutdown()
            engine.deleteLater()
            qtbot.wait(30)

    def start():
        nonlocal app
        close()
        app, _, _ = create_qml_app(root, prompt_on_launch=True)
        qtbot.wait(50)

    def active():
        bridge, engine = app._tingmu_quick
        return bridge, engine.rootContext().contextProperty("studyHome"), engine.rootObjects()[0]

    def click(name):
        _, _, window = active()
        button = window.findChild(QObject, name)
        assert button.isVisible() and button.isEnabled(), name
        center = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        QTest.mouseClick(window, Qt.LeftButton, Qt.NoModifier, center)
        qtbot.wait(50)

    def verify():
        click("verifyProcessing")
        qtbot.waitUntil(lambda: not active()[1].verifying, timeout=20000)

    start()
    yield SimpleNamespace(active=active, click=click, start=start, verify=verify, root=root)
    close()


def test_skip_processing_remembers_choice_and_opens_workspace(session):
    session.click("skipProcessing")
    assert session.active()[1].page == "direction"
    session.start()
    assert session.active()[1].page == "direction"
    session.click("homeEnglish")
    bridge, home, _ = session.active()
    assert home.page == "home" and bridge.settings_snapshot().target_language == "en"
    assert not bridge.processing_setup_pending and not bridge.settings_snapshot().allow_audio_upload
    assert not bridge.queue.snapshot()
    session.start()
    session.click("homeEnglish")
    assert session.active()[1].page == "home"


def test_first_run_local_is_scoped_to_selected_language(session, monkeypatch):
    bridge, home, _ = session.active()
    assert home.page == "method"
    home.direction("zh-CN", "en")
    home.prepare()
    home.catalog()
    assert home.page == "method" and not bridge.queue.snapshot()
    session.click("chooseLocalProcessing")
    session.start()
    assert session.active()[1].page == "direction"
    session.click("homeEnglish")
    bridge, home, window = session.active()
    assert bridge.settings_snapshot().target_language == "en" and home.page == "speechSetup"
    session.click("configureProcessing")
    assert window.findChild(QObject, "workspaceSetup").property("opened")
    session.click("workspaceContinue")
    assert home.page == "speechSetup"
    ready = False
    monkeypatch.setattr(processing_setup, "weight_readiness",
                        lambda model: SimpleNamespace(ready=ready, message="缺少模型权重"))
    monkeypatch.setattr(processing_setup.runtime_deps, "probe", lambda *a, **k: {"ready": True})
    session.verify()
    assert home.page == "speechSetup" and "缺少" in home.message
    ready = True
    session.verify()
    assert home.page == "home" and not bridge.settings_snapshot().text_models_enabled
    session.start()
    assert session.active()[1].page == "direction"  # Preview builds still ask which language.
    session.click("homeEnglish")
    assert session.active()[1].page == "home"


def test_first_run_api_requires_working_transcription_and_text(session, monkeypatch):
    calls = []
    speech_reply = {"text": "missing timestamps"}
    class Server(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            calls.append(self.path)
            if self.path.endswith("transcriptions"):
                assert self.headers["Authorization"] == "Bearer test-only"
                assert b"RIFF" in body
                response = speech_reply
            else:
                assert json.loads(body)["model"] == "fixture-text"
                response = {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}]}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Server)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}/v1"
    # The real client validates HTTPS and credentials. Route only its final network hop to our fixture.
    real_post = external_asr._post
    monkeypatch.setattr(external_asr, "_post",
                        lambda url, key, fields, audio: real_post(base + "/audio/transcriptions", key, fields, audio))
    monkeypatch.setattr(processing_setup.runtime_deps, "probe", lambda *a, **k: {"ready": True})
    try:
        session.click("chooseApiProcessing")
        session.click("homeCantonese")
        bridge, home, window = session.active()
        assert home.page == "speechSetup" and bridge.settings_snapshot().asr_backend == "external"
        bridge.setting("asr_endpoint", "https://fixture.invalid/v1/audio/transcriptions")
        bridge.setting("asr_key_env", "")
        session.verify()
        assert home.page == "speechSetup" and "允许" in home.message and not calls
        bridge.setting("allow_audio_upload", True)
        session.verify()
        assert "密钥" in home.message and not calls
        monkeypatch.setenv("TINGMU_ONBOARDING_TEST_KEY", "test-only")
        bridge.setting("asr_key_env", "TINGMU_ONBOARDING_TEST_KEY")
        session.verify()
        assert home.page == "speechSetup" and "segments" in home.message
        speech_reply.clear()
        speech_reply["segments"] = []
        session.verify()
        assert home.page == "textSetup"
        session.click("configureProcessing")
        assert window.findChild(QObject, "workspaceSetup").property("section") == "text"
        session.click("workspaceContinue")
        bridge.command("textModelPolicy", {"mode": "off"})
        session.verify()
        assert home.page == "textSetup" and home.repair == "text"
        bridge.command("textModelPolicy", {"mode": "local"})
        ident = bridge.command("saveModel", {"provider": "local_compatible", "base_url": base,
                                            "model": "fixture-text", "key_source": "none"})
        for role in ("annotation", "review", "companion"):
            bridge.command("selectModel", {"role": role, "id": ident})
        session.verify()
        assert home.page == "home", home.state
        assert calls == ["/v1/audio/transcriptions"] * 2 + ["/v1/chat/completions"]
        assert "test-only" not in home.marker.read_text("utf-8")
        session.start()
        session.click("homeCantonese")
        assert session.active()[1].page == "home"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()
