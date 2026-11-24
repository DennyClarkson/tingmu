"""Drop-folder discovery is stable, asynchronous, durable, and permission bounded."""

from dataclasses import asdict
import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from tingmu.batch import PreparationQueue
from tingmu.library import MediaLibrary
from tingmu.media_inbox import InboxScanner, preparation_block_reason
from tingmu.models import AppSettings, Cue, StudyDocument
from tingmu.quick.bridge import Bridge
from tingmu.storage import Store, media_fingerprint


def test_recursive_discovery_waits_for_media_and_sidecar_quiet_period(tmp_path):
    folder = tmp_path / "media"
    nested = folder / "series"
    nested.mkdir(parents=True)
    media = nested / "episode.MKV"
    media.write_bytes(b"first")
    scanner = InboxScanner(folder, quiet_seconds=3)
    assert scanner.scan(now=0)["waiting"] == [str(media)]
    assert not scanner.scan(now=2)["ready"]
    media.write_bytes(b"copy still growing")
    assert not scanner.scan(now=3)["ready"]
    sidecar = media.with_suffix(".srt")
    sidecar.write_text("subtitle")
    assert not scanner.scan(now=6)["ready"]
    assert list(scanner.scan(now=9)["ready"]) == [str(media)]
    media.unlink()
    assert scanner.scan(now=10)["present"] == []


def test_scanner_ignores_symlinks_and_empty_media(tmp_path):
    folder = tmp_path / "media"
    folder.mkdir()
    (folder / "empty.mp4").touch()
    elsewhere = tmp_path / "external"
    elsewhere.mkdir()
    (elsewhere / "outside.mp4").write_bytes(b"a")
    try:
        (folder / "link").symlink_to(elsewhere, target_is_directory=True)
    except OSError:
        pass  # Creating Windows symlinks can require developer mode.
    scanner = InboxScanner(folder, quiet_seconds=0)
    assert scanner.scan(now=0)["ready"] == {}
    assert scanner.scan(now=1)["present"] == [str(folder / "empty.mp4")]


def test_unchanged_inbox_material_is_not_deserialized_every_two_seconds(tmp_path, monkeypatch):
    store = Store(tmp_path)
    video = tmp_path / "episode.mp4"
    video.write_bytes(b"first")
    media = str(video)
    pair = AppSettings().language
    doc = StudyDocument(media, pair, [Cue("one", 1, 2, "word")], metadata={"stage": "ready"})
    path = store.save_document(doc)
    scanner = InboxScanner(tmp_path)
    reads = []
    load_document = store.load_document

    def observe(media, language):
        reads.append(media)
        return load_document(media, language)

    def stage():
        return scanner.document_stages(store, {media: media_fingerprint(media)}, pair, lambda: False)[media]

    monkeypatch.setattr(store, "load_document", observe)
    assert stage() == stage() == "ready"
    assert reads == [media]
    doc.metadata["stage"] = "transcribed"
    store.save_document(doc)
    assert stage() == stage() == "transcribed"
    assert len(reads) == 2
    # An interrupted publish still needs the normal recovery path on a cache hit.
    path.with_suffix(".pending").touch()
    assert stage() == "transcribed" and not path.with_suffix(".pending").exists()
    assert len(reads) == 3
    video.write_bytes(b"replacement")
    assert stage() == ""
    path.unlink()
    assert stage() == ""


@pytest.mark.skipif(os.name != "nt", reason="Windows writer sharing contract")
def test_copy_handle_must_close_even_when_size_stops_growing(tmp_path):
    path = tmp_path / "copy.mp4"
    scanner = InboxScanner(tmp_path, quiet_seconds=0)
    with path.open("wb") as stream:
        stream.write(b"content")
        stream.flush()
        assert not scanner.scan(now=0)["ready"]
        assert not scanner.scan(now=10)["ready"]
    assert str(path) in scanner.scan(now=11)["ready"]


