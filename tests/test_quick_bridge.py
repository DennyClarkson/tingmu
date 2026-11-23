"""Behavior contracts for the replacement frontend and app-local media index."""

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from tingmu.library import MediaLibrary
from tingmu.models import Cue, LanguagePair, StudyDocument, Token
from tingmu.quick.bridge import Bridge
from tingmu.storage import Store


@pytest.fixture
def bridge(qapp, tmp_path):
    obj = Bridge(Store(tmp_path / "app/data"))
    yield obj
    obj.shutdown()


def test_progress_checkpoint_leaves_ui_free_and_flushes_latest_position(bridge, tmp_path, monkeypatch):
    bridge.media_path = str(tmp_path / "episode.mkv")
    current = [1500]
    monkeypatch.setattr(bridge.player, "position", lambda: current[0])
    entered, release = threading.Event(), threading.Event()
    writes = []
    save = bridge.store.save_position

    def slow_save(media, position):
        writes.append((threading.get_ident(), position))
        entered.set()
        assert release.wait(2)
        save(media, position)

    monkeypatch.setattr(bridge.store, "save_position", slow_save)
    try:
        bridge._checkpoint_position()
        assert entered.wait(1)
        assert writes[0][0] != threading.get_ident()
        current[0] = 3000
        bridge._checkpoint_position()
        assert len(writes) == 1
    finally:
        release.set()
    bridge.save_position()
    assert [position for _, position in writes] == [1500, 3000]
    bridge._checkpoint_position()
    assert bridge._position_future is None
    assert bridge.store.load_position(bridge.media_path) == 3000


def test_playback_lessons_reuse_model_status_until_settings_or_assignment_changes(bridge, monkeypatch):
    reads = []
    def text_role(settings, role):
        reads.append(role)
        return True, {"key_source": "none"}

    monkeypatch.setattr(bridge.model_registry, "text_role", text_role)
    bridge.modelsChanged.emit()
    for _ in range(10):
        assert bridge.lessonModel["configured"]
    assert reads == ["annotation"]
    bridge.settingsChanged.emit()
    assert bridge.lessonModel["configured"]
    bridge.modelsChanged.emit()
    assert bridge.lessonModel["configured"]
    assert reads == ["annotation"] * 3


def test_start_queue_retries_failed_jobs_and_single_retry_starts(bridge, tmp_path, monkeypatch):
    video = tmp_path / "retry.mkv"
    video.touch()
    job = bridge.queue.enqueue([video], bridge._settings)[0]
    bridge.queue._jobs[0].update(status="failed", error="AnnotationValidationError")
    started = []
    monkeypatch.setattr(bridge, "_start_job", lambda key, *args: started.append(key))
    bridge._command("startQueue", {})
    assert started == ["queue"]
    assert bridge.queue.snapshot()[0]["status"] == "queued"
    bridge.queue._jobs[0].update(status="failed")
    bridge._command("retryJob", {"id": job["id"]})
    assert started == ["queue", "queue"]
    assert bridge.queue.snapshot()[0]["status"] == "queued"


def test_queue_renders_safe_reason_and_legacy_validation_error(bridge, tmp_path):
    video = tmp_path / "failed.mkv"
    video.touch()
    bridge.queue.enqueue([video], bridge._settings)
    bridge.queue._jobs[0].update(status="failed", error="AnnotationValidationError")
    bridge._refresh_jobs()
    assert "未通过校验" in bridge.jobs[0]["error"]
    bridge.queue._jobs[0]["error_message"] = "分句必须逐字重构复核正文。 已完成步骤已保存。"
    bridge._refresh_jobs()
    assert "逐字重构" in bridge.jobs[0]["error"]


