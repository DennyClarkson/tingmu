"""Pointer and keyboard regressions against the live player, timeline and media library."""

from unittest.mock import Mock

from PySide6.QtCore import QCoreApplication, QPointF, Qt
from PySide6.QtGui import QFontMetricsF
from PySide6.QtTest import QTest

from test_qml_v4 import find, invoke, quick as quick
from tingmu.models import Cue, LanguagePair, StudyDocument, Token
from tingmu.storage import canonical_path


def scene_point(item, x_fraction=0.5, y_fraction=0.5):
    return item.mapToScene(QPointF(item.width() * x_fraction, item.height() * y_fraction)).toPoint()


def click(item):
    QTest.mouseClick(item.window(), Qt.MouseButton.LeftButton, pos=scene_point(item))


def row(view, index=0):
    delegates = sorted(
        (item for item in view.property("contentItem").childItems() if item.property("modelData") is not None),
        key=lambda item: item.y(),
    )
    return delegates[index] if index < len(delegates) else None


def test_empty_player_open_button_receives_real_click(quick, qtbot, monkeypatch):
    app, bridge, engine, window = quick
    command = Mock()
    monkeypatch.setattr(bridge, "_command", command)
    button = find(window, "emptyPlayerOpenButton")
    assert button.isVisible()
    click(button)
    command.assert_called_once_with("openFiles", {})
    qtbot.wait(app.doubleClickInterval() + 50)
    command.assert_called_once_with("openFiles", {})
    assert engine._tingmu_warnings == []


def test_player_single_and_double_clicks_and_immediate_shortcuts(quick, qtbot, monkeypatch):
    app, bridge, engine, window = quick
    command = Mock()
    monkeypatch.setattr(bridge, "_command", command)
    bridge._set_state(mediaPath="synthetic.mkv")
    player = find(window, "playerModule").property("loadedItem")
    video = find(window, "playerVideo")
    full = find(window, "fullscreenWindow")
    assert not find(window, "emptyPlayerOpenButton").isVisible()

    click(video)
    command.assert_not_called()
    qtbot.waitUntil(lambda: command.call_count == 1)
    command.assert_called_once_with("playPause", {})
    command.reset_mock()
    QTest.mouseDClick(window, Qt.MouseButton.LeftButton, pos=scene_point(video))
    qtbot.waitUntil(full.isVisible)
    qtbot.wait(app.doubleClickInterval() + 60)
    command.assert_not_called()

    # Click the film outside the companion, controls, captions and title.
    point = scene_point(video, 0.85, 0.55)
    QTest.mouseClick(full, Qt.MouseButton.LeftButton, pos=point)
    command.assert_not_called()
    qtbot.waitUntil(lambda: command.call_count == 1)
    command.assert_called_once_with("playPause", {})
    command.reset_mock()
    QTest.mouseDClick(full, Qt.MouseButton.LeftButton, pos=point)
    qtbot.waitUntil(lambda: not full.isVisible())
    qtbot.wait(app.doubleClickInterval() + 60)
    command.assert_not_called()

    window.requestActivate()
    qtbot.waitUntil(lambda: bridge.focusWindow is window)
    player.forceActiveFocus()
    QTest.keyClick(window, Qt.Key.Key_Space)
    command.assert_called_once_with("playPause", {})
    QTest.keyClick(window, Qt.Key.Key_F)
    assert full.isVisible()
    qtbot.waitUntil(lambda: bridge.focusWindow is full)
    QTest.keyClick(full, Qt.Key.Key_F)
    assert not full.isVisible()
    command.assert_called_once_with("playPause", {})
    assert engine._tingmu_warnings == []


