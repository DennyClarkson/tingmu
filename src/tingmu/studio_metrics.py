"""Small projections of measured preparation data for the local queue UI."""

from math import exp

from .translation_review import translation_verified


LIVE_FIELDS = frozenset(("quality", "run_started_at", "elapsed_seconds", "worker_limit", "batch_slots",
                         "matrix_stage", "throughput", "stage_history", "track_evidence", "screening_notes"))


class CueRate:
    """Time-weighted moving average of new cues per second in one pipeline stage."""

    def __init__(self, now, completed=0):
        self.anchor, self.completed, self.rate = now, completed, 0.0

    def update(self, now, completed):
        elapsed = now - self.anchor
        if elapsed > 0 and completed > self.completed:
            observed = (completed - self.completed) / elapsed
            weight = 1 - exp(-elapsed / 5) if self.rate else 1
            self.rate += weight * (observed - self.rate)
            self.anchor, self.completed = now, completed
        return self.rate


def quality_summary(metadata):
    """Explicitly omit lineage, raw dialogue, response bodies and model reasoning."""
    fields = ("input_cue_count", "output_cue_count", "coalesced_count", "split_runon_count",
              "repaired_timing_count", "short_fragment_count", "sub_500ms_count", "dropped_count")
    audit = metadata.get("pre_llm_audit") or {}
    summary = {key: audit[key] for key in fields if type(audit.get(key)) is int}
    if summary:
        summary["source_kind"] = audit.get("source_kind", "")
    regroup = metadata.get("regroup_audit") or {}
    for key in ("input_unit_count", "covered_unit_count", "chunk_count", "fallback_chunk_count", "corrected_count"):
        if type(regroup.get(key)) is int:
            summary[key] = regroup[key]
    return summary


def verified_examples(cues, review):
    """The preview accepts only a review digest matching this exact cue revision."""
    result = []
    for cue in reversed(cues):
        if translation_verified(cue, review):
            result.append({"id": cue.id, "source": cue.text[:200], "translation": cue.translation[:200]})
            if len(result) == 3:
                break
    return result[::-1]


def record_batch_slot(job, batch):
    """Assign real scheduler events to stable slots, preferring unused slots."""
    limit = min(16, max(1, job.get("worker_limit", 1)))
    if job.get("matrix_stage") != job.get("stage_name"):
        job["batch_slots"] = []
    job["matrix_stage"] = job.get("stage_name", "")
    slots = job.setdefault("batch_slots", [])
    index = next((i for i, row in enumerate(slots) if row["id"] == batch["id"]), None)
    if index is None:
        if len(slots) < limit:
            slots.append(dict(batch))
            return
        # Retire the oldest observed finished slot. A currently running worker
        # retains its position until its matching outcome arrives.
        free = [i for i, row in enumerate(slots) if row["status"] != "running"]
        if not free:
            return
        index = min(free, key=lambda i: slots[i]["id"])
    slots[index] = dict(batch)