def test_inbox_index_survives_existing_index_restart_and_user_removal(tmp_path):
    store = Store(tmp_path / "workspace")
    library = MediaLibrary(store)
    external = tmp_path / "reference.mp4"
    external.write_bytes(b"original")
    library.add([external])
    library.media_dir.mkdir()
    media = library.media_dir / "drop.mp4"
    media.write_bytes(b"new")
    ready = {str(media): media_fingerprint(media)}
    library = MediaLibrary(store)
    assert library.reconcile_inbox(ready, ready)[0] == [str(media)]
    assert str(media) in MediaLibrary(store).automatic
    library.remove([media])
    assert media.exists()
    library = MediaLibrary(store)
    assert not library.reconcile_inbox(ready, ready)[0]
    media.write_bytes(b"replacement")
    ready = {str(media): media_fingerprint(media)}
    assert library.reconcile_inbox(ready, ready)[0] == [str(media)]
    media.unlink()
    library.reconcile_inbox({}, {})
    assert library.paths == [str(external)]


def test_preflight_missing_models_and_explicit_remote_consent(tmp_path, monkeypatch):
    media = tmp_path / "episode.mp4"
    media.write_bytes(b"sample")
    from tingmu.workspace_models import ModelReadiness
    monkeypatch.setattr("tingmu.workspace_models.model_readiness",
                        lambda _: ModelReadiness(False, "model_missing", "尚未检测到完整模型"))
    settings = AppSettings()
    assert "等待安装" in preparation_block_reason(settings, [media])
    settings.asr_backend = "external"
    assert "明确允许" in preparation_block_reason(settings, [media])
    settings.allow_audio_upload = True
    assert preparation_block_reason(settings, [media]) == ""
    settings.asr_backend = "local"
    media.with_suffix(".srt").write_text("subtitle")
    assert preparation_block_reason(settings, [media]) == ""


@pytest.mark.parametrize(("model", "language", "supported"), [
    ("large-v3-turbo", "eo", False), ("large-v3-turbo", "ar-EG", True),
    ("qwen3-asr-0.6b", "ar-EG", False), ("qwen3-asr-0.6b", "fr-CA", True),
    ("small", "yue", False), ("large-v3", "yue", True),
])
def test_inbox_language_gate_matches_selected_model(tmp_path, monkeypatch, model, language, supported):
    media = tmp_path / "lesson.mp4"
    media.write_bytes(b"media")
    monkeypatch.setattr("tingmu.model_advisor.probe_hardware", lambda: pytest.fail("unexpected hardware probe"))
    from tingmu.workspace_models import ModelReadiness
    monkeypatch.setattr("tingmu.workspace_models.model_readiness",
                        lambda _: ModelReadiness(True, "ready", "已就绪"))
    settings = AppSettings(source_language="sw", target_language=language, asr_model=model)
    reason = preparation_block_reason(settings, [media])
    if supported:
        assert reason == ""
        return
    assert language in reason and "字幕" in reason and "API" in reason
    settings.asr_backend = "local_api"
    assert preparation_block_reason(settings, [media]) == ""
    settings.asr_backend = "external"
    assert "明确允许" in preparation_block_reason(settings, [media])
    settings.allow_audio_upload = True
    assert preparation_block_reason(settings, [media]) == ""


@pytest.fixture
def bridge(qapp, tmp_path):
    obj = Bridge(Store(tmp_path / "workspace"))
    obj.inbox_timer.stop()
    yield obj
    obj.shutdown()


def inbox_result(bridge, media, stage="", blocked=""):
    return {"ready": {str(media): media_fingerprint(media)}, "present": [str(media)],
            "waiting": [], "error": "", "settings": asdict(bridge._settings),
            "documents": {str(media): stage}, "blocked": {str(media): blocked}}


def test_unsupported_target_waits_for_matching_sidecar_then_enqueues(bridge, qtbot, monkeypatch):
    qtbot.waitUntil(lambda: bridge._inbox_thread is not None and not bridge._inbox_thread.is_alive()
                    and not bridge.state["mediaInbox"]["scanning"])
    bridge._settings = AppSettings(source_language="fi", target_language="eo", auto_prepare_media=True)
    bridge._inbox_scanner.quiet_seconds = 0
    media = bridge.catalog.media_dir / "lesson.mp4"
    media.write_bytes(b"sample")
    media.with_suffix(".de.srt").write_text("unrelated subtitles")
    starts = []
    monkeypatch.setattr(bridge, "_start_job", lambda *args: starts.append(args[0]))
    bridge._scan_media_inbox()
    qtbot.waitUntil(lambda: bool(bridge.state["mediaInbox"]["blocked"]))
    assert bridge.catalog.paths == [str(media)]
    assert not starts and not bridge.queue.snapshot()
    media.with_suffix(".eo.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nSaluton!\n")
    bridge._scan_media_inbox()
    qtbot.waitUntil(lambda: starts == ["queue"])
    assert bridge.queue.snapshot()[0]["subtitle_path"] == str(media.with_suffix(".eo.srt"))


