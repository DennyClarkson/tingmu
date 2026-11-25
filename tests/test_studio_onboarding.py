"""Real guide navigation, per-workspace consent, and source-aware readiness."""

from dataclasses import replace

from PySide6.QtCore import QObject, QMetaObject, Qt, Q_ARG

from tingmu.models import AppSettings
from tingmu.quick.preparation_readiness import job_configuration, readiness_rows, subtitle_source, text_roles
from test_qml_v4 import find, invoke, quick as quick


def issues(settings, source, profiles=(), components=None):
    return {row["code"]: row["action"] for row in readiness_rows(
        settings, source, text_roles(settings, profiles, {}),
        components=components or {}, weights=False, language_supported=True, portable=True)}


def test_source_evidence_reuses_originals_without_asr_dependencies(tmp_path):
    settings = AppSettings(preparation_workflow="subtitle_agent", text_models_enabled=False)
    job = {"settings": settings.to_dict(), "subtitle_path": "translation.srt"}
    assert subtitle_source("auto", "subtitle_agent", job, "", "en") == "evaluate"
    for counts, expected in [([7, 3, 3], "caption"), ([0, 0, 6], "asr")]:
        job["evidence_counts"] = counts
        assert subtitle_source("auto", "subtitle_agent", job, "", "en") == expected
    path = tmp_path / "episode.mp4"
    path.with_suffix(".srt").write_text("caption", encoding="utf8")
    assert subtitle_source("auto", "classic", {}, str(path), "en") == "caption"
    assert issues(settings, "caption") == {"teach_off": "text"}
    assert issues(settings, "watch") == {}
    assert "unchecked" not in issues(settings, "evaluate")


def test_asr_dependencies_follow_backend_and_cuda_selection():
    settings = AppSettings(device="auto", asr_model="large-v3")
    components = {k: {"ready": False} for k in ("ffmpeg", "local-asr", "whisper-cuda", "qwen")}
    assert {k for k in issues(settings, "asr", components=components) if k in components} == {"ffmpeg", "local-asr"}
    settings.device = "cuda"
    assert issues(settings, "asr", components=components)["whisper-cuda"] == "runtime"
    settings.asr_model = "qwen3-asr-0.6b"
    assert {k for k in issues(settings, "asr", components=components) if k in components} == {"ffmpeg", "qwen"}
    settings.asr_backend = "external"
    external = issues(settings, "asr", components=components)
    assert "weights" not in external and external["audioConsent"] == "speech"
    settings.asr_backend = "local_api"
    assert "audioConsent" not in issues(settings, "asr", components=components)


def test_role_scope_and_smart_selection_do_not_confuse_global_assignment_with_consent():
    settings = AppSettings(segmentation_mode="llm_regroup", cloud_text_enabled=False,
                           annotation_profile_id="teacher", review_profile_id="reviewer")
    profiles = [dict(id="teacher", model="remote", is_loopback=False, key_source="none"),
                dict(id="reviewer", model="local", is_loopback=True, key_source="none")]
    assert issues(settings, "caption", profiles) == {"teach_remote": "allowRemote"}
    settings.review_profile_id = ""
    assert issues(settings, "caption", profiles) == {"teach_remote": "allowRemote", "smart_remote": "allowRemote"}
    settings.cloud_text_enabled = True
    assert issues(settings, "caption", profiles) == {}
    settings.text_models_enabled = False
    assert issues(settings, "caption", profiles) == {"teach_off": "allowRemote", "smart_off": "allowRemote"}


def test_queued_policies_intersect_with_current_permissions_and_retry_snapshot():
    workspace = AppSettings(cloud_text_enabled=True, annotation_profile_id="remote")
    saved = replace(workspace, text_models_enabled=False, cloud_text_enabled=False,
                    preparation_workflow="subtitle_agent", segmentation_mode="llm_regroup")
    job = {"settings": saved.to_dict()}
    effective, differs = job_configuration(workspace, job)
    assert differs and not effective.text_models_enabled and not effective.cloud_text_enabled
    assert effective.preparation_workflow == "subtitle_agent" and effective.segmentation_mode == "llm_regroup"
    roles = text_roles(effective, [dict(id="remote", model="test", key_source="none")], {})
    rows = readiness_rows(effective, "caption", roles, components={}, weights=True, language_supported=True,
                          portable=False, workspace=workspace)
    assert {r["code"] for r in rows} == {"teach_frozen", "smart_frozen"}
    job["retry_text_settings"] = {"text_models_enabled": True, "cloud_text_enabled": True}
    effective, _ = job_configuration(workspace, job)
    assert effective.cloud_text_enabled and effective.text_models_enabled
    effective, _ = job_configuration(replace(workspace, text_models_enabled=False), job)
    assert not effective.text_models_enabled
    external = replace(workspace, asr_backend="external", allow_audio_upload=True)
    changed = replace(external, asr_endpoint="https://changed.invalid/v1/audio/transcriptions")
    effective, _ = job_configuration(changed, {"settings": external.to_dict()})
    assert not effective.allow_audio_upload