def test_subtitle_filter_matches_all_fields_and_keeps_source_actions(quick, qtbot, monkeypatch):
    _, bridge, engine, window = quick
    bridge.timer.stop()
    bridge._set_document(StudyDocument("synthetic.mkv", LanguagePair(), [
        Cue("other", 0, 1, "Other"),
        Cue("target", 4, 5, "Walk", translation="Stroll", tokens=[Token("Walk", "Waak6")]),
        Cue("last", 8, 9, "Last"),
    ]))
    bridge._sync_cue(4200)
    search = find(window, "subtitleSearch")
    view = find(window, "subtitleList")
    for term in ("  WALK ", "sTrOlL", " WaAK6 "):
        search.setProperty("text", term)
        qtbot.waitUntil(lambda: view.property("count") == 1 and row(view) is not None)
        assert row(view).property("active")
        assert find(row(view), "correctSubtitleRow").isVisible()
        assert view.property("contentHeight") == row(view).height()
    bridge._sync_cue(8100)
    assert not row(view).property("active")
    command = Mock()
    monkeypatch.setattr(bridge, "_command", command)
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, pos=scene_point(row(view), 0.1))
    command.assert_called_once_with("seek", {"position": 4000})
    search.setProperty("text", "unknown")
    qtbot.waitUntil(lambda: view.property("count") == 0)
    assert view.property("contentHeight") <= 0
    assert find(window, "subtitleEmptyMessage").isVisible()
    clear = find(window, "clearSubtitleSearch")
    assert clear.isVisible()
    click(clear)
    qtbot.waitUntil(lambda: view.property("count") == 3)
    assert search.property("text") == ""
    search.setProperty("text", "  ")
    assert view.property("count") == 3
    assert engine._tingmu_warnings == []


def test_library_filtered_select_all_and_remove_preserve_files(quick, qtbot, tmp_path, monkeypatch):
    _, bridge, engine, window = quick
    paths = [tmp_path / name for name in ("Hidden.mkv", "Sample A.mkv", "Sample B.mkv")]
    for item in paths:
        item.write_bytes(b"synthetic index fixture")
    bridge.add_paths([str(item) for item in paths])
    hidden, first, second = map(canonical_path, paths)
    bridge._set_state(mediaPath=first)
    search = find(window, "librarySearch")
    view = find(window, "libraryList")
    select_all = find(window, "selectVisibleMedia")
    qtbot.waitUntil(lambda: bridge._library_state.library_future is None
                    and view.property("count") == 3 and row(view) is not None)
    hidden_row = row(view)
    click(find(hidden_row, "selectMediaRow"))
    assert bridge._agent_selected_media == [hidden]
    search.setProperty("text", " sAmPLe ")
    qtbot.waitUntil(lambda: view.property("count") == 2 and row(view) is not None)
    assert not select_all.property("checked")
    click(select_all)
    assert set(bridge._agent_selected_media) == {hidden, first, second}
    assert select_all.property("checked")
    click(select_all)
    assert bridge._agent_selected_media == [hidden]
    click(select_all)

    search.setProperty("text", "nothing matches")
    qtbot.waitUntil(lambda: view.property("count") == 0)
    assert view.property("contentHeight") <= 0
    assert not select_all.isEnabled()
    assert find(window, "libraryNoResults").isVisible()
    click(find(window, "clearLibrarySearch"))
    qtbot.waitUntil(lambda: view.property("count") == 3 and row(view, 1) is not None)
    assert search.property("text") == ""
    current = row(view, 1)
    marker = find(current, "currentMediaIndicator")
    assert marker.isVisible() and marker.width() == 2
    assert not find(row(view), "currentMediaIndicator").isVisible()
    remove = find(current, "removeMediaRow")
    QTest.mouseMove(window, scene_point(current))
    qtbot.waitUntil(remove.isVisible)
    assert remove.property("tip") == "仅移出列表，不删除原文件"
    command = Mock(wraps=bridge._command)
    monkeypatch.setattr(bridge, "_command", command)
    click(remove)
    qtbot.waitUntil(lambda: view.property("count") == 2)
    command.assert_any_call("removeMedia", {"paths": [first]})
    assert all(call.args[0] != "openMedia" for call in command.call_args_list)
    assert set(bridge._agent_selected_media) == {hidden, second}
    assert {item["path"] for item in bridge.library} == {hidden, second}
    assert paths[1].read_bytes() == b"synthetic index fixture"
    assert engine._tingmu_warnings == []


