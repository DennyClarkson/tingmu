from types import SimpleNamespace

from PySide6.QtCore import QMetaObject, Qt, Q_ARG

from tingmu.models import Cue, LanguagePair, StudyDocument
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