def test_external_files_from_multiple_folders_keep_all_generated_data_in_app(tmp_path):
    app = tmp_path / "app"
    videos = [tmp_path / "one/第一集.mkv", tmp_path / "two/第二集.mp4"]
    for path in videos:
        path.parent.mkdir()
        path.write_bytes(b"sample")
    store = Store(app / "data")
    library = MediaLibrary(store)
    assert len(library.add([videos[0].parent, videos[1]])) == 2
    assert library.add(videos) == []
    assert MediaLibrary(store).paths == [str(p.resolve()) for p in videos]
    cue = Cue("a", 1, 2, "你好")
    for video in videos:
        doc = StudyDocument(str(video), LanguagePair(), [cue])
        assert store.save_document(doc).is_relative_to(app)
        store.save_note(str(video), cue, "测试笔记", doc.language)
    assert len(store.entries()) == 2
    assert list(videos[0].parent.iterdir()) == [videos[0]]
    assert list(videos[1].parent.iterdir()) == [videos[1]]
    library.remove([videos[0]])
    assert videos[0].exists() and len(store.entries()) == 2


def test_gap_never_merges_text_or_changes_overlay_layout(bridge):
    doc = StudyDocument("video.mkv", LanguagePair(), [
        Cue("a", 1, 2, "係呀"), Cue("b", 2.3, 3, "唔係喎"),
        Cue("c", 5, 7, "長少少嘅一句")])
    bridge.saveLayout({"overlayWidth": 460, "overlayHeight": 280})
    bridge._set_document(doc)
    bridge._sync_cue(2150)
    assert bridge.activeCue["id"] == "a"
    assert bridge.activeCue["text"] == "係呀"
    bridge._sync_cue(2300)
    assert bridge.activeCue["id"] == "b"
    bridge._sync_cue(5500)
    assert bridge.layout == {"overlayWidth": 460, "overlayHeight": 280}
    bridge._sync_cue(2100)
    assert bridge.activeCue["id"] == "a"
    bridge._sync_cue(4000)
    assert not bridge.activeCue


def test_word_sentence_and_chat_identity_remain_bound_to_selected_video(bridge, tmp_path):
    media = tmp_path / "external.mkv"
    media.write_bytes(b"x")
    bridge.media_path = str(media)
    cue = Cue("a", 1, 2, "唔該", tokens=[Token("唔該", "m4 goi1", "谢谢")])
    bridge._set_document(StudyDocument(str(media), LanguagePair(), [cue]))
    feedback = []
    bridge.collectionFeedback.connect(lambda ok, text: feedback.append((ok, text)))
    bridge.command("saveWord", {"cueId": "a", "tokenIndex": 0})
    assert feedback == [(True, "已收藏「唔該」")]
    bridge.command("saveNote", {"cueId": "a", "note": "问路时使用"})
    assert {e["kind"] for e in bridge.entries} == {"word", "note"}
    assert all(e["media_path"] == str(media) for e in bridge.entries)
    assert len(bridge.store.entries(str(tmp_path / "different.mkv"))) == 0
    assert not (media.parent / "external.json").exists()


def test_position_ticks_reuse_cue_and_transport_metadata(bridge, monkeypatch):
    cues = [Cue("a", 0, 1, "早晨"), Cue("b", 1, 2, "再見")]
    bridge._set_document(StudyDocument("lesson.mkv", LanguagePair(), cues))
    assert bridge.cueCount == 2
    details_updates = []
    bridge.playbackDetailsChanged.connect(lambda: details_updates.append(bridge.playbackDetails))
    with monkeypatch.context() as patch:
        patch.setattr(bridge, "_tracks", lambda *args, **kwargs: pytest.fail("position tick rebuilt track metadata"))
        patch.setattr(bridge, "_present_cue", lambda *args: pytest.fail("position tick rebuilt the displayed cue"))
        for position in (500, 1200, 1300):
            bridge._position_changed(position)
            assert bridge.playbackPosition == bridge.playback["position"] == position
            assert bridge.activeCue["id"] == ("a" if position < 1000 else "b")
    assert not details_updates
    bridge.audio.setMuted(True)
    assert details_updates[-1]["muted"] is True


