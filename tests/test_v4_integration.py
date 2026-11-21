"""v4 end-to-end service boundaries; all inference/ASR transports are mocked."""

from copy import deepcopy
from dataclasses import replace
import json
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

import tingmu.pipeline as pipeline
from tingmu.batch import PreparationQueue
from tingmu.models import AppSettings, Cue, LanguagePair, StudyDocument, Token
from tingmu.providers import ModelRegistry
from tingmu.quick.bridge import Bridge
from tingmu.quick.features import message
from tingmu.storage import Store


@pytest.fixture
def bridge(qapp, tmp_path):
    obj = Bridge(Store(tmp_path / "data"))
    yield obj
    obj.shutdown()


@pytest.fixture(autouse=True)
def no_real_provider_network(monkeypatch):
    monkeypatch.setattr("tingmu.providers._open", lambda *a, **kw: pytest.fail("Unexpected real provider HTTP"))


def _movie(tmp_path, name="sample.mkv"):
    path = tmp_path / name
    path.write_bytes(b"source-media-never-edit")
    return path


def _seed(store, movie, language=None):
    language = language or LanguagePair()
    cue = Cue("c1", 1, 3, "Reviewed text", original_text="Raw text", translation="translation",
              explanation="explanation", tokens=[Token("Reviewed", "reading", "meaning")],
              segmented_texts=["Reviewed ", "text"])
    metadata = {"stage": "ready", "review_stage": "ready", "review_spec": {"original": True},
                "reviewed_text": {"c1": "Reviewed text"}, "reviewed_segmented_texts": {"c1": cue.segmented_texts},
                "raw_display_text": {"c1": "Raw text"}, "transcript_spec": {"original_asr": True},
                "review_records": {"c1": {"status": "reviewed"}}, "completed_ids": ["c1"],
                "annotated_count": 1, "annotation_spec": {"old": True},
                "model_responses": [{"requested_model": "old-teacher"}]}
    store.save_document(StudyDocument(str(movie), language, [cue], metadata=metadata))
    store.save_note(str(movie), cue, "Keep this personal note", language)
    store.save_word(str(movie), cue, cue.tokens[0], language)
    return store._document_path(str(movie), language)


def test_multiselect_reset_preserves_unselected_languages_and_personal_data(tmp_path):
    store = Store(tmp_path / "data")
    queue = PreparationQueue(store)
    movies = [_movie(tmp_path, f"{i}.mkv") for i in range(3)]
    settings = AppSettings()
    paths = [_seed(store, m) for m in movies]
    other_path = _seed(store, movies[0], LanguagePair(target="en"))
    other_bytes, unselected_bytes = other_path.read_bytes(), paths[2].read_bytes()
    entries = store.entries()
    store.save_conversation(str(movies[0]), settings.language, [{"role": "user", "content": "Keep chat"}])
    store.save_position(str(movies[0]), 2700)
    jobs = queue.enqueue(movies, settings)
    assert queue.reset([j["id"] for j in jobs[:2]], "all", True,
                       replace(settings, target_language="ja", model="new-model")) == 2
    assert not paths[0].exists() and not paths[1].exists()
    assert paths[2].read_bytes() == unselected_bytes and other_path.read_bytes() == other_bytes
    assert store.entries() == entries and store.load_position(str(movies[0])) == 2700
    assert store.load_conversation(str(movies[0]), settings.language)[0]["content"] == "Keep chat"
    assert all(m.read_bytes() == b"source-media-never-edit" for m in movies)
    reset = queue.snapshot()[:2]
    assert all(j["force_asr"] and j["subtitle_path"] is None and j["status"] == "queued" for j in reset)
    assert all(j["settings"]["target_language"] == "yue" and j["settings"]["model"] == "new-model" for j in reset)
    assert len(list((store.data_dir / "material-backups").rglob("*.json"))) == 2


