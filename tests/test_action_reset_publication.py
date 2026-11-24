"""Agent resets publish the same fresh views as native queue reset commands."""

from copy import deepcopy
from unittest.mock import Mock

import pytest

from tingmu.app_actions import ActionContext
from tingmu.models import Cue, StudyDocument
from test_domain_events import bridge as bridge


@pytest.mark.parametrize("mode", ["all", "teaching"])
def test_action_reset_without_start_reloads_document_and_invalidates_selected_cache(
    bridge, tmp_path, qtbot, monkeypatch, mode
):
    media = tmp_path / "selected.mkv"
    other = tmp_path / "other.mkv"
    for path in (media, other):
        path.touch()
        document = StudyDocument(
            str(path),
            bridge._settings.language,
            [Cue("one", 1, 2, "source", translation="translation", explanation="lesson")],
            metadata={"stage": "transcribed", "raw_display_text": {"one": "source"}},
        )
        bridge.store.save_document(document)
    bridge.catalog.add([media, other])
    bridge.media_path = str(media)
    bridge._load_document()
    bridge.queue.enqueue([media], deepcopy(bridge._settings))
    bridge._refresh_jobs()
    qtbot.waitUntil(lambda: bridge._library_future is None and bridge._library_pending is None)
    assert bridge.document.cues[0].explanation == "lesson"
    assert bridge._library_cache.peek(str(media), bridge._settings.language)["cueCount"] == 1
    other_before = bridge._library_cache.peek(str(other), bridge._settings.language)
    invalidate = Mock(wraps=bridge._library_cache.invalidate)
    monkeypatch.setattr(bridge._library_cache, "invalidate", invalidate)
    context = ActionContext(bridge._active_session_id, str(media), deepcopy(bridge._settings.language))

    result = bridge._perform_app_action(
        "process_videos", {"scope": "current", "mode": mode, "start": False}, context, None
    )

    assert result["accepted"] and not result["queue_running"]
    invalidate.assert_called_once_with(str(media))
    assert bridge._library_cache.peek(str(other), bridge._settings.language) == other_before
    job = bridge.queue.snapshot()[0]
    assert job["status"] == "queued"
    assert job["force_asr"] == (mode == "all")
    assert job["teaching_only"] == (mode == "teaching")
    if mode == "all":
        assert bridge.document is None and bridge.cueCount == 0
        assert bridge.store.load_document(str(media), context.language) is None
    else:
        assert bridge.cueCount == 1
        assert bridge.document.cues[0].translation == bridge.document.cues[0].explanation == ""
        assert bridge.document.cues[0].original_text == "source"
    qtbot.waitUntil(lambda: bridge._library_future is None and bridge._library_pending is None)
    assert bridge._library_cache.peek(str(media), context.language)["cueCount"] == (0 if mode == "all" else 1)


@pytest.mark.parametrize("action", ["process_videos", "manage_queue"])
def test_agent_start_actions_reach_facade_start_hook(bridge, tmp_path, monkeypatch, action):
    media = tmp_path / "selected.mkv"
    media.touch()
    bridge.queue.enqueue([media], deepcopy(bridge._settings))
    job_id = bridge.queue.snapshot()[0]["id"]
    context = ActionContext(
        bridge._active_session_id, "", deepcopy(bridge._settings.language), selected_media=(str(media),)
    )
    started = Mock()
    monkeypatch.setattr(bridge, "_start_action_jobs", started)
    arguments = (
        {"scope": "selected", "mode": "prepare"}
        if action == "process_videos"
        else {"action": "start", "job_ids": [job_id]}
    )
    bridge._perform_app_action(action, arguments, context, None)
    started.assert_called_once_with([job_id])
    assert "queue" not in bridge.task_keys()