def test_automatic_jobs_respect_opt_in_prepared_failed_and_unrelated_jobs(bridge, tmp_path, monkeypatch):
    bridge.catalog.media_dir.mkdir(exist_ok=True)
    media = bridge.catalog.media_dir / "new.mp4"
    media.write_bytes(b"sample")
    starts = []
    monkeypatch.setattr(bridge, "_start_job", lambda *args: starts.append(args))
    bridge._inbox_completed(inbox_result(bridge, media))
    assert bridge.catalog.paths == [str(media)]
    assert not starts and not bridge.queue.snapshot()
    bridge._settings.auto_prepare_media = True
    bridge._inbox_completed(inbox_result(bridge, media, blocked="等待安装"))
    assert bridge.state["mediaInbox"]["blocked"] == "等待安装"
    assert not starts
    bridge._inbox_completed(inbox_result(bridge, media, stage="ready"))
    assert not starts
    unrelated = tmp_path / "manual.mp4"
    unrelated.write_bytes(b"sample")
    manual = bridge.queue.enqueue([unrelated], bridge._settings)[0]
    bridge._inbox_completed(inbox_result(bridge, media))
    auto = bridge.queue.snapshot()[-1]
    assert auto["origin"] == "media-inbox" and auto["allow_model_downloads"] is False
    assert len(starts) == 1
    called = {}
    monkeypatch.setattr(bridge.queue, "run", lambda *args, **kwargs: called.update(kwargs))
    starts[0][1](lambda _: None, lambda: False)
    assert called["job_ids"] == [auto["id"]] and manual["id"] not in called["job_ids"]
    assert called["allow_model_downloads"] is False
    bridge.queue._jobs[-1]["status"] = "failed"
    bridge._inbox_completed(inbox_result(bridge, media))
    assert len(starts) == 1


def test_automatic_queue_restart_pause_and_removal(bridge, monkeypatch):
    bridge._settings.auto_prepare_media = True
    bridge.catalog.media_dir.mkdir(exist_ok=True)
    media = bridge.catalog.media_dir / "resume.mp4"
    media.write_bytes(b"sample")
    starts = []
    monkeypatch.setattr(bridge, "_start_job", lambda *args: starts.append(args))
    bridge._inbox_completed(inbox_result(bridge, media))
    bridge.queue._jobs[-1]["status"] = "running"
    bridge.queue._save()
    bridge.queue = PreparationQueue(bridge.store)
    assert bridge.queue.snapshot()[0]["status"] == "paused"
    bridge._inbox_completed(inbox_result(bridge, media))
    assert len(starts) == 2
    bridge.queue.pause_automatic()
    bridge._inbox_completed(inbox_result(bridge, media))
    assert len(starts) == 2
    media.unlink()
    result = inbox_result(bridge, media)
    result.update(ready={}, present=[], documents={}, blocked={})
    bridge._inbox_completed(result)
    assert not bridge.catalog.paths and not bridge.queue.snapshot()


def test_workspace_copy_rejoins_automatic_preparation(bridge, tmp_path, monkeypatch):
    bridge._settings.auto_prepare_media = True
    original = tmp_path / "episode.mp4"
    original.write_bytes(b"media")
    started = []

    def start_job(key, work, complete):
        if key == "media-copy":
            complete(work(lambda _: None, lambda: False))
        else:
            started.append(key)

    monkeypatch.setattr(bridge, "_start_job", start_job)
    bridge._copy_media([original])
    copied, = bridge.catalog.paths
    scanner = InboxScanner(bridge.catalog.media_dir, quiet_seconds=0)

    def scan():
        result = scanner.scan()
        bridge._inbox_completed({**result, "settings": asdict(bridge._settings),
                                 "documents": {}, "blocked": {}})

    scan()
    assert started == ["queue"]
    assert MediaLibrary(bridge.store).automatic == {copied}
    bridge.catalog.remove([copied])
    scan()
    assert not bridge.catalog.paths
    bridge._copy_media([copied])
    scan()
    assert bridge.catalog.automatic == {copied}
    assert len(bridge.queue.snapshot()) == 1