def test_studio_card_grants_only_requested_text_scope_and_checks_source_runtime(quick, qtbot, monkeypatch):
    from test_player_sources import visual
    _, bridge, engine, window = quick
    monkeypatch.setattr("tingmu.agent.provider_status", lambda: {})
    bridge.command("saveModel", {"provider": "ollama", "model": "local-teacher"})
    local = next(p for p in bridge.modelProfiles if p["model"] == "local-teacher")
    bridge.command("selectModel", {"role": "annotation", "id": local["id"]})
    bridge.command("textModelPolicy", {"mode": "off"})
    invoke(window, "navigateStudio", "prepare")
    qtbot.wait(40)
    invoke(visual(window, "readinessFix-teach_off"), "clicked")
    assert bridge.settings["text_models_enabled"] and not bridge.settings["cloud_text_enabled"]
    bridge.command("saveModel", {"provider": "custom", "base_url": "https://example.invalid/v1",
                                 "model": "remote-teacher", "key_source": "none"})
    remote = next(p for p in bridge.modelProfiles if p["model"] == "remote-teacher")
    bridge.command("selectModel", {"role": "annotation", "id": remote["id"]})
    qtbot.wait(30)
    invoke(visual(window, "readinessFix-teach_remote"), "clicked")
    consent = find(window, "remoteTextPolicyConfirmation")
    qtbot.waitUntil(lambda: consent.property("opened"))
    assert not bridge.settings["cloud_text_enabled"]
    invoke(find(consent, "confirmRemotePolicy"), "clicked")
    assert bridge.settings["cloud_text_enabled"] and not bridge.settings["allow_audio_upload"]

    runtime = bridge.workspaces.runtime_setup
    assert not runtime.available
    # Other tests can warm the process-wide probe cache for this installation.
    # This case starts before the user has checked any components.
    monkeypatch.setattr("tingmu.runtime_deps.last_probe", lambda root, kind: None)
    monkeypatch.setattr(runtime, "results", {})
    probes = []
    def probe(kind, configured=None, **kwargs):
        probes.append(kind)
        return {"ready": True}
    monkeypatch.setattr(runtime, "_probe", probe)
    invoke(window, "navigateStudio", "speech")
    setup = find(window, "workspaceSetup")
    qtbot.waitUntil(lambda: setup.property("opened") and setup.property("section") == "models")
    qtbot.wait(30)
    invoke(visual(window, "readinessFix-unchecked"), "clicked")
    qtbot.waitUntil(lambda: bool(runtime.results) and not runtime.phase)
    assert "ffmpeg" in probes and "local-asr" in probes
    assert not engine._tingmu_warnings


def test_guide_navigation_and_real_workspace_remote_confirmation(quick, qtbot, monkeypatch):
    _, bridge, engine, window = quick
    guide = find(window, "helpDialog")
    assert guide.findChild(QObject, "guideSettingsTab") is None
    invoke(window, "openGuide")
    qtbot.waitUntil(lambda: guide.property("opened"))
    invoke(find(guide, "guideSettingsTab"), "clicked")
    invoke(find(guide, "guideAction-workspace"), "clicked")
    workspace = find(window, "workspaceSetup")
    qtbot.waitUntil(lambda: workspace.property("opened"))
    assert workspace.property("section") == "general"
    invoke(find(workspace, "workspaceQuickStart"), "clicked")
    qtbot.waitUntil(lambda: guide.property("opened"))
    invoke(find(guide, "guideStartTab"), "clicked")
    invoke(find(guide, "guideAction-prepare"), "clicked")
    assert window.property("viewMode") == "prepare"
    bridge.command("textModelPolicy", {"mode": "local"})
    monkeypatch.setattr("tingmu.agent.provider_status", lambda: {})
    bridge.command("saveModel", {"provider": "custom", "base_url": "https://example.invalid/v1",
                                 "model": "test-teacher", "key_source": "none"})
    remote = next(p for p in bridge.modelProfiles if p["model"] == "test-teacher")
    bridge.command("selectModel", {"role": "annotation", "id": remote["id"]})
    invoke(window, "navigateStudio", "text")
    qtbot.waitUntil(lambda: workspace.property("opened") and workspace.property("section") == "text")
    report = bridge.workspaces.preparationReadiness("text", {}, "")
    assert any(r["code"] == "teach_remote" and r["action"] == "allowRemote" for r in report["issues"])
    choice = find(workspace, "textModelPolicy")
    def choose_remote():
        assert QMetaObject.invokeMethod(choice, "activated", Qt.ConnectionType.DirectConnection, Q_ARG("int", 2))
    choose_remote()
    consent = find(workspace, "remoteTextPolicyConfirmation")
    qtbot.waitUntil(lambda: consent.property("opened"))
    assert not bridge.settings["cloud_text_enabled"] and choice.property("currentIndex") == 1
    invoke(find(consent, "cancelRemotePolicy"), "clicked")
    assert not bridge.settings["cloud_text_enabled"]
    choose_remote()
    # A changed destination requires explicit confirmation of the updated endpoint.
    bridge.command("saveModel", {"id": remote["id"], "base_url": "https://changed.invalid/v1"})
    invoke(find(consent, "confirmRemotePolicy"), "clicked")
    assert consent.property("changedOffer") and not bridge.settings["cloud_text_enabled"]
    invoke(find(consent, "confirmRemotePolicy"), "clicked")
    assert bridge.settings["cloud_text_enabled"]
    assert not bridge.settings["allow_audio_upload"]
    assert not engine._tingmu_warnings



def test_agent_shows_conditional_audio_prerequisites_before_source_inspection(quick):
    _, bridge, _, _ = quick
    controller = bridge.workspaces
    controller.runtime_setup.results["ffmpeg"] = {"ready": False}
    settings = bridge.settings_snapshot()
    job = {"settings": {**settings.to_dict(), "preparation_workflow": "subtitle_agent"}}
    report = controller.preparationReadiness("auto", job, "")
    assert report["source"] == "evaluate"
    assert "ffmpeg" in {r["code"] for r in report["asrFallback"]}
    assert "ffmpeg" not in {r["code"] for r in report["issues"]}
    job["evidence_counts"] = [10, 5, 0]
    report = controller.preparationReadiness("auto", job, "")
    assert report["source"] == "caption" and report["asrFallback"] == []
