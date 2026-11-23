"""The optional subtitle workflow remains explicit through UI, chat, retry and publication."""
from dataclasses import replace

from tingmu.batch import PreparationQueue
from tingmu.lesson_material import lesson_counts, translated_subtitles
from tingmu.models import AppSettings, Cue, StudyDocument
from tingmu.providers import ProviderError
from tingmu.storage import Store
from tingmu.task_progress import Progress
from tingmu.translation_review import TRANSLATION_REVIEW_VERSION, lesson_digest
from test_app_actions_v5 import context
from test_quick_bridge import bridge as bridge


def test_file_selection_queues_a_frozen_workflow_without_replacing_open_lesson(bridge, tmp_path):
    movie = tmp_path / "Show S01E01.mkv"
    movie.touch()
    sidecar = tmp_path / "chosen.zh.srt"
    sidecar.touch()
    bridge.media_path = str(movie)
    assert bridge.settings["preparation_workflow"] == "classic"
    doc = StudyDocument(str(movie), bridge._settings.language, [Cue("prior", 0, 2, "original")])
    original = bridge.store.save_document(doc).read_bytes()
    bridge.setting("preparation_workflow", "subtitle_agent")
    bridge._command("importStudySubtitle", {"path": str(sidecar)})
    job, = bridge.queue.snapshot()
    assert job["settings"]["preparation_workflow"] == "subtitle_agent" and job["subtitle_path"] == str(sidecar)
    assert bridge.store.material_paths(str(movie), doc.language)[0].read_bytes() == original
    bridge.setting("preparation_workflow", "classic")
    bound = context(bridge, str(movie))
    result = bridge._request_app_action("process_videos", {"scope": "current", "mode": "prepare",
                                       "start": False, "workflow": "subtitle_agent"}, bound, None)
    assert result["job_ids"] == [job["id"]]
    assert result["jobs"][0]["workflow"] == "subtitle_agent"
    assert bridge.store.load_settings().preparation_workflow == "classic"
    bridge._command("enqueue", {"paths": [str(movie)]})
    assert [j["settings"]["preparation_workflow"] for j in bridge.queue.snapshot()] == ["subtitle_agent", "classic"]


def test_evidence_changes_queue_identity_and_retry_uses_current_text_permission(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    movie = tmp_path / "Show S01E01 (1080p).mkv"
    movie.touch()
    settings = AppSettings(preparation_workflow="subtitle_agent", text_models_enabled=False, cloud_text_enabled=False)
    store.save_settings(settings)
    queue = PreparationQueue(store)
    old, = queue.enqueue([movie], settings)
    (tmp_path / "Show S01E01.zh.srt").write_text("sidecar")
    new, = queue.enqueue([movie], settings)
    assert new["id"] != old["id"] and not queue.enqueue([movie], settings)
    calls = []

    def prepare(path, active, store, **kwargs):
        calls.append((active.text_models_enabled, active.cloud_text_enabled))
        kwargs["progress"](Progress("decision", stage="读取原字幕", decision="chosen",
                                    evidence_notes=["bitmap skipped"], evidence_counts=[2, 2, 2]))
        doc = StudyDocument(str(path), active.language, [Cue("one", 1, 2, "original")],
                            metadata={"subtitle_plan": {"identity": "test"}, "stage": "ready"})
        if not active.text_models_enabled:
            doc.metadata.update(stage="partial", lesson_status="needs_teaching")
            store.save_document(doc)
            raise ProviderError("Configure model.")
        store.save_document(doc)
        return doc

    monkeypatch.setattr("tingmu.batch.prepare_media", prepare)
    queue.run(job_ids=[new["id"]])
    queue = PreparationQueue(store)
    interrupted = queue.snapshot()[1]
    assert interrupted["status"] == "failed" and interrupted["needs_teaching"]
    assert interrupted["decision"] == "chosen" and interrupted["evidence_counts"] == [2, 2, 2]
    store.save_settings(replace(settings, text_models_enabled=True))
    queue.retry(new["id"])
    queue.run(job_ids=[new["id"]])
    assert calls == [(False, False), (True, False)]
    assert [row["status"] for row in queue.snapshot()] == ["queued", "completed"]


def test_supplied_translation_exports_and_disappears_if_association_changes(tmp_path):
    store = Store(tmp_path)
    cue = Cue("line", 1, 3, "Ready?", translation="准备好了吗？", explanation="A question.")
    doc = StudyDocument(str(tmp_path / "clip.mkv"), AppSettings().language, [cue],
                        metadata={"stage": "ready", "translation_review": {"version": TRANSLATION_REVIEW_VERSION,
                                  "provided": {cue.id: lesson_digest(cue, [])}}})
    filename = store.save_document(doc).with_suffix(".translation.srt")
    assert filename.exists() and lesson_counts(doc)["verified"] == 0
    translated, = translated_subtitles(doc)
    assert (translated.text, translated.start, translated.end) == (cue.translation, 1, 3)
    cue.text = "Welcome!"
    store.save_document(doc)
    assert not filename.exists()