def test_running_queue_and_bridge_reject_material_reset(bridge, tmp_path):
    movie = _movie(tmp_path)
    path = _seed(bridge.store, movie)
    before = path.read_bytes()
    job = bridge.queue.enqueue([movie], bridge._settings)[0]
    bridge.queue._running = True
    try:
        with pytest.raises(ValueError, match="暂停"):
            bridge.queue.reset([job["id"]])
    finally:
        bridge.queue._running = False
    bridge._workers["import"] = (None, threading.Event())
    try:
        with pytest.raises(ValueError, match="暂停"):
            bridge._feature_command("resetJobs", {"ids": [job["id"]], "mode": "all"})
    finally:
        bridge._workers.pop("import")
    assert path.read_bytes() == before


def test_force_asr_ignores_sidecar_and_failed_retry_reuses_checkpoint(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    movie = _movie(tmp_path)
    movie.with_suffix(".srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nStale sidecar\n")
    settings = AppSettings(review_enabled=False, target_language="en")
    queue = PreparationQueue(store)
    job = queue.enqueue([movie], settings)[0]
    assert job["subtitle_path"]
    queue.reset([job["id"]], "all", True, settings)
    transcriptions, annotations = [], []
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **kw:
                        transcriptions.append(1) or [Cue("a", 0, 1, "Fresh"), Cue("b", 1, 2, "Transcript")])
    monkeypatch.setattr(pipeline, "load_subtitles", lambda *a: pytest.fail("Reset used stale sidecar"))

    class Teacher:
        def verify_translations(self, cues, language, context):
            return [(c, list(c.segmented_texts) if len(c.segmented_texts) > 1 else []) for c in cues]

        def __init__(self, **kw):
            pass

        def annotate(self, cues, language, **kw):
            annotations.append([c.id for c in cues])
            if len(annotations) == 1:
                kw["checkpoint"]([replace(cues[0], translation="draft", explanation="saved-first")])
                raise RuntimeError("mock outage")
            return [replace(c, translation="draft", explanation="retried-second") for c in cues]

    monkeypatch.setattr(pipeline, "LearningAgent", Teacher)
    assert queue.run()[0]["status"] == "failed"
    queue = PreparationQueue(store)
    queue.retry(job["id"])
    assert queue.run()[0]["status"] == "completed"
    assert transcriptions == [1] and annotations == [["a", "b"], ["b"]]
    doc = store.load_document(str(movie), settings.language)
    assert [c.explanation for c in doc.cues] == ["saved-first", "retried-second"]
    assert movie.with_suffix(".srt").read_text().endswith("Stale sidecar\n")


def test_teaching_reset_preserves_review_and_asr_through_failed_resume(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    movie = _movie(tmp_path)
    settings = AppSettings(target_language="en", review_enabled=True)
    _seed(store, movie, settings.language)
    before = store.load_document(str(movie), settings.language)
    store.reset_material(str(movie), settings.language, "teaching")
    reset = store.load_document(str(movie), settings.language)
    assert not reset.cues[0].translation and not reset.cues[0].tokens
    for name in ("review_spec", "review_records", "raw_display_text", "reviewed_text", "transcript_spec"):
        assert reset.metadata[name] == before.metadata[name]
    assert reset.cues[0].original_text == "Raw text"
    assert reset.cues[0].segmented_texts == ["Reviewed ", "text"]
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **kw: pytest.fail("Teaching reset repeated ASR"))
    monkeypatch.setattr(pipeline, "TranscriptReviewer", lambda **kw: pytest.fail("Teaching reset repeated review"))
    attempts = []

    class Teacher:
        def verify_translations(self, cues, language, context):
            return [(c, list(c.segmented_texts) if len(c.segmented_texts) > 1 else []) for c in cues]

        def __init__(self, **kw):
            pass

        def annotate(self, cues, language, **kw):
            attempts.append(1)
            assert cues[0].text == "Reviewed text" and self.preserve_text
            if len(attempts) == 1:
                raise RuntimeError("mock outage")
            return [replace(cues[0], translation="draft", explanation="new-teaching")]

    monkeypatch.setattr(pipeline, "LearningAgent", Teacher)
    with pytest.raises(RuntimeError):
        pipeline.prepare_media(movie, settings, store)
    result = pipeline.prepare_media(movie, settings, store)
    assert result.cues[0].explanation == "new-teaching" and attempts == [1, 1]
    assert result.metadata["review_records"] == before.metadata["review_records"]


