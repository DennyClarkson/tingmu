"""Observe the shipping QML controls on native Windows and their static offscreen state."""

from contextlib import contextmanager

from PySide6.QtCore import QPointF

from test_qml_v4 import find, invoke, quick as quick
from test_qml_v5 import visual_find


@contextmanager
def observe(signal, callback):
    connection = signal.connect(callback)
    try:
        yield
    finally:
        signal.disconnect(connection)


def prepare(bridge, window, qtbot, **changes):
    window.resize(1500, 970)
    invoke(window, "changeView", "prepare")
    job = dict(id="motion", name="Example.mkv", status="running", plan=["逐句讲解", "译稿核验"],
               stage_name="逐句讲解", matrix_stage="逐句讲解", stage_history=["逐句讲解"],
               stage_total=10, stage_done=1, worker_limit=2, evidence_notes=["detail"],
               batch_slots=[dict(id=i, status="running", total=1, done=0) for i in (1, 2)])
    job.update(changes)
    bridge._jobs = [job]
    bridge.jobsChanged.emit()
    panel = find(window, "queueModule").property("loadedItem")
    qtbot.waitUntil(lambda: find(panel, "queueList").property("currentItem") is not None)
    qtbot.wait(80)
    return job, visual_find(panel, "preparationStudio"), find(panel, "studioDetailScroll").property("contentItem")


def publish(bridge, job, **changes):
    job.update(changes)
    bridge._jobs = [dict(job)]
    bridge.jobsChanged.emit()


def scroll_to(viewport, item, margin=20):
    y = item.mapToItem(viewport.property("contentItem"), QPointF()).y()
    viewport.setProperty("contentY", max(0, min(y - margin, viewport.property("contentHeight") - viewport.height())))


def test_visible_lanes_reveal_real_unicode_then_drain_and_stop(quick, qtbot):
    app, bridge, engine, window = quick
    job, studio, viewport = prepare(bridge, window, qtbot, worker_limit=16)
    lane = visual_find(studio, "studioWorkerSlot:0")
    stream = visual_find(lane, "studioWorkerStream:0")
    other = visual_find(studio, "studioWorkerStream:1")
    output = visual_find(stream, "studioStreamOutput")
    poll = find(studio, "studioStreamPoll")
    streams = bridge.queue.streams
    delta = streams.bind(job["id"], job["stage_name"])
    streams.sync(job["id"], job["stage_name"], job["batch_slots"])
    scroll_to(viewport, lane)
    qtbot.wait(100)
    native = app.platformName() not in ("offscreen", "minimal")
    assert poll.property("running") == native
    assert stream.property("cursorBlinking") == native
    line = "我𠮷們🙂在看原文"
    observed = []
    height = stream.height()
    with observe(stream.displayedChanged, lambda: observed.append(stream.property("displayed"))):
        delta(1, '{"cues":[{"text":"' + line)
        delta(2, '{"cues":[{"translation":"two')
        if native:
            qtbot.waitUntil(lambda: stream.property("displayed") == line, timeout=2000)
            assert observed == [line[:i] for i in range(1, len(line) + 1)]
            assert other.property("displayed") == "two"
            assert output.property("selectedText") == line[-1]
            assert stream.property("highlight") > 0
            qtbot.waitUntil(lambda: stream.property("highlight") == 0)
        else:
            invoke(studio, "refreshStreams", False)
            assert observed == [line]
            assert stream.property("highlight") == 0
            assert other.property("displayed") == "two"
    assert stream.height() == height
    suffix = "多行输出保持固定视口" * 9
    delta(1, suffix + '"}]}')
    job["batch_slots"][0] = dict(job["batch_slots"][0], status="completed", done=1)
    streams.sync(job["id"], job["stage_name"], job["batch_slots"])
    publish(bridge, job)
    qtbot.waitUntil(lambda: stream.property("displayed") == line + suffix, timeout=1000)
    assert not stream.property("cursorBlinking")
    assert stream.height() == height
    assert output.y() < 0
    if native:
        assert lane.property("flash") > 0
        assert poll.property("running")
        window.resize(1500, 640)
        qtbot.wait(60)
        viewport.setProperty("contentY", viewport.property("contentHeight") - viewport.height())
        qtbot.waitUntil(lambda: not poll.property("running"))
        assert not other.property("cursorBlinking")
        scroll_to(viewport, lane)
        qtbot.waitUntil(lambda: poll.property("running"))
        window.showMinimized()
        qtbot.waitUntil(lambda: not poll.property("running"))
        assert not other.property("cursorBlinking")
        window.showNormal()
        qtbot.waitUntil(lambda: poll.property("running"))
    invoke(window, "changeView", "watch")
    assert not poll.property("running")
    assert not other.property("cursorBlinking")
    assert engine._tingmu_warnings == []


