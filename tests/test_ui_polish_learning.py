"""Exercise saved-item confirmation and the study surfaces at their supported sizes."""
from PySide6.QtCore import QPointF

from tingmu.models import Cue, LanguagePair, Token
from test_qml_v4 import find, invoke, quick as quick
from test_qml_v5 import click, visual_find


def save_study_words(bridge, words):
    for index, word in enumerate(words):
        cue = Cue(str(index), index, index + 1, word)
        bridge.store.save_word("studio.mp4", cue, Token(word, meaning="释义 " * 160), LanguagePair())
    bridge._refresh_notebooks()
    return {entry["text"]: entry["id"] for entry in bridge.notebooks["collections"]}


def test_collection_delete_confirms_original_entry_after_delegate_refresh(quick, qtbot):
    _, bridge, engine, window = quick
    entries = save_study_words(bridge, ["詞甲", "詞乙"])
    invoke(window, "changeView", "learn")
    inbox = find(window, "collectionInbox")
    dialog = find(inbox, "deleteCollectionConfirmation")
    target = entries["詞甲"]
    listing = find(inbox, "collectionList")
    index = next(i for i, item in enumerate(bridge.notebooks["collections"]) if item["id"] == target)
    listing.positionViewAtIndex(index, 0)
    qtbot.wait(40)
    remove = visual_find(inbox, "deleteCollectionEntry_" + str(target))
    invoke(remove, "click")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    assert dialog.property("entryId") == target
    assert dialog.property("entryText") == "詞甲"
    assert dialog.property("destructiveAccept")
    invoke(dialog, "reject")
    assert {row["id"] for row in bridge.notebooks["collections"]} == set(entries.values())
    invoke(remove, "click")
    qtbot.waitUntil(lambda: dialog.property("opened"))
    # A model refresh may remove the original delegate while the confirmation is open.
    inbox.setProperty("entries", [entry for entry in bridge.notebooks["collections"] if entry["id"] != target])
    qtbot.wait(30)
    assert dialog.property("entryId") == target
    invoke(dialog, "accept")
    qtbot.waitUntil(lambda: [row["id"] for row in bridge.notebooks["collections"]] == [entries["詞乙"]])
    assert engine._tingmu_warnings == []


def test_review_content_scrolls_and_actions_remain_reachable(quick, qtbot):
    _, bridge, engine, window = quick
    save_study_words(bridge, ["學習"])
    invoke(find(window, "interfaceEnglish"), "trigger")
    invoke(window, "changeView", "learn")
    click(window, find(window, "reviewTab"))
    qtbot.waitUntil(lambda: bool(bridge.notebooks["practice"]))
    click(window, find(window, "revealReviewAnswer"))
    card = find(window, "learningStudyCard")
    scroll = find(window, "reviewContentScroll")
    for w, h in ((1480, 930), (980, 620)):
        window.resize(w, h)
        qtbot.wait(50)
        assert 0 < card.width() <= 760
        assert scroll.height() > 20
        for rating in ("again", "hard", "good", "easy"):
            button = visual_find(card, "reviewRating_" + rating)
            assert button.isVisible()
            point = button.mapToItem(card, QPointF(button.width(), button.height()))
            assert 0 < point.x() <= card.width() and 0 < point.y() <= card.height()
        sidebar = find(window, "learningSidebar")
        divider = find(window, "learningSidebarDivider")
        learning = find(window, "learningWorkspace")
        divider_right = divider.mapToItem(learning, QPointF(divider.width(), 0)).x()
        card_center = card.mapToItem(learning, QPointF(card.width() / 2, 0)).x()
        # Equal gutter and outer margin put the center halfway from divider to right edge.
        assert abs(card_center - (divider_right + learning.width()) / 2) < 2
        for child in sidebar.childItems():
            if child.isVisible():
                right = child.mapToItem(divider, QPointF(child.width(), 0)).x()
                assert right <= 0, child.objectName()
    view = scroll.property("contentItem")
    assert view.property("contentHeight") > scroll.height()
    view.setProperty("contentY", 80)
    assert view.property("contentY") == 80
    click(window, visual_find(card, "reviewRating_good"))
    qtbot.waitUntil(lambda: not bridge.notebooks["practice"])
    assert engine._tingmu_warnings == []


def test_queue_semantic_badges_leave_the_scrollbar_clear(quick, qtbot):
    _, bridge, engine, window = quick
    bridge.timer.stop()
    invoke(window, "openModule", "queue")
    queue = find(window, "queueModule").property("loadedItem")
    queue.window().resize(330, 700)
    expected = {"failed": "danger", "paused": "warning", "completed": "accent", "queued": "neutral"}
    bridge._jobs = [{"id": state, "name": "study.mp4", "status": state, "message": state} for state in expected]
    bridge.jobsChanged.emit()
    listing = find(queue, "queueList")
    for index, (state, tone) in enumerate(expected.items()):
        listing.positionViewAtIndex(index, 0)
        qtbot.wait(30)
        card = visual_find(listing, "queueJob:" + state)
        badge = visual_find(card, "queueStatusBadge")
        assert badge.property("tone") == tone
        assert card.width() <= listing.width() - 6
        assert visual_find(card, "queueJobMessage").property("text") == state
    assert engine._tingmu_warnings == []