def test_chat_switch_and_cancel_keep_partial_in_origin_session(bridge, tmp_path, monkeypatch, qtbot):
    started, release = threading.Event(), threading.Event()
    movie = _movie(tmp_path)
    bridge.media_path = str(movie)
    bridge._activate_context_session()
    original = bridge._active_session_id

    def reply(self, *args, on_delta, cancelled, **kw):
        on_delta("partial answer")
        started.set()
        release.wait(3)
        assert cancelled()
        raise RuntimeError("mock cancelled transport")

    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", reply)
    try:
        bridge._v4_send_chat("question")
        qtbot.waitUntil(started.is_set)
        qtbot.waitUntil(lambda: bridge._messages[-1]["content"] == "partial answer")
        bridge._feature_command("newChat", {})
        selected = bridge._active_session_id
        assert selected != original and bridge._messages == []
        bridge._feature_command("stopChat", {})
    finally:
        release.set()
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    assert bridge._active_session_id == selected and bridge._messages == []
    messages = bridge.conversations.load(original)["messages"]
    assert messages[-1]["content"] == "partial answer" and messages[-1]["status"] == "stopped"
    assert all(m.get("id") and m.get("created_at") for m in messages)


def test_edit_creates_branch_and_preserves_original_messages(bridge, monkeypatch, qtbot):
    original = bridge._active_session_id
    history = [message("user", "old question"), message("assistant", "old answer"),
               message("user", "later question"), message("assistant", "later answer")]
    bridge.conversations.save(original, history)
    history = bridge.conversations.load(original)["messages"]
    bridge._select_chat(original)
    captured = []

    def reply(self, text, prior, *args, **kw):
        captured.append((text, prior))
        return "new answer"

    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", reply)
    bridge._feature_command("editChat", {"id": history[0]["id"], "text": "new question"})
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    assert bridge._active_session_id != original
    assert bridge.conversations.load(original)["messages"] == history
    assert captured == [("new question", [])]
    assert [m["content"] for m in bridge._messages] == ["new question", "new answer"]


def test_tools_remain_scoped_to_captured_media_language_and_substring(bridge, tmp_path):
    first, second = _movie(tmp_path, "first.mkv"), _movie(tmp_path, "second.mkv")
    cue = Cue("original", 1, 2, "Have a nice day", tokens=[Token("nice", "nais", "愉快")])
    language = LanguagePair(target="en")
    executor = bridge._tool_executor(str(first), language, deepcopy(cue), None)
    bridge.media_path = str(second)
    bridge._settings.target_language = "ja"
    with pytest.raises(ValueError):
        executor("collect_current_word", {"text": "nice", "media": str(second), "target_language": "ja"})
    executor("collect_current_word", {"text": "nice"})
    executor("collect_current_sentence", {"note": "captured-context"})
    entries = bridge.store.entries()
    assert len(entries) == 2 and all(e["media_path"] == str(first) and e["target_language"] == "en" for e in entries)
    assert next(e for e in entries if e["kind"] == "word")["reading"] == "nais"
    with pytest.raises(ValueError):
        executor("collect_current_word", {"text": "invented word"})
    with pytest.raises(ValueError):
        executor("execute_shell", {"command": "no"})
    assert executor("read_notebook", {})["language"] == "en"
    assert not bridge.notebook_manager.snapshot("ja")["entries"]


def _profile(registry, role):
    return registry.save_profile({"label": role, "provider": "ollama", "protocol": "openai",
                                  "base_url": "http://127.0.0.1:11434/v1", "key_source": "none",
                                  "model": role + "-model"})