def test_multiselect_enqueue_deduplicates_and_retains_setting_snapshot(bridge, tmp_path):
    paths = []
    for name in ("one.mkv", "two.mkv"):
        path = tmp_path / name
        path.write_bytes(b"x")
        paths.append(str(path))
    bridge.command("enqueue", {"paths": paths})
    assert len(bridge.jobs) == 2
    assert all(j["status"] == "queued" and j["stage"] == "等待" for j in bridge.jobs)
    old = bridge.jobs[0]["settings"]["target_language"]
    bridge.setting("target_language", "en")
    assert all(j["settings"]["target_language"] == old for j in bridge.jobs)
    assert len(bridge.library) == 2


def test_playback_word_colors_follow_edits_and_perform_no_per_cue_io(bridge, tmp_path, monkeypatch):
    media = str(tmp_path / "lesson.mkv")
    bridge.media_path = media
    first = Cue("a", 1, 2, "Nice day.", tokens=[Token("Nice")])
    other = Cue("b", 3, 4, "How nice.", tokens=[Token("nice")])
    bridge.set_languages("zh-CN", "en")
    bridge._set_document(StudyDocument(media, bridge._settings.language, [first, other]))
    bridge.command("saveWord", {"cueId": first.id, "tokenIndex": 0})
    entry_id = bridge.entries[0]["id"]
    bridge.notebook_manager.append_note("en", "Greetings", "An example", [entry_id])
    bridge._learningUpdated.emit()
    bridge.command("deleteEntry", {"id": entry_id})
    assert bridge.wordCollectionStates(other.id, ["nice"]) == {"nice": "organized"}
    bridge.command("saveWord", {"cueId": other.id, "tokenIndex": 0, "confirm": True})
    assert bridge.wordCollectionStates(first.id, ["Nice"]) == {"Nice": "organized"}
    assert bridge.wordCollectionStates(other.id, ["Nice", "nice", "NICE"]) == {
        "Nice": "pending", "nice": "pending", "NICE": "unsaved"}

    # The same text can occur in thousands of new cues, or be selected by dragging.
    # Both companion windows share this index. Unsaved selections do not accumulate.
    from pathlib import Path
    index_size = len(bridge._word_states)
    def forbidden(*args, **kwargs):
        pytest.fail("Playback opened a database or resolved a path for a word label")
    with monkeypatch.context() as patch:
        patch.setattr(bridge.store, "_connection", forbidden)
        patch.setattr(Path, "resolve", forbidden)
        for n in range(1000):
            texts = ["Nice", f"selection {n}"]
            assert bridge.wordCollectionStates(str(n), texts) == {texts[0]: "organized", texts[1]: "unsaved"}
    assert len(bridge._word_states) == index_size
    bridge.media_path = str(tmp_path / "another.mkv")
    bridge._load_personal()
    assert bridge.wordCollectionStates(other.id, ["nice"]) == {"nice": "organized"}
    bridge.set_languages("fr", "en")
    assert bridge.wordCollectionStates(other.id, ["nice"]) == {"nice": "unsaved"}
    bridge.set_languages("zh-CN", "en")
    bridge.command("selectNotebook", {"language": "fr"})
    assert bridge.wordCollectionStates(other.id, ["nice"]) == {"nice": "organized"}


def test_pending_chat_reply_saved_to_original_media_after_user_switches(bridge, tmp_path, monkeypatch, qtbot):
    ready, release = threading.Event(), threading.Event()
    def reply(self, *args, **kwargs):
        ready.set()
        release.wait(3)
        return "原视频的回答"
    monkeypatch.setattr("tingmu.companion.CompanionAgent.reply", reply)
    first, second = str(tmp_path / "one.mkv"), str(tmp_path / "two.mkv")
    bridge.media_path = first
    bridge.command("sendChat", {"text": "这句怎么用"})
    qtbot.waitUntil(ready.is_set)
    bridge.command("summarizeNotes", {})
    assert "当前回复" in bridge.state["error"]
    assert "summary" not in bridge._workers
    bridge.media_path = second
    bridge._load_personal()
    release.set()
    qtbot.waitUntil(lambda: not bridge.state["chatBusy"])
    assert bridge.messages == []
    assert bridge.store.load_conversation(first, LanguagePair())[-1]["content"] == "原视频的回答"
    assert bridge.store.load_conversation(second, LanguagePair()) == []


