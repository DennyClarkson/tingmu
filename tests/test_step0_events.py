"""Material mutations and persisted replies drive fresh UI projections."""

from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest
from PySide6.QtTest import QSignalSpy

from tingmu.app_actions import ActionContext
from tingmu.models import AppSettings
from tingmu.quick.bridge import Bridge
from tingmu.quick.features import FeatureMixin
from tingmu.storage import canonical_path
from test_domain_events import bridge as bridge
from test_workspace_events import pending_reply, request_switch, workspace as workspace


@pytest.mark.parametrize("command", ["resetJobs", "resetMedia"])
def test_material_reset_invalidates_only_selected_media_before_refresh(tmp_path, command):
    selected = canonical_path(tmp_path / "selected.mkv")
    other = canonical_path(tmp_path / "other.mkv")
    events = Mock()
    jobs = [{"id": "selected", "media_path": selected},
            {"id": "duplicate", "media_path": selected},
            {"id": "other", "media_path": other}]
    queue = Mock()
    queue.snapshot.return_value = jobs
    queue.reset.return_value = 2
    events.attach_mock(queue.reset, "reset")
    bridge = SimpleNamespace(
        _workers={}, _settings=AppSettings(), queue=queue,
        _library_cache=SimpleNamespace(invalidate=events.invalidate),
        _load_document=events.load_document, _refresh_jobs=events.refresh_jobs,
        _refresh_library=events.refresh_library, _set_state=Mock(),
    )
    args = {"ids": ["selected", "duplicate"]} if command == "resetJobs" else {"paths": [selected]}
    assert FeatureMixin._feature_command(bridge, command, args)
    assert events.mock_calls == [
        call.reset(["selected", "duplicate"], "all", False, bridge._settings),
        call.invalidate(selected), call.load_document(), call.refresh_jobs(), call.refresh_library(),
    ]


def test_subtitle_revision_invalidates_bound_media_before_publication(tmp_path, monkeypatch):
    media = canonical_path(tmp_path / "selected.mkv")
    settings = AppSettings()
    context = ActionContext("chat", media, settings.language)
    document = object()
    events = Mock()
    revise = Mock(return_value=document)
    events.attach_mock(revise, "revise")
    monkeypatch.setattr("tingmu.corrections.revise_target", revise)
    bridge = SimpleNamespace(
        media_path=media, _settings=settings, _workers={}, store=object(),
        queue=SimpleNamespace(snapshot=lambda: []),
        _library_cache=SimpleNamespace(invalidate=events.invalidate),
        _set_document=events.set_document, _refresh_library=events.refresh_library, _set_state=Mock(),
    )
    result = FeatureMixin._perform_app_action(bridge, "revise_current_subtitle", {}, context, None)
    assert result["saved"]
    assert events.mock_calls == [
        call.revise(bridge.store, context, {}, settings.target_script, None),
        call.invalidate(media), call.set_document(document), call.refresh_library(),
    ]


@pytest.mark.parametrize("status", ["complete", "completed"])
def test_persisted_completion_aliases_allow_deferred_workspace_switch(workspace, qtbot, status):
    bridge, controller, target, context, transitions, start = workspace
    reply = pending_reply(bridge)

    def complete(_):
        rows = bridge.conversations.load(context.session_id)["messages"]
        rows[-1].update(status=status, content="Persisted reply")
        bridge.conversations.save(context.session_id, rows)

    release = start("chat", complete)
    request_switch(controller, target, context)
    assert controller._switch_origin == (context.session_id, reply["id"])
    release.set()
    qtbot.waitUntil(lambda: bool(transitions))
    assert transitions == [(target, None, False)]
    assert not controller.pending_switch
    assert not controller.error


def test_explicit_queue_refresh_consumes_pending_timer(bridge, qtbot, monkeypatch):
    snapshot = Mock(return_value=[])
    monkeypatch.setattr(bridge.queue, "snapshot", snapshot)
    timeout = QSignalSpy(bridge.timer.timeout)
    bridge._schedule_queue_refresh()
    assert bridge.timer.isActive()
    bridge._refresh_jobs()
    assert not bridge.timer.isActive()
    qtbot.wait(bridge.timer.interval() + 30)
    snapshot.assert_called_once_with()
    assert timeout.count() == 0


@pytest.mark.parametrize("status", ["completed", "paused", "failed"])
def test_queue_terminal_change_invalidates_changed_media_before_refresh(bridge, tmp_path, monkeypatch, status):
    media = canonical_path(tmp_path / "selected.mkv")
    other = canonical_path(tmp_path / "other.mkv")
    jobs = [{"id": "selected", "media_path": media, "status": "running"},
            {"id": "other", "media_path": other, "status": "running"}]
    monkeypatch.setattr(bridge.queue, "snapshot", lambda: jobs)
    events = Mock()
    monkeypatch.setattr(bridge._library_cache, "invalidate", events.invalidate)
    monkeypatch.setattr(bridge, "_refresh_library", events.refresh_library)
    bridge._refresh_jobs()
    events.reset_mock()
    jobs[0]["status"] = status
    bridge._refresh_jobs()
    assert events.mock_calls == [call.invalidate(media), call.refresh_library()]
    events.reset_mock()
    bridge._refresh_jobs()
    assert events.mock_calls == []


@pytest.mark.parametrize("navigated", [False, True])
def test_subtitle_import_completion_invalidates_original_media(tmp_path, navigated):
    media = canonical_path(tmp_path / "selected.mkv")
    document = SimpleNamespace(cues=[object()])
    events = Mock()
    bridge = SimpleNamespace(
        media_path=media, _settings=AppSettings(), _workers={}, _start_job=Mock(),
        _library_cache=SimpleNamespace(invalidate=events.invalidate),
        _set_document=events.set_document, _refresh_library=events.refresh_library, _set_state=Mock(),
    )
    Bridge._import_subtitle(bridge, str(tmp_path / "subtitles.srt"))
    _, _, completed = bridge._start_job.call_args.args
    if navigated:
        bridge.media_path = canonical_path(tmp_path / "other.mkv")
    completed(document)
    expected = [call.invalidate(media)]
    if not navigated:
        expected.append(call.set_document(document))
    assert events.mock_calls == [*expected, call.refresh_library()]