def test_model_selection_wires_all_roles_and_cloud_off_blocks_chat(bridge, monkeypatch):
    selected = {role: _profile(bridge.model_registry, role) for role in ("annotation", "review", "companion")}
    bridge._refresh_models()
    for role, profile in selected.items():
        bridge._feature_command("selectModel", {"role": role, "id": profile["id"]})
        assert bridge._profile(role)["model"] == role + "-model"
    persisted = bridge.store.load_settings()
    assert persisted.annotation_profile_id == selected["annotation"]["id"]
    assert persisted.review_profile_id == selected["review"]["id"]
    assert persisted.companion_profile_id == selected["companion"]["id"]
    tutor = bridge._tutor_transport()
    assert tutor.model == "companion-model"
    remote = bridge.model_registry.save_profile({"provider": "custom", "base_url": "https://example.invalid/v1",
                                                "model": "cloud-model", "key_source": "none"})
    bridge._refresh_models()
    bridge._feature_command("selectModel", {"role": "companion", "id": remote["id"]})
    bridge._settings.cloud_text_enabled = False
    with pytest.raises(ValueError, match="云端文本"):
        bridge._v4_send_chat("must stay local")
    with pytest.raises(ValueError, match="云端文本"):
        bridge._organize_notebook()
    assert not bridge._workers


def test_pipeline_passes_selected_profile_to_annotation_and_review(tmp_path, monkeypatch):
    movie = _movie(tmp_path)
    store = Store(tmp_path / "data")
    registry = ModelRegistry(store.data_dir)
    teacher, reviewer = _profile(registry, "annotation"), _profile(registry, "review")
    settings = AppSettings(target_language="en", annotation_profile_id=teacher["id"],
                           review_profile_id=reviewer["id"])
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **kw: [Cue("a", 1, 2, "source")])
    calls = []

    class Reviewer:
        def __init__(self, **kw):
            calls.append(("review", kw))
            self.review_metadata, self.response_metadata, self.batch_metadata = {}, [], []

        def review(self, cues, *a, **kw):
            return [replace(c, text="reviewed") for c in cues]

    class Teacher:
        def verify_translations(self, cues, language, context):
            return [(c, list(c.segmented_texts) if len(c.segmented_texts) > 1 else []) for c in cues]

        def __init__(self, **kw):
            calls.append(("annotation", kw))

        def annotate(self, cues, *a, **kw):
            assert self.preserve_text
            return [replace(c, translation="draft", explanation="teacher") for c in cues]

    monkeypatch.setattr(pipeline, "LearningAgent", Teacher)
    monkeypatch.setattr(pipeline, "TranscriptReviewer", Reviewer)
    result = pipeline.prepare_media(movie, settings, store)
    assert [(role, kwargs["model"]) for role, kwargs in calls] == [
        ("review", "review-model"), ("annotation", "annotation-model")]
    assert all(kwargs["profile"]["model"] == role + "-model" for role, kwargs in calls)
    assert result.cues[0].text == "reviewed" and result.cues[0].explanation == "teacher"
    assert "_secret" not in store._document_path(str(movie), settings.language).read_text(encoding="utf-8")


def test_cloud_off_does_not_resolve_profiles_or_use_any_cloud_model(tmp_path, monkeypatch):
    store, movie = Store(tmp_path / "data"), _movie(tmp_path)
    settings = AppSettings(target_language="en", cloud_text_enabled=False,
                           annotation_profile_id="missing", review_profile_id="missing")
    monkeypatch.setattr(ModelRegistry, "resolve", lambda *a: pytest.fail("Cloud-off resolved cloud profile"))
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **kw: [Cue("a", 1, 2, "local")])
    monkeypatch.setattr(pipeline, "annotate_local", lambda cues, language: cues)
    monkeypatch.setattr(pipeline, "LearningAgent", lambda **kw: pytest.fail("Cloud-off constructed teacher"))
    monkeypatch.setattr(pipeline, "TranscriptReviewer", lambda **kw: pytest.fail("Cloud-off constructed reviewer"))
    result = pipeline.prepare_media(movie, settings, store)
    assert result.provider == "local" and result.metadata["stage"] == "ready"


