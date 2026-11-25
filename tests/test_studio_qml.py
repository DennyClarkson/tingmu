"""Exercise selected-job studio state through the shipping workspace and real Qt controls."""

from PySide6.QtCore import QObject

from test_qml_v4 import find, invoke, quick as quick
from test_qml_v5 import visual_find


def test_live_studio_projects_real_plan_slots_and_only_runs_visible_work(quick, qtbot):
    app, bridge, engine, window = quick
    window.resize(1480, 930)
    invoke(window, "changeView", "prepare")
    panel = find(window, "queueModule").property("loadedItem")
    phases = ["字幕评估", "读取原字幕", "逐句讲解", "译稿核验", "保存字幕"]
    rows = [dict(id=i+1, label=f"第 {i+1} 句", total=1, done=0, status="running") for i in range(16)]
    bridge._jobs = [{"id": "live", "name": "Studio.mkv", "status": "running", "plan": phases,
                     "stage_name": phases[3], "stage_history": phases[:4], "stage_done": 6, "stage_total": 10,
                     "unit": "句", "progress": 0.6, "batch_slots": rows, "worker_limit": 16,
                     "source_name": "Studio.en.srt", "adoption": "bilingual", "evidence_counts": [10, 6, 0],
                     "quality": {"input_cue_count": 15, "output_cue_count": 10}, "run_started_at": 1,
                     "verified_examples": [{"id": "a", "source": "One.", "translation": "一。"}]}]
    bridge.jobsChanged.emit()
    qtbot.waitUntil(lambda: find(panel, "queueList").property("currentItem") is not None)
    qtbot.wait(40)
    studio = visual_find(panel, "preparationStudio")
    assert visual_find(studio, "studioOverallPercent").property("text") == "72%"
    assert visual_find(studio, "studioAdoptedSource").isVisible()
    assert visual_find(studio, "studioCoverageBar").property("value") == 0.6
    assert visual_find(studio, "studioVerifiedPreview").isVisible()
    assert visual_find(studio, "studioQualityAudit").isVisible()
    assert studio.property("activeCount") == 16
    timer = studio.findChild(QObject, "studioElapsedTimer")
    if app.platformName() not in ("offscreen", "minimal"):
        qtbot.waitUntil(lambda: timer.property("running"))
        viewport = find(panel, "studioDetailScroll").property("contentItem")
        viewport.setProperty("contentY", viewport.property("contentHeight") - viewport.height())
        qtbot.waitUntil(lambda: not timer.property("running"))
        viewport.setProperty("contentY", 0)
        qtbot.waitUntil(lambda: timer.property("running"))
    else:
        assert not timer.property("running")
    invoke(window, "changeView", "watch")
    assert not timer.property("running")
    assert engine._tingmu_warnings == []


def test_finished_studio_opens_prepared_job_media_and_watch_layout(quick, qtbot, tmp_path):
    _, bridge, engine, window = quick
    movie = tmp_path / "Selected.mkv"
    movie.touch()
    invoke(window, "changeView", "prepare")
    bridge._jobs = [{"id": "done", "path": str(movie), "name": movie.name, "status": "completed", "progress": 1.0}]
    bridge.jobsChanged.emit()
    panel = find(window, "queueModule").property("loadedItem")
    qtbot.waitUntil(lambda: find(panel, "queueList").property("currentItem") is not None)
    qtbot.wait(30)
    invoke(visual_find(panel, "studioWatchButton"), "clicked")
    assert window.property("viewMode") == "watch"
    assert bridge.mediaState["mediaName"] == movie.name
    assert engine._tingmu_warnings == []