def test_stage_progress_evidence_and_verified_arrivals_in_native_layout(quick, qtbot):
    app, bridge, engine, window = quick
    first = dict(id="one", source="One", translation="一")
    job, studio, viewport = prepare(bridge, window, qtbot, verified_examples=[first])
    native = app.platformName() not in ("offscreen", "minimal")
    percent = visual_find(studio, "studioOverallPercent")
    step = visual_find(studio, "studioStep:0")
    progress_frames = []
    with observe(percent.textChanged, lambda: progress_frames.append(percent.property("text"))):
        publish(bridge, job, stage_name="译稿核验", stage_history=job["plan"], stage_done=4)
        qtbot.waitUntil(lambda: percent.property("text") == "70%")
    assert step.property("trailingFraction") == 1
    if native:
        assert any(5 < int(frame[:-1]) < 70 for frame in progress_frames)
    toggle = visual_find(studio, "studioEvidenceToggle")
    body = visual_find(studio, "studioEvidenceBody")
    scroll_to(viewport, toggle)
    invoke(toggle, "clicked")
    if native:
        qtbot.waitUntil(lambda: 0 < body.opacity() < 1)
    qtbot.waitUntil(lambda: body.opacity() == 1)
    assert body.height() > 0
    invoke(toggle, "clicked")
    qtbot.waitUntil(lambda: not body.isVisible())
    row = visual_find(studio, "studioVerifiedRow:one")
    scroll_to(viewport, row)
    publish(bridge, job, verified_examples=[first, dict(id="two", source="Two", translation="二")])
    qtbot.waitUntil(lambda: visual_find(studio, "studioVerifiedRow:two").width() > 0)
    new_row = visual_find(studio, "studioVerifiedRow:two")
    if native:
        qtbot.waitUntil(lambda: 0 < new_row.property("arrival") < 1)
    qtbot.waitUntil(lambda: new_row.property("arrival") == 1)
    assert visual_find(studio, "studioVerifiedRow:one") is row
    assert engine._tingmu_warnings == []


def test_matrix_transitions_and_stream_reuse(quick, qtbot):
    app, bridge, engine, window = quick
    job, studio, viewport = prepare(bridge, window, qtbot)
    matrix = visual_find(studio, "studioBatchMatrix")
    scroll_to(viewport, matrix)
    slot = visual_find(matrix, "studioWorkerSlot:0")
    stream = visual_find(slot, "studioWorkerStream:0")
    native = app.platformName() not in ("offscreen", "minimal")
    initial = slot.property("displayedInk")
    delta = bridge.queue.streams.bind(job["id"], job["stage_name"])
    bridge.queue.streams.sync(job["id"], job["stage_name"], job["batch_slots"])
    delta(1, '{"cues":[{"text":"old"}]}')
    invoke(studio, "refreshStreams", False)
    qtbot.waitUntil(lambda: stream.property("displayed") == "old")
    delta(1, None)
    delta(1, '{"cues":[{"text":"新"}]}')
    publish(bridge, job, batch_slots=[dict(id=1, status="running", total=1, done=0, repair=True)])
    qtbot.waitUntil(lambda: stream.property("displayed") == "新")
    if native:
        assert slot.property("flash") > 0
    qtbot.waitUntil(lambda: slot.property("displayedInk") != initial)
    height = matrix.height()
    publish(bridge, job, batch_slots=[])
    if native:
        qtbot.waitUntil(lambda: 0 < matrix.opacity() < 1)
        assert matrix.height() < height
    qtbot.waitUntil(lambda: not matrix.isVisible())
    assert matrix.implicitHeight() == 0
    publish(bridge, job, batch_slots=[dict(id=3, status="running")])
    if native:
        qtbot.waitUntil(lambda: 0 < matrix.opacity() < 1)
    qtbot.waitUntil(lambda: matrix.opacity() == 1)
    assert visual_find(matrix, "studioWorkerSlot:0") is slot
    assert stream.property("displayed") == ""
    assert engine._tingmu_warnings == []


def test_queue_selection_interpolates_only_in_visible_list(quick, qtbot):
    app, bridge, engine, window = quick
    job, _, _ = prepare(bridge, window, qtbot)
    bridge._jobs = [job, dict(id="second", name="Second.mkv", status="queued")]
    bridge.jobsChanged.emit()
    panel = find(window, "queueModule").property("loadedItem")
    card = visual_find(panel, "queueJob:second")
    initial = card.property("color")
    colors = []
    with observe(card.colorChanged, lambda: colors.append(card.property("color").rgba())):
        invoke(panel, "toggle", "second")
        qtbot.waitUntil(lambda: card.property("color") != initial)
        qtbot.wait(250)
    if app.platformName() not in ("offscreen", "minimal"):
        assert len(set(colors)) > 2
    else:
        assert len(set(colors)) == 1
    invoke(window, "changeView", "watch")
    invoke(panel, "toggle", "second")
    assert card.property("color") == initial
    assert engine._tingmu_warnings == []