def test_teaching_reset_cloud_off_preserves_already_reviewed_text_and_splits(tmp_path, monkeypatch):
    store, movie = Store(tmp_path / "data"), _movie(tmp_path)
    settings = AppSettings(target_language="en", cloud_text_enabled=False)
    _seed(store, movie, settings.language)
    store.reset_material(str(movie), settings.language, "teaching")
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **kw: pytest.fail("Local teaching repeated ASR"))
    monkeypatch.setattr(pipeline, "TranscriptReviewer", lambda **kw: pytest.fail("Cloud-off repeated review"))
    monkeypatch.setattr(pipeline, "annotate_local", lambda cues, language: cues)
    result = pipeline.prepare_media(movie, settings, store)
    assert result.cues[0].text == "Reviewed text"
    assert result.cues[0].original_text == "Raw text"
    assert result.cues[0].segmented_texts == ["Reviewed ", "text"]


def test_companion_selected_profile_is_used_by_stream_transport(bridge, monkeypatch, qtbot):
    profile = _profile(bridge.model_registry, "companion")
    bridge._refresh_models()
    bridge._feature_command("selectModel", {"role": "companion", "id": profile["id"]})
    requests = []

    def stream(transport, body, *, on_delta=None, cancelled=None, on_reasoning=None):
        requests.append((transport._profile["id"], transport.model, deepcopy(body)))
        if on_delta:
            on_delta("profile reply")
        return {"model": "reported-model", "choices": [{"finish_reason": "stop",
                "message": {"role": "assistant", "content": "profile reply"}}], "usage": {"total_tokens": 7}}

    monkeypatch.setattr("tingmu.agent.LearningAgent._stream_request", stream)
    bridge._v4_send_chat("Use selected model")
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    assert requests[0][0:2] == (profile["id"], "companion-model")
    assert requests[0][2]["stream"] is True
    assert bridge._messages[-1]["content"] == "profile reply"
    assert bridge._messages[-1]["model"] == "companion-model"


def test_reentering_media_context_does_not_remigrate_new_chat_mirror(bridge, tmp_path, monkeypatch, qtbot):
    first, second = _movie(tmp_path, "first.mkv"), _movie(tmp_path, "second.mkv")
    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", lambda *a, **kw: "saved answer")
    bridge.media_path = str(first)
    bridge._activate_context_session()
    original = bridge._active_session_id
    bridge._v4_send_chat("saved question")
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    for _ in range(3):
        bridge.media_path = str(second)
        bridge._load_personal()
        bridge.media_path = str(first)
        bridge._load_personal()
        assert bridge._active_session_id == original
    sessions = bridge.conversations.list_sessions("yue")
    assert len([s for s in sessions if s["media_path"] == str(first)]) == 1
    assert len(bridge.conversations.load(original)["messages"]) == 2


def test_teaching_reset_requeue_keeps_original_audio_and_asr_settings(tmp_path):
    movie, store = _movie(tmp_path), Store(tmp_path / "data")
    queue = PreparationQueue(store)
    original = AppSettings(audio_track=2, asr_model="original-model", device="cpu")
    job = queue.enqueue([movie], original)[0]
    queue.reset([job["id"]], "teaching", True,
                replace(original, audio_track=4, asr_model="different-model", model="new-teacher"))
    current = queue.snapshot()[0]["settings"]
    assert current["audio_track"] == 2 and current["asr_model"] == "original-model"
    assert current["model"] == "new-teacher"


def test_explicit_notebook_language_saves_original_book_after_external_switch(bridge):
    original = bridge.notebook_manager.snapshot("yue")
    other = bridge.notebook_manager.sync("en")
    # Equal content/revisions prove the language identity, not merely the hash,
    # prevents saving an old editor into a newly selected book.
    bridge.notebook_manager.save("en", original["markdown"], other["revision"])
    bridge.setting("target_language", "en")
    before = bridge.notebook_manager.snapshot("en")
    draft = original["markdown"] + "\n我的粤语草稿\n"
    bridge._feature_command("saveNotebook", {"language": "yue", "markdown": draft,
                                             "revision": original["revision"]})
    assert bridge.notebook_manager.snapshot("yue")["markdown"] == draft
    assert bridge.notebook_manager.snapshot("en")["markdown"] == before["markdown"]
    assert bridge._notebook_language == "en"


