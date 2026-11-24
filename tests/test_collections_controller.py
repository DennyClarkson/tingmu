"""Collection state and frozen subtitle operations work without a UI facade."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from tingmu.conversations import ConversationStore
from tingmu.models import AppSettings, Cue, StudyDocument, Token
from tingmu.notebooks import NotebookManager
from tingmu.quick.collections_controller import CollectionsController, CollectionsPorts, CollectionsState
from tingmu.storage import Store
from tingmu.timeline import SubtitleTimeline


@pytest.fixture
def collection(tmp_path):
    store = Store(tmp_path / "data")
    settings = AppSettings(source_language="en", target_language="en")
    cue = Cue("one", 0, 2, "Original sentence", "raw acoustic text", tokens=[Token("Original")])
    document = StudyDocument("synthetic.mp4", settings.language, [cue])
    timeline = SubtitleTimeline(document.cues)
    notebook = NotebookManager(store)
    conversations = ConversationStore(store)
    session = conversations.create(settings.language, document.media_path)
    context = SimpleNamespace(
        media=document.media_path,
        settings=settings,
        document=document,
        timeline=timeline,
        source=cue,
        requests=[],
        messages=[],
        errors=[],
        feedback=[],
        changed=[],
        status={},
        command_error=None,
    )

    def error(exc, **kwargs):
        context.errors.append((str(exc), kwargs))

    def send_chat(text, **kwargs):
        context.requests.append((text, deepcopy(kwargs)))
        context.messages.append({"id": "submitted"})

    def command(name, payload):
        if context.command_error:
            raise context.command_error
        assert name == "saveNote"
        store.save_note(context.media, cue, payload["note"], settings.language)

    ports = CollectionsPorts(
        settings=lambda: settings,
        media_path=lambda: context.media,
        store=lambda: store,
        notebook_manager=lambda: notebook,
        find_cue=lambda ident: next((row for row in timeline.cues if row.id == ident), None),
        source_ids=timeline.source_ids,
        source_cue=lambda ident: cue if ident == cue.id else None,
        document=lambda: document,
        imported_cue=lambda: {},
        embedded_cue=lambda: {},
        conversations=lambda: conversations,
        active_session_id=lambda: session["id"],
        messages=lambda: context.messages,
        collect_word=lambda name, payload: controller._collect_word(name, payload),
        refresh_notebooks=lambda: controller._refresh_entries(),
        activate_context_session=lambda: None,
        send_chat=send_chat,
        command=command,
        error=error,
        error_text=lambda: context.errors[-1][0],
        log_exception=lambda *args: None,
        set_state=lambda **kwargs: context.status.update(kwargs),
        entries_changed=lambda: context.changed.append(True),
        collection_feedback=lambda success, text: context.feedback.append((success, text)),
    )
    controller = CollectionsController(CollectionsState(), ports)
    return controller, context, store


def test_collection_lookup_uses_owned_indexes_without_rereading_storage(collection, monkeypatch):
    controller, context, store = collection
    store.save_note(context.media, context.source, "handwritten", context.settings.language)
    controller._refresh_entries()
    assert controller.state.entries_revision == 1
    assert context.changed == [True]
    monkeypatch.setattr(store, "entries", lambda *args: pytest.fail("lookup should use collection indexes"))
    assert controller.cueNote("one") == "handwritten"
    assert controller.sentenceCollected("one", "Original sentence")
    context.media = "different.mp4"
    assert controller.cueNote("one") == ""
    assert not controller.sentenceCollected("one", "Original sentence")
    assert controller.wordCollectionStates("one", ["Original"]) == {}


def test_word_collection_rejects_changed_context_and_preserves_idempotence(collection):
    controller, context, store = collection
    request = {"cueId": "one", "tokenIndex": 0, "mediaPath": context.media, "text": "Original"}
    assert controller.collectWord({**request, "mediaPath": "different.mp4"}) == ""
    assert context.errors[-1][1] == {"domain": "learning"}
    assert context.feedback[-1] == (False, context.errors[-1][0])
    assert store.entries() == []
    assert controller.collectWord(request) == "collected"
    assert controller.collectWord(request) == "pending"
    assert len(store.entries()) == 1
    assert controller.wordCollectionStates("one", ["Original"]) == {"Original": "pending"}


def test_subtitle_correction_freezes_source_revision_and_uses_original_timing(collection):
    controller, context, _ = collection
    captured = controller.captureSubtitle("one")
    assert captured["target"]["source_id"] == "one"
    assert controller.submitSubtitleCorrection(captured, "Confirm this wording") == {"messageId": "submitted"}
    request = context.requests[-1][1]["request_context"]
    assert request["correction"] == captured["target"]
    assert (request["cue"]["start"], request["cue"]["end"], request["cue"]["original_text"]) == (
        0,
        2,
        "raw acoustic text",
    )
    context.source.text = "Updated elsewhere"
    assert "error" in controller.submitSubtitleCorrection(captured, "Stale correction")
    assert len(context.requests) == 1


def test_note_save_reports_failure_and_retains_last_persisted_note(collection):
    controller, context, store = collection
    assert controller.saveCueNote("one", "saved note")
    context.command_error = OSError("synthetic save failure")
    assert not controller.saveCueNote("one", "unsaved edit")
    assert store.entries()[0]["note"] == "saved note"
    assert context.errors[-1] == ("synthetic save failure", {"domain": "learning"})
