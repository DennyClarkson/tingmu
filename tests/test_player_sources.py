from types import SimpleNamespace
import json

from PySide6.QtCore import QMetaObject, QPoint, QPointF, Qt, Q_ARG
from PySide6.QtGui import QColor, QImage
from PySide6.QtMultimedia import QVideoFrame
from PySide6.QtTest import QTest
import pytest

from tingmu.embedded_subtitles import Events
from tingmu.models import Cue, LanguagePair, StudyDocument
from tingmu.quick.main import create_qml_app
from tingmu.storage import Store
from tingmu.quick.playback_sources import DisplayTrack, PlaybackSources
from test_qml_v4 import find, invoke, quick as quick
from test_qml_v5 import click


def visual(window, name):
    items = [window.contentItem()]
    while items:
        item = items.pop()
        if item.objectName() == name:
            return item
        items.extend(item.childItems())
    raise AssertionError(name)


def test_external_captions_coexist_and_survive_media_switch(quick, tmp_path, qtbot):
    _, bridge, engine, window = quick
    media = str(bridge.store.data_dir / "media/film.mkv")
    bridge.media_path = media
    bridge.sources.open(media)
    study = StudyDocument(media, LanguagePair(), [Cue("1", 0, 3, "学习原文")])
    bridge.store.save_document(study)
    bridge._set_document(study)
    bridge._embedded.text = "内嵌原文"
    bridge._update_playback()
    path = tmp_path / "学习对照.srt"
    path.write_text("1\n00:00:00,000 --> 00:00:02,000\n第一句\n\n2\n00:00:01,000 --> 00:00:03,000\n第二句\n", "utf-8")
    bridge.command("importSubtitle", {"path": str(path)})
    qtbot.waitUntil(lambda: bridge.sources.importedName == path.name)
    captions = find(window, "playbackCaptionLayer")
    player = find(window, "playerModule").property("loadedItem")
    assert captions.property("lines").toVariant() == ["学习原文", "内嵌原文", "第一句"]
    player.setProperty("displayCaption", False)
    assert captions.property("lines").toVariant() == ["内嵌原文", "第一句"]
    player.setProperty("displayImported", False)
    assert captions.property("lines").toVariant() == ["内嵌原文"]
    bridge.sources.update_position(1000)
    assert bridge.sources.importedText == "第一句\n第二句"
    bridge.sources.update_position(2000)
    assert bridge.sources.importedText == "第二句"
    bridge.sources.update_position(3000)
    assert bridge.sources.importedText == ""
    path.unlink()
    bridge.sources.open(str(tmp_path / "other.mkv"))
    assert not bridge.sources.importedName
    bridge.sources.open(media)
    assert bridge.sources.importedName == path.name
    bridge.command("removeDisplaySubtitle", {})
    bridge.sources.open(media)
    assert not bridge.sources.importedName
    assert bridge.store.load_document(media, LanguagePair()).cues[0].text == "学习原文"
    assert engine._tingmu_warnings == []


def test_style_editor_audio_choice_and_fullscreen_clearance(quick, qtbot):
    _, bridge, engine, window = quick
    player = find(window, "playerModule").property("loadedItem")
    click(window, find(window, "openPlayerSettings"))
    qtbot.waitUntil(lambda: player.property("settingsOpen"))
    click(window, visual(window, "playerSettingsTab1"))
    outputs = bridge.sources.audioOutputs
    choice = find(window, "audioOutputChoice")
    assert choice.property("count") == len(outputs)
    for index in reversed(range(len(outputs))):
        choice.setProperty("currentIndex", index)
        assert QMetaObject.invokeMethod(choice, "activated", Qt.ConnectionType.DirectConnection, Q_ARG(int, index))
        assert bridge.store.load_settings().audio_output_id == outputs[index]["id"]
        if index:
            assert bytes(bridge.audio.device().id()).hex() == outputs[index]["id"]
    click(window, visual(window, "playerSettingsTab3"))
    editor = find(window, "captionStyleEditor")
    preset = find(editor, "captionPresetChoice")
    preset.setProperty("currentIndex", 2)
    assert QMetaObject.invokeMethod(preset, "activated", Qt.ConnectionType.DirectConnection, Q_ARG(int, 2))
    assert player.property("appearance")["box"]
    find(editor, "captionPresetName").setProperty("text", "高对比测试")
    invoke(find(editor, "saveCaptionPreset"), "clicked")
    invoke(window, "saveWorkspace")
    assert bridge.layout["preferences"]["player"]["savedStyles"]["高对比测试"]["box"]
    invoke(find(window, "playerSettingsPopup"), "close")
    invoke(player, "toggleFullscreen")
    qtbot.waitUntil(lambda: player.property("fullscreenGeometryReady"))
    full = find(window, "fullscreenWindow")
    video = find(window, "fullscreenVideoArea")
    controls = find(window, "fullscreenTransport")
    assert video.height() == full.height() - controls.height()
    player.setProperty("controlsVisible", False)
    assert video.height() == full.height() - controls.height()
    player.setProperty("reserveBar", False)
    assert video.height() == full.height()
    invoke(player, "openSettings")
    assert find(window, "playerSettingsPopup").property("parent").window() == full
    invoke(player, "toggleFullscreen")
    assert engine._tingmu_warnings == []