def test_companion_construction_failure_is_persisted_as_error(bridge, monkeypatch, qtbot):
    session_id = bridge._active_session_id

    def fail_constructor(*args, **kwargs):
        raise ValueError("simulated invalid companion configuration")

    monkeypatch.setattr("tingmu.companion.CompanionAgent", fail_constructor)
    bridge._v4_send_chat("keep my question")
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    saved = bridge.conversations.load(session_id)["messages"]
    assert [m["content"] for m in saved] == ["keep my question", ""]
    assert saved[-1]["status"] == "error"
    assert bridge._messages[-1]["status"] == "error"
    assert not bridge.state["streaming"]


def test_tutor_configuration_failure_does_not_create_pending_message(bridge, monkeypatch):
    before = bridge.conversations.load(bridge._active_session_id)["messages"]

    def fail_setup(**kwargs):
        raise ValueError("simulated invalid tutor configuration")

    monkeypatch.setattr(bridge, "_tutor_transport", fail_setup)
    with pytest.raises(ValueError):
        bridge._v4_send_chat("question not submitted")
    assert bridge.conversations.load(bridge._active_session_id)["messages"] == before
    assert bridge._messages == before and "chat" not in bridge._workers


@pytest.mark.parametrize(("tool", "args"), [
    ("collect_current_word", {"text": "nice"}),
    ("collect_current_sentence", {"note": "must not save"}),
    ("append_learning_note", {"text": "must not append"}),
    ("organize_notebook", {}),
])
def test_cancelled_learning_tools_cannot_write(bridge, tmp_path, tool, args):
    movie = _movie(tmp_path)
    original = bridge.notebook_manager.snapshot("yue")
    executor = bridge._tool_executor(str(movie), LanguagePair(), Cue("a", 1, 2, "nice day"),
                                     None, cancelled=lambda: True)
    with pytest.raises((ValueError, RuntimeError)):
        executor(tool, args)
    assert bridge.store.entries() == []
    assert bridge.notebook_manager.snapshot("yue")["markdown"] == original["markdown"]


def test_organize_tool_passes_cancellation_through_nested_model_call(bridge, tmp_path):
    movie = _movie(tmp_path)
    cue = Cue("a", 1, 2, "nice day")
    bridge.store.save_word(str(movie), cue, Token("nice"), LanguagePair())
    before = bridge.notebook_manager.sync("yue")
    stopped = threading.Event()

    class Tutor:
        provider = "mock"

        def _complete(self, prompt, payload, schema):
            stopped.set()  # User clicks Stop while this nested response is arriving.
            return json.dumps({"topics": [{"title": "新主题", "entry_ids":
                                          [e["id"] for e in payload["entries"]]}]})

    executor = bridge._tool_executor(str(movie), LanguagePair(), cue, Tutor(), cancelled=stopped.is_set)
    with pytest.raises((ValueError, RuntimeError)):
        executor("organize_notebook", {})
    assert stopped.is_set()
    assert bridge.notebook_manager.snapshot("yue")["markdown"] == before["markdown"]


def test_stream_partial_is_durable_before_transport_finishes(bridge, monkeypatch, qtbot):
    started, release = threading.Event(), threading.Event()
    session_id = bridge._active_session_id

    def reply(self, *args, on_delta, **kwargs):
        on_delta("durable partial answer")
        started.set()
        release.wait(5)
        return "durable partial answer, completed"

    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", reply)
    try:
        bridge._v4_send_chat("stream question")
        qtbot.waitUntil(started.is_set)
        qtbot.waitUntil(lambda: bridge.conversations.load(session_id)["messages"][-1]["content"]
                       == "durable partial answer", timeout=2000)
        assert "chat" in bridge._workers
        assert bridge.conversations.load(session_id)["messages"][-1]["status"] == "streaming"
    finally:
        release.set()
    qtbot.waitUntil(lambda: "chat" not in bridge._workers)
    assert bridge.conversations.load(session_id)["messages"][-1]["content"] == "durable partial answer, completed"