def test_library_status_badges_follow_live_row_status(quick, qtbot):
    _, bridge, engine, window = quick
    view = find(window, "libraryList")
    for status, tone in (("ready", "accent"), ("completed", "accent"), ("running", "accent"),
                         ("failed", "danger"), ("missing", "warning"), ("unprepared", "neutral")):
        bridge._library = [{"name": "Synthetic.mkv", "path": "Synthetic.mkv", "status": status}]
        bridge.libraryChanged.emit()
        qtbot.waitUntil(lambda: row(view) is not None and row(view).property("modelData")["status"] == status)
        delegate = row(view)
        badge = find(delegate, "mediaStatusBadge")
        assert badge.property("tone") == tone
        assert badge.property("filledAccent") is (status in {"ready", "completed"})
        assert badge.property("text")
    assert engine._tingmu_warnings == []


def test_time_display_switches_and_saves_in_both_player_windows(quick, qtbot):
    _, bridge, engine, window = quick
    bridge.timer.stop()
    bridge._set_state(mediaPath="synthetic.mkv")
    bridge._playback_details = {**bridge.playbackDetails, "duration": 185000}
    bridge.playbackDetailsChanged.emit()
    bridge._position_changed(23000)
    button = find(window, "playerTimeDisplay")
    full_button = find(window, "fullscreenTimeDisplay")
    player = find(window, "playerModule").property("loadedItem")
    assert button.property("text") == "03:05"
    click(button)
    assert button.property("text") == full_button.property("text") == "−02:42"
    invoke(window, "saveWorkspace")
    assert bridge.layout["preferences"]["player"]["remainingTime"] is True
    invoke(player, "toggleFullscreen")
    qtbot.waitUntil(lambda: player.property("fullscreenGeometryReady"))
    click(full_button)
    assert button.property("text") == full_button.property("text") == "03:05"
    invoke(window, "saveWorkspace")
    assert bridge.layout["preferences"]["player"]["remainingTime"] is False
    invoke(player, "toggleFullscreen")
    assert engine._tingmu_warnings == []


def test_speed_menu_uses_compact_width_and_keeps_selection(quick, qtbot):
    _, bridge, engine, window = quick
    speed = find(window, "playerSpeedChoice")
    popup = find(speed, "choicePopup")
    click(speed)
    qtbot.waitUntil(lambda: popup.property("opened"))
    assert popup.property("width") == 96
    assert speed.width() < popup.property("width")
    QTest.keyClick(window, Qt.Key.Key_End)
    QTest.keyClick(window, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: not popup.property("visible"))
    assert bridge.playbackDetails["rate"] == 2
    assert speed.property("displayText") == "2×"
    assert engine._tingmu_warnings == []


def test_narrow_english_search_and_queue_labels_fit_and_import_still_opens(quick, qtbot, monkeypatch):
    _, bridge, engine, window = quick
    window.resize(980, 620)
    invoke(find(window, "interfaceEnglish"), "trigger")
    bridge._set_state(mediaPath="synthetic.mkv")
    search = find(window, "subtitleSearch")
    enqueue = find(window, "enqueueSelectedMedia")
    label = QCoreApplication.translate("Tingmu", "搜索字幕")
    assert label.isascii()
    qtbot.waitUntil(lambda: search.property("placeholderText") == label)
    field_width = search.width() - search.property("leftPadding") - search.property("rightPadding")
    assert QFontMetricsF(search.property("font")).horizontalAdvance(label) <= field_width
    assert not find(enqueue, "buttonText").property("truncated")
    action = find(window, "importStudySubtitles")
    assert action.property("iconSource") and action.property("actionLabel") and action.property("tip")
    command = Mock()
    monkeypatch.setattr(bridge, "_command", command)
    click(action)
    command.assert_called_once_with("importStudySubtitle", {})
    assert engine._tingmu_warnings == []