def test_cloud_off_chat_blocked_and_unknown_settings_ignored(bridge):
    bridge.setting("cloud_text_enabled", False)
    bridge.command("sendChat", {"text": "你好"})
    assert "云端文本" in bridge.state["error"]
    assert not bridge._workers
    bridge.setting("data_dir", "C:/wrong")
    assert "data_dir" not in bridge.settings
    bridge.setting("subtitle_gap_ms", 9000)
    assert bridge.settings["subtitle_gap_ms"] == 1500


def test_bookmark_seek_survives_ffmpeg_loading_signal_order(bridge, qtbot):
    """Duration/seekable precede LoadedMedia; early seeks are discarded by FFmpeg."""
    from PySide6.QtMultimedia import QMediaPlayer

    class LoadingTransport:
        status = QMediaPlayer.MediaStatus.LoadingMedia
        requested = []

        def mediaStatus(self):
            return self.status

        def isSeekable(self):
            return True

        def duration(self):
            return 300000

        def setPosition(self, position):
            self.requested.append(position)

    original_player, original_update = bridge._player, bridge._update_playback
    transport = LoadingTransport()
    bridge._player = transport
    bridge._update_playback = lambda: None
    try:
        bridge._seek(180000)
        bridge._duration_changed(300000)
        assert bridge._resume_position == 180000
        assert transport.requested == []
        transport.status = QMediaPlayer.MediaStatus.LoadedMedia
        bridge._media_status(transport.status)
        assert transport.requested == []  # seek only after the load callback returns
        qtbot.waitUntil(lambda: transport.requested == [180000])
        assert bridge._resume_position is None
    finally:
        bridge._player, bridge._update_playback = original_player, original_update


def test_seek_before_media_is_ready_keeps_requested_bookmark(bridge):
    bridge._seek(180000)
    assert bridge._resume_position == 180000
    bridge._duration_changed(0)
    assert bridge._resume_position == 180000


def test_shutdown_waits_for_cancellable_local_worker_cleanup(bridge, qtbot):
    entered, cleaned = threading.Event(), threading.Event()
    def work(progress, cancelled):
        entered.set()
        while not cancelled():
            cleaned.wait(0.01)
        cleaned.set()
    bridge._start_job("local-test", work, lambda result: None)
    qtbot.waitUntil(entered.is_set)
    bridge.shutdown()
    assert cleaned.is_set()


def test_developer_diagnostics_export_stays_local_without_user_text(bridge, tmp_path):
    import json
    bridge._set_document(StudyDocument(str(tmp_path / "secret.mkv"), LanguagePair(), [
        Cue("a", 1, 2, "PRIVATE SUBTITLE TEXT", explanation="PRIVATE EXPLANATION")]))
    bridge.setting("developer_mode", True)
    bridge.refresh_diagnostics()
    assert bridge.diagnostics["logLevel"] == "DEBUG"
    assert bridge.diagnostics["privacy"]["logsIncludeContent"] is False
    assert bridge.logs
    path = bridge.export_diagnostics()
    assert path.is_relative_to(bridge.store.data_dir)
    exported = path.read_text("utf-8")
    assert "PRIVATE" not in exported
    from tingmu import __version__
    assert json.loads(exported)["runtime"]["version"] == __version__
    bridge.setting("developer_mode", False)
    assert bridge.store.load_settings().developer_mode is False
