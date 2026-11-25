"""Exercise reviewed translations through the desktop controls and workspace files."""
from tingmu.models import Cue, StudyDocument
from tingmu.translation_review import TRANSLATION_REVIEW_VERSION, lesson_digest
from test_player_sources import visual
from test_qml_v4 import find, quick as quick
from test_qml_v5 import click


def load_lesson(bridge, *, reviewed):
    bridge.timer.stop()
    bridge.setting("source_language", "fr")
    bridge.setting("target_language", "es")
    media = bridge.store.data_dir / "media" / "pelicula.mkv"
    bridge.media_path = str(media)
    cue = Cue("line", 0, 4, "Hola. Adiós.", translation="Salut. Au revoir.",
              explanation="Deux salutations.", segmented_texts=["Hola. ", "Adiós."])
    parts = ["Salut.", "Au revoir."]
    metadata = {"translation_review": {
        "version": TRANSLATION_REVIEW_VERSION, "verified": {"line": lesson_digest(cue, parts)},
        "segments": {"line": parts},
    }} if reviewed else {}
    metadata["stage"] = "ready"
    doc = StudyDocument(str(media), bridge._settings.language, [cue], metadata=metadata)
    path = bridge.store.save_document(doc)
    bridge._load_document()
    return doc, path.with_suffix(".translation.srt")


def open_caption_settings(window, qtbot):
    player = find(window, "playerModule").property("loadedItem")
    click(window, find(window, "openPlayerSettings"))
    qtbot.waitUntil(lambda: player.property("settingsOpen"))
    click(window, visual(window, "playerSettingsTab2"))
    qtbot.waitUntil(lambda: player.property("settingsTab") == 2)


def test_reviewed_segments_can_play_bilingual_or_translation_only(quick, qtbot):
    _, bridge, engine, window = quick
    doc, canonical = load_lesson(bridge, reviewed=True)
    open_caption_settings(window, qtbot)
    layer = find(window, "playbackCaptionLayer")
    toggle = visual(window, "showTranslationTrack")
    assert bridge.lessonModel["language"] in toggle.property("text")
    click(window, toggle)
    assert layer.property("lines").toVariant() == ["Hola. ", "Salut."]
    click(window, visual(window, "showLearningTrack"))
    assert layer.property("lines").toVariant() == ["Salut."]
    bridge._sync_cue(3500, force=True)
    assert layer.property("lines").toVariant() == ["Au revoir."]
    bridge.command("exportTranslation", {"path": "pelicula.fr.srt"})
    exported = bridge.store.data_dir / "exports" / "pelicula.fr.srt"
    assert exported.read_bytes() == canonical.read_bytes()
    contents = exported.read_text(encoding="utf-8-sig")
    assert "Salut." in contents and "Au revoir." in contents and "Hola" not in contents
    doc.cues[0].text = "Buenos días."
    doc.cues[0].segmented_texts = []
    bridge.store.save_document(doc, replace_existing=True)
    bridge._load_document()
    assert not canonical.exists()
    assert not bridge.activeCue["translationVerified"]
    assert layer.property("lines").toVariant() == []
    assert engine._tingmu_warnings == []


def test_legacy_draft_opens_text_model_settings(quick, qtbot):
    _, bridge, engine, window = quick
    assert not bridge.cues
    bridge.setting("text_models_enabled", False)
    _, canonical = load_lesson(bridge, reviewed=False)
    assert bridge.state["lessonCounts"] == {"total": 1, "taught": 1, "verified": 0}
    assert not canonical.exists()
    open_caption_settings(window, qtbot)
    click(window, visual(window, "showTranslationTrack"))
    assert find(window, "playbackCaptionLayer").property("lines").toVariant() == ["Hola. "]
    button = visual(window, "prepareLessonButton")
    assert "配置" in button.property("text")
    click(window, button)
    setup = find(window, "workspaceSetup")
    qtbot.waitUntil(lambda: setup.property("visible") and setup.property("section") == "text")
    assert engine._tingmu_warnings == []


def test_short_caption_exports_match_configured_playback_while_document_keeps_acoustic_time(quick):
    from tingmu.media import load_subtitles

    _, bridge, _, _ = quick
    bridge.setting("subtitle_min_ms", 900)
    doc, translation_file = load_lesson(bridge, reviewed=True)
    first = doc.cues[0]
    first.end = .2  # A legacy verified split cannot be shown sequentially here.
    next_cue = Cue("next", 3.2, 4.5, "Buenas.", translation="Bonjour.", explanation="Salutation.")
    doc.cues.append(next_cue)
    doc.metadata["translation_review"]["verified"][next_cue.id] = lesson_digest(next_cue, [])
    source_file = bridge.store.save_document(doc, replace_existing=True).with_suffix(".srt")
    bridge._load_document()
    bridge._sync_cue(899, force=True)
    assert bridge.activeCue["id"] == first.id
    assert bridge.activeCue["sourceEnd"] == .2
    assert bridge.activeCue["displayEnd"] == .9
    assert bridge.activeCue["translationVerified"]
    bridge._sync_cue(900, force=True)
    assert not bridge.activeCue
    for command, path, text in (("exportSubtitle", source_file, first.text),
                                ("exportTranslation", translation_file, first.translation)):
        bridge.command(command, {"path": path.name})
        exported = bridge.store.data_dir / "exports" / path.name
        assert exported.read_bytes() == path.read_bytes()
        saved, following = load_subtitles(path)
        assert (saved.text, saved.start, saved.end, following.start) == (text, 0, .9, 3.2)
    assert bridge.store.load_document(doc.media_path, doc.language).cues[0] == first