def test_long_overlapping_cue_keeps_original_end():
    track = DisplayTrack("x", [(0, 100, "long"), (10, 11, "one"), (60, 80, "two")])
    assert track.at(60) == "long\ntwo"
    assert track.at(99) == "long"
    assert track.at(100) == ""


def drag_caption(window, hit, delta):
    start = hit.mapToScene(QPointF(hit.width() / 2, hit.height() / 2)).toPoint()
    finish = start + delta
    QTest.mousePress(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, start)
    QTest.mouseMove(window, finish, delay=30)
    QTest.mouseRelease(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, finish)
    return hit.mapToScene(QPointF(hit.width() / 2, hit.height() / 2)).toPoint() - start


@pytest.mark.parametrize("bitmap", [False, True])
def test_drag_actual_subtitles_in_both_windows_and_persist(quick, qtbot, tmp_path, bitmap):
    _, bridge, engine, window = quick
    player = find(window, "playerModule").property("loadedItem")
    frame = QImage(640, 360, QImage.Format.Format_ARGB32)
    frame.fill(QColor("black"))
    bridge._player.videoSink().setVideoFrame(QVideoFrame(frame))
    if bitmap:
        path = tmp_path / "subtitle.png"
        frame.fill(QColor("transparent"))
        frame.setPixelColor(320, 280, QColor("white"))
        assert frame.save(str(path))
        bridge._embedded.image = path.as_uri()
        bridge._embedded.bounds = {"x": 0.4, "y": 0.74, "width": 0.2, "height": 0.08}
        bridge._update_playback()
    else:
        bridge._set_document(StudyDocument("sample.mkv", LanguagePair(), [Cue("a", 0, 1, "字幕位置")]))
    key = "bitmapPosition" if bitmap else "textPosition"
    target = "bitmapCaptionDrag" if bitmap else "textCaptionDrag"
    layer = find(window, "playbackCaptionLayer")
    qtbot.waitUntil(lambda: layer.width() > 100)
    qtbot.wait(40)
    hit = visual(window, target)
    assert hit.width() > 12 and hit.height() > 5
    displacement = QPoint(26, -31)
    assert (drag_caption(window, hit, displacement) - displacement).manhattanLength() <= 2
    initial = player.property(key).toVariant()
    invoke(player, "toggleFullscreen")
    qtbot.waitUntil(lambda: player.property("fullscreenGeometryReady"))
    player.setProperty("overlayVisible", False)
    full = find(window, "fullscreenWindow")
    point = hit.mapToItem(layer, QPointF(hit.width()/2, hit.height()/2))
    assert point.x() / layer.width() == pytest.approx(initial["x"], abs=0.01)
    assert point.y() / layer.height() == pytest.approx(initial["y"], abs=0.01)
    displacement = QPoint(-33, -28)
    assert (drag_caption(full, hit, displacement) - displacement).manhattanLength() <= 2
    invoke(player, "toggleFullscreen")
    invoke(window, "saveWorkspace")
    saved = json.loads((bridge.store.data_dir / "quick-layout.json").read_text("utf-8"))
    assert saved["preferences"]["player"][key] == player.property(key).toVariant() != initial
    _, restored, other_engine = create_qml_app(bridge.store.data_dir)
    try:
        other_player = find(other_engine.rootObjects()[0], "playerModule").property("loadedItem")
        assert other_player.property(key) == saved["preferences"]["player"][key]
        assert other_engine._tingmu_warnings == []
    finally:
        restored.shutdown()
        other_engine.deleteLater()
        qtbot.wait(20)
    assert engine._tingmu_warnings == []


