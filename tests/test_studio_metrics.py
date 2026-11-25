"""Studio projections are bounded, evidence-based and ephemeral."""

from dataclasses import replace
import json

import pytest

from tingmu import batch
from tingmu.models import AppSettings, Cue, StudyDocument
from tingmu.storage import Store
from tingmu.studio_metrics import CueRate, LIVE_FIELDS, quality_summary, record_batch_slot, verified_examples
from tingmu.task_progress import Progress
from tingmu.translation_review import TRANSLATION_REVIEW_VERSION, lesson_digest


def test_preview_requires_current_review_digest_and_bounds_count_and_length():
    cues = [Cue(str(i), i, i + 1, "private source " * 24, translation=str(i), explanation="lesson")
            for i in range(8)]
    review = {"version": TRANSLATION_REVIEW_VERSION, "verified": {c.id: lesson_digest(c, []) for c in cues[:7]},
              "provided": {"7": lesson_digest(cues[7], [])}, "segments": {}}
    cues[6] = replace(cues[6], translation="edited after review")
    rows = verified_examples(cues, review)
    assert [r["id"] for r in rows] == ["3", "4", "5"]
    assert all(len(r["source"]) == 200 and set(r) == {"id", "source", "translation"} for r in rows)


def test_studio_rate_smooths_new_work_and_excludes_restored_cues():
    rate = CueRate(10, completed=50)
    assert rate.update(15, 60) == 2
    assert rate.update(16, 70) == pytest.approx(3.450153975)
    assert rate.update(16, 70) == pytest.approx(rate.rate)
    assert rate.update(20, 71) < 3.45


@pytest.mark.parametrize("limit", [1, 2, 16])
def test_batch_matrix_keeps_active_worker_slots_stable_during_splits_and_repairs(limit):
    job = {"worker_limit": limit, "stage_name": "逐句讲解"}
    for i in range(limit):
        record_batch_slot(job, {"id": i, "status": "running"})
    record_batch_slot(job, {"id": 0, "status": "split"})
    assert job["batch_slots"][0]["status"] == "split"
    record_batch_slot(job, {"id": limit, "status": "running", "repair": True})
    assert job["batch_slots"][0] == {"id": limit, "status": "running", "repair": True}
    assert [s["id"] for s in job["batch_slots"][1:]] == list(range(1, limit))
    record_batch_slot(job, {"id": limit, "status": "partial", "done": 2, "repair": True})
    assert len(job["batch_slots"]) == limit
    job["stage_name"] = "译稿核验"
    record_batch_slot(job, {"id": 0, "status": "running"})
    assert job["batch_slots"] == [{"id": 0, "status": "running"}]


def test_quality_summary_carries_only_numeric_evidence_and_origin():
    meta = {"pre_llm_audit": {"source_kind": "sidecar", "input_cue_count": 3, "coalesced_count": 1,
                             "source_text": "secret speech", "counts": {"speaker": "private"}},
            "regroup_audit": {"input_unit_count": 7, "covered_unit_count": 7, "fallback_chunk_count": 1,
                             "responses": [{"content": "private response body"}]}}
    assert quality_summary(meta) == {"source_kind": "sidecar", "input_cue_count": 3, "coalesced_count": 1,
                                    "input_unit_count": 7, "covered_unit_count": 7, "fallback_chunk_count": 1}


def test_queue_never_persists_preview_or_live_telemetry_even_at_stage_boundaries(tmp_path, monkeypatch):
    movie = tmp_path / "film.mp4"
    movie.touch()
    q = batch.PreparationQueue(Store(tmp_path / "data"))
    settings = AppSettings()
    ident = q.enqueue([movie], settings)[0]["id"]
    log = []

    def prepare(path, settings, store, *, progress, **kwargs):
        cues = [Cue(str(i), i, i+1, f"private source {i}", translation=f"private translation {i}", explanation="ok")
                for i in range(5)]
        review = {"version": TRANSLATION_REVIEW_VERSION, "verified": {c.id: lesson_digest(c, []) for c in cues}}
        progress(Progress("verify", stage="译稿核验", plan=["译稿核验", "保存字幕"]))
        for i in range(5):
            progress(Progress("verified", done=i+1, total=5, unit="句",
                              verified_examples=verified_examples([cues[i]], review)))
        progress(Progress("publish", stage="保存字幕"))
        return StudyDocument(str(path), settings.language, cues, metadata={"translation_review": review})

    monkeypatch.setattr(batch, "prepare_media", prepare)
    q.run(progress=log.append)
    assert [r["id"] for r in q.studio_previews()[ident]] == ["2", "3", "4"]
    assert "verified_examples" not in q.snapshot()[0]
    disk = json.loads(q.path.read_bytes())["jobs"][0]
    assert not LIVE_FIELDS.intersection(disk)
    assert all("private source" not in s and "private translation" not in s for s in (
        q.path.read_text("utf-8"), q._saved_payload, "".join(log)))
    assert batch.PreparationQueue(q.store).studio_previews() == {}
    q.clear_completed()
    assert not q.studio_previews()