@pytest.mark.parametrize("resume", ["queue", "setting"])
def test_explicit_resume_restarts_folder_after_queue_pause(bridge, monkeypatch, resume):
    bridge.setting("auto_prepare_media", True)
    folder = bridge.catalog.media_dir
    folder.mkdir(exist_ok=True)
    first, second = folder / "first.mp4", folder / "second.mp4"
    first.write_bytes(b"episode one")
    scanner = InboxScanner(folder, quiet_seconds=0)
    started = []
    monkeypatch.setattr(bridge, "_start_job", lambda key, *args: started.append(key))

    def scan():
        bridge._inbox_completed({**scanner.scan(), "settings": asdict(bridge._settings),
                                 "documents": {}, "blocked": {}})

    scan()
    bridge._command("pauseQueue", {})
    second.write_bytes(b"episode two")
    scan()
    assert started == ["queue"]
    assert "已暂停" in bridge.state["mediaInbox"]["blocked"]
    assert bridge.queue.snapshot()[0]["auto_paused"]
    if resume == "queue":
        bridge._command("startQueue", {})
    else:
        bridge.setting("auto_prepare_media", False)
        bridge.setting("auto_prepare_media", True)
    scan()
    assert len(started) > 1
    assert not bridge.state["mediaInbox"]["blocked"]
    assert {job["name"] for job in bridge.queue.snapshot() if not job.get("auto_paused")} == {first.name, second.name}


def test_scans_are_off_ui_thread_and_stale_results_are_discarded(bridge, qtbot, monkeypatch):
    # Let the startup singleShot finish before installing the controlled scanner.
    qtbot.waitUntil(lambda: bridge._inbox_thread is not None and not bridge._inbox_thread.is_alive()
                    and not bridge.state["mediaInbox"]["scanning"])
    entered, release = threading.Event(), threading.Event()
    threads = []
    def scan(**kwargs):
        threads.append(threading.get_ident())
        entered.set()
        release.wait(2)
        return {"ready": {}, "present": [], "waiting": [], "error": ""}
    monkeypatch.setattr(bridge._inbox_scanner, "scan", scan)
    bridge._scan_media_inbox()
    assert entered.wait(1)
    assert "media-inbox" not in bridge._workers and not bridge.state["busy"]
    bridge._settings.level = "advanced"
    release.set()
    qtbot.waitUntil(lambda: not bridge.state["mediaInbox"]["scanning"])
    assert threads[0] != threading.get_ident()
    bridge._closed = True
    before = dict(bridge.state)
    bridge._inbox_completed({"failure": "old workspace"})
    assert bridge.state == before
    bridge._closed = False


def test_idle_inbox_polls_do_not_invalidate_the_player(bridge, qtbot, monkeypatch):
    qtbot.waitUntil(lambda: bridge._inbox_thread is not None and not bridge._inbox_thread.is_alive()
                    and not bridge.mediaInbox["scanning"])
    changes, inbox_changes = [], []
    bridge.stateChanged.connect(lambda: changes.append(True))
    bridge.mediaInboxChanged.connect(lambda: inbox_changes.append(True))
    monkeypatch.setattr(bridge, "_refresh_jobs", lambda: None)
    result = {"ready": {}, "present": [], "waiting": [], "error": "", "documents": {},
              "blocked": {}, "settings": asdict(bridge._settings)}
    for _ in range(3):
        bridge._set_inbox_state(scanning=True)
        bridge._inbox_completed(result)
    assert len(inbox_changes) == 6
    assert not changes


def test_auto_pipeline_is_installed_only_and_rejects_changed_file(tmp_path, monkeypatch):
    store = Store(tmp_path / "workspace")
    folder = store.data_dir / "media"
    folder.mkdir()
    media = folder / "episode.mp4"
    media.write_bytes(b"sample")
    queue = PreparationQueue(store)
    queue.enqueue([media], AppSettings(), automatic=True)
    calls = []
    def prepare(path, settings, store, **kwargs):
        calls.append(kwargs)
        return StudyDocument(str(path), settings.language, [Cue("a", 0, 1, "word")])
    monkeypatch.setattr("tingmu.batch.prepare_media", prepare)
    queue.run()
    assert calls[0]["allow_model_downloads"] is False
    queue.enqueue([media], AppSettings(level="advanced"), automatic=True)
    media.write_bytes(b"different video")
    queue.run()
    assert len(calls) == 1
    assert queue.snapshot()[-1]["status"] == "failed"
