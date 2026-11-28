import json

import tingmu.batch as batch
from tingmu.models import AppSettings, Cue, StudyDocument
from tingmu.storage import Store


def test_concurrency_changes_reuse_legacy_queue_and_apply_on_resume(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    media = tmp_path / "film.mkv"
    media.touch()
    settings = AppSettings(llm_high_concurrency=False, llm_max_concurrency=7)
    store.save_settings(settings)
    queue = batch.PreparationQueue(store)
    job = queue.enqueue([media], settings)[0]
    saved = json.loads(queue.path.read_bytes())
    for key in ("llm_high_concurrency", "llm_max_concurrency"):
        del saved["jobs"][0]["settings"][key]
    queue.path.write_text(json.dumps(saved), encoding="utf-8")
    queue = batch.PreparationQueue(store)
    calls = []

    def prepare(path, active, store, **kwargs):
        calls.append(active.llm_workers)
        if len(calls) == 1:
            queue.pause()
            raise InterruptedError()
        return StudyDocument(str(path), active.language, [Cue("one", 0, 1, "hello")])

    monkeypatch.setattr(batch, "prepare_media", prepare)
    assert queue.run()[0]["status"] == "paused"
    settings.llm_high_concurrency = True
    store.save_settings(settings)
    assert queue.enqueue([media], settings) == []
    resumed = queue.run()
    assert [(j["id"], j["status"]) for j in resumed] == [(job["id"], "completed")]
    assert calls == [1, 7]


def test_explicit_enqueue_concurrency_in_a_store_without_saved_settings(tmp_path, monkeypatch):
    queue = batch.PreparationQueue(Store(tmp_path / "data"))
    media = tmp_path / "film.mkv"
    media.touch()
    queue.enqueue([media], AppSettings(llm_high_concurrency=False))

    def prepare(path, settings, *_args, **_kwargs):
        assert settings.llm_workers == 1
        return StudyDocument(str(path), settings.language, [Cue("a", 0, 1, "hello")])

    monkeypatch.setattr(batch, "prepare_media", prepare)
    assert queue.run()[0]["status"] == "completed"


def test_queue_persists_skips_complete_and_recovers_interrupted_job(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    videos = [tmp_path / f"{i}.mkv" for i in range(3)]
    for video in videos:
        video.write_bytes(b"media")
    queue = batch.PreparationQueue(store)
    settings = AppSettings(review_enabled=False)
    assert len(queue.enqueue(videos, settings)) == 3
    assert queue.enqueue(videos, settings) == []
    calls = []

    def prepare(path, settings, store, **kwargs):
        calls.append(path.name)
        document = StudyDocument(str(path), settings.language, [Cue("a", 1, 2, "hello")])
        store.save_document(document)
        kwargs["progress"]("测试检查点")
        if len(calls) == 1:
            queue.pause()
        return document

    monkeypatch.setattr(batch, "prepare_media", prepare)
    queue.run()
    assert calls == ["0.mkv"]
    saved = json.loads(queue.path.read_text("utf-8"))
    saved["jobs"][1]["status"] = "running"
    queue.path.write_text(json.dumps(saved), encoding="utf-8")
    recovered = batch.PreparationQueue(store)
    assert recovered.snapshot()[1]["status"] == "paused"
    recovered.run()
    assert calls == ["0.mkv", "1.mkv", "2.mkv"]
    assert all(j["status"] == "completed" for j in recovered.snapshot())
    assert recovered.pending_count == 0


def test_failed_file_does_not_discard_next_and_retry_is_explicit(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    paths = [tmp_path / "bad.mkv", tmp_path / "good.mkv"]
    for path in paths:
        path.touch()
    queue = batch.PreparationQueue(store)
    queue.enqueue(paths, AppSettings())
    fail = True

    def prepare(path, settings, *_args, **_kwargs):
        if path.name == "bad.mkv" and fail:
            raise ValueError("not a valid media file")
        return StudyDocument(str(path), settings.language, [Cue("one", 0, 2, "ok")])

    monkeypatch.setattr(batch, "prepare_media", prepare)
    queue.run()
    assert [j["status"] for j in queue.snapshot()] == ["failed", "completed"]
    assert queue.snapshot()[0]["error"] == "ValueError"
    fail = False
    queue.retry_failed()
    queue.run()
    assert all(j["status"] == "completed" for j in queue.snapshot())
    queue.clear_completed()
    assert queue.snapshot() == []


def test_failed_job_retry_uses_updated_text_model_assignment(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    movie = tmp_path / "film.mkv"
    movie.touch()
    original = AppSettings(review_profile_id="", review_provider="openai", review_model="gpt-6-astra")
    store.save_settings(original)
    queue = batch.PreparationQueue(store)
    job = queue.enqueue([movie], original)[0]

    def fail(*_args, **_kwargs):
        raise ValueError("temporary provider failure")

    monkeypatch.setattr(batch, "prepare_media", fail)
    assert queue.run()[0]["status"] == "failed"

    updated = AppSettings(review_profile_id="deepseek-profile", review_provider="deepseek",
                           review_model="deepseek-v4-pro")
    store.save_settings(updated)
    queue.retry(job["id"])
    retried = queue.snapshot()[0]
    assert retried["settings"]["review_profile_id"] == "deepseek-profile"
    assert retried["settings"]["review_provider"] == "deepseek"
    assert retried["settings"]["review_model"] == "deepseek-v4-pro"

    received = []
    monkeypatch.setattr(
        batch, "prepare_media",
        lambda path, settings, *_args, **_kwargs: (
            received.append(settings) or StudyDocument(str(path), settings.language, [Cue("a", 0, 1, "hello")])
        ),
    )
    assert queue.run()[0]["status"] == "completed"
    assert received[0].review_profile_id == "deepseek-profile"
    assert received[0].review_provider == "deepseek"
    assert received[0].review_model == "deepseek-v4-pro"


def test_completed_video_can_be_enqueued_after_sidecar_changed(tmp_path, monkeypatch):
    video = tmp_path / "movie.mkv"
    video.touch()
    subtitle = tmp_path / "movie.yue.srt"
    subtitle.write_text("first", encoding="utf-8")
    store = Store(tmp_path / "data")
    queue = batch.PreparationQueue(store)
    monkeypatch.setattr(batch, "prepare_media", lambda path, settings, *a, **k:
                        StudyDocument(str(path), settings.language, [Cue("a", 0, 1, "test")]))
    queue.enqueue([video], AppSettings())
    queue.run()
    assert queue.enqueue([video], AppSettings()) == []
    subtitle.write_text("updated sidecar", encoding="utf-8")
    assert len(queue.enqueue([video], AppSettings())) == 1


def test_validation_failure_explains_stage_retains_progress_and_logs_safely(tmp_path, monkeypatch):
    from tingmu.agent import AnnotationValidationError
    from tingmu.diagnostics import AppLog

    store = Store(tmp_path / "data")
    video = tmp_path / "film.mkv"
    video.touch()
    queue = batch.PreparationQueue(store)
    job = queue.enqueue([video], AppSettings())[0]
    log = AppLog(store.data_dir)

    def fail(path, settings, store, **kwargs):
        store.save_document(StudyDocument(str(path), settings.language,
            [Cue("one", 0, 1, "private subtitle"), Cue("two", 1, 2, "other private text")],
            metadata={"stage": "partial", "review_stage": "partial", "reviewed_count": 1}))
        raise AnnotationValidationError("分句必须逐字重构复核正文。")

    monkeypatch.setattr(batch, "prepare_media", fail)
    queue.run(on_error=lambda exc: log.exception(exc, "queue_error"))
    failed = queue.snapshot()[0]
    assert failed["error"] == "AnnotationValidationError"
    assert "逐字重构" in failed["error_message"]
    assert "文本复核" in failed["progress"]
    assert (failed["completed_cues"], failed["total_cues"]) == (1, 2)
    logged = log.path.read_text(encoding="utf-8")
    assert "queue_error" in logged and "AnnotationValidationError" in logged
    assert "private" not in logged and "逐字重构" not in logged
    queue.retry(job["id"])
    assert queue.snapshot()[0]["error_message"] == ""
    log.close()


def test_queue_does_not_expose_untrusted_exception_payload(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    video = tmp_path / "film.mkv"
    video.touch()
    queue = batch.PreparationQueue(store)
    queue.enqueue([video], AppSettings())

    def fail(*args, **kwargs):
        raise ValueError("provider payload contains PRIVATE_INFORMATION")

    monkeypatch.setattr(batch, "prepare_media", fail)
    updates = []
    queue.run(updates.append)
    assert "PRIVATE_INFORMATION" not in json.dumps(queue.snapshot()) + str(updates)
    assert "开发者日志" in queue.snapshot()[0]["error_message"]


def test_review_initialization_failure_keeps_review_checkpoint_counts():
    from tingmu.providers import ProviderError
    document = StudyDocument("film.mkv", AppSettings().language, [Cue("a", 0, 1, "source")],
        metadata={"stage": "reviewing", "review_stage": "running", "reviewed_count": 1})
    details = batch._failure_details(ProviderError("所选模型配置不存在，请重新选择。"), document)
    assert "文本复核" in details["progress"]
    assert (details["completed_cues"], details["total_cues"]) == (1, 1)


def test_selected_job_run_keeps_other_pending_and_continues_after_selected_failure(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    queue = batch.PreparationQueue(store)
    paths = [tmp_path / name for name in ("unrequested.mkv", "bad.mkv", "good.mkv")]
    for path in paths:
        path.touch()
    jobs = queue.enqueue(paths, AppSettings())
    calls = []
    def prepare(path, settings, *args, **kwargs):
        calls.append(path.name)
        if path.name == "bad.mkv":
            raise ValueError("synthetic failure")
        return StudyDocument(str(path), settings.language, [Cue("a", 0, 1, "ok")])
    monkeypatch.setattr(batch, "prepare_media", prepare)
    queue.run(job_ids=[])
    assert calls == []
    queue.run(job_ids=[jobs[1]["id"], jobs[2]["id"]])
    assert calls == ["bad.mkv", "good.mkv"]
    assert [j["status"] for j in queue.snapshot()] == ["queued", "failed", "completed"]
    queue.run()
    assert calls == ["bad.mkv", "good.mkv", "unrequested.mkv"]