def test_sentence_button_saves_source_and_keeps_remarks_in_both_windows(quick, qtbot):
    _, bridge, engine, window = quick
    media = str(bridge.store.data_dir / "media/sample.mkv")
    bridge.media_path = media
    bridge._set_state(mediaPath=media)
    source = Cue("whole", 1.5, 4, "前半句。后半句。", translation="整句译文", segmented_texts=["前半句。", "后半句。"])
    bridge._set_document(StudyDocument(media, LanguagePair(), [source]))
    bridge._sync_cue(3700)
    companion = find(window, "companionModule").property("loadedItem")
    button = find(companion, "saveSentenceButton")
    qtbot.wait(60)
    click(window, button)
    assert button.property("text") == "已收藏整句"
    assert find(companion, "sentenceCollectionFeedback").property("visible")
    existing = Store(bridge.store.data_dir)
    entry, = existing.entries(media)
    assert (entry["cue_id"], entry["text"], entry["meaning"], entry["start"]) == (
        source.id, source.text, source.translation, source.start)
    existing.save_note(media, source, "手写备注", LanguagePair())
    player = find(window, "playerModule").property("loadedItem")
    invoke(player, "toggleFullscreen")
    qtbot.waitUntil(lambda: player.property("fullscreenGeometryReady"))
    full = find(window, "fullscreenWindow")
    overlay = find(window, "overlayScroll")
    click(full, find(overlay, "saveSentenceButton"))
    qtbot.wait(30)
    saved, = existing.entries(media)
    assert saved["id"] == entry["id"] and saved["note"] == "手写备注"
    assert find(overlay, "sentenceCollectionFeedback").property("text") == "整句已加入收藏箱"
    assert saved["id"] in {row["id"] for row in bridge.notebooks["entries"]}
    assert engine._tingmu_warnings == []


def test_sentence_button_uses_current_display_only_source(quick, qtbot):
    _, bridge, engine, window = quick
    media = str(bridge.store.data_dir / "media/test.mkv")
    bridge.media_path = media
    bridge._set_state(mediaPath=media)
    bridge.sources.open(media)
    bridge.sources.install(DisplayTrack("外部.srt", [(1000, 2500, "相同台词"), (2500, 3700, "相同台词")]))
    bridge.sources.update_position(2900)
    companion = find(window, "companionModule").property("loadedItem")
    button = find(companion, "saveSentenceButton")
    qtbot.wait(40)
    click(window, button)
    entry, = bridge.store.entries(media)
    assert (entry["start"], entry["text"]) == (2.5, "相同台词")
    bridge.sources.update_position(3800)
    bridge._embedded.track = 1
    bridge._embedded._source = Events((3010, 4900), ("内嵌台词", ""))
    bridge._embedded.position(3800)
    qtbot.wait(40)
    click(window, button)
    assert [(e["text"], e["start"]) for e in bridge.store.entries(media)] == [
        ("相同台词", 2.5), ("内嵌台词", 3.01)]
    assert engine._tingmu_warnings == []


def test_preferred_output_is_restored_after_reconnection():
    def device(key):
        return SimpleNamespace(id=lambda: key, description=key.decode, isNull=lambda: False)

    speakers, headphones = device(b"speakers"), device(b"USB")
    available = [speakers, headphones]
    actual = [speakers]
    sources = SimpleNamespace(
        _wanted=b"USB".hex(),
        devices=SimpleNamespace(audioOutputs=lambda: available, defaultAudioOutput=lambda: speakers),
        output=SimpleNamespace(device=lambda: actual[0], setDevice=lambda value: actual.__setitem__(0, value)),
        outputsChanged=SimpleNamespace(emit=lambda: None),
    )
    PlaybackSources.refresh_outputs(sources)
    assert actual == [headphones]
    available.remove(headphones)
    PlaybackSources.refresh_outputs(sources)
    assert actual == [speakers]
    available.append(headphones)
    PlaybackSources.refresh_outputs(sources)
    assert actual == [headphones]
