"""Checkpoint the optional index-based text-model review before ordinary teaching."""
from dataclasses import asdict, replace
import hashlib
import json
import time

from .material_reset import TEACHING_METADATA
from .models import Cue, Token
from .runtime import confined_path
from .storage import atomic_json
from .task_progress import Progress


def _persist_evidence(store, evidence):
    payload = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    relative = "regroup-audit/" + hashlib.sha256(payload.encode()).hexdigest() + ".json"
    atomic_json(confined_path(store.data_dir, relative), evidence)
    return relative


def acquisition_cues(document, protected_ids, store):
    """Recover acquisition evidence across changes of segmentation model or mode."""
    meta = document.metadata
    ledger = meta.get("pre_llm_lineage", {})
    raw = ledger.get("sources")
    by_id = {c.id: c for c in document.cues}
    derived_locks = (set(protected_ids) & by_id.keys()) - {r["id"] for r in raw or ()}
    display = meta.get("raw_display_text", {})
    if raw:
        sources = [Cue(**{**r, "tokens": [Token(**t) for t in r.get("tokens", ())]}) for r in raw]
        if derived_locks:
            # One source may have several derived children. Keep that entire
            # family together while recovering the original units elsewhere.
            families = {k: set(row["source_indices"]) for k, row in ledger["outputs"].items()}
            covered = set().union(*(families[k] for k in derived_locks))
            while True:
                extra = set().union(*(v for v in families.values() if v & covered)) - covered
                if not extra:
                    break
                covered.update(extra)
            meta["source_ancestry"] = _persist_evidence(store, {
                "lineage": ledger, "previous": meta.get("source_ancestry"),
                "regroup_evidence": meta.get("regroup_evidence"),
            })
            sources = [c for i, c in enumerate(sources) if i not in covered]
            sources.extend(replace(c, text=display.get(c.id, c.text)) for c in document.cues
                           if families[c.id] & covered)
            sources.sort(key=lambda c: (c.start, c.end))
    else:
        sources = [replace(c, text=display.get(c.id, c.text)) for c in document.cues]
    return [by_id[c.id] if c.id in protected_ids and c.id in by_id else
            replace(c, translation=meta.get("provided_translations", {}).get(c.id, ""),
                    explanation="", tokens=[], segmented_texts=[]) for c in sources]


def regroup_document(ctx, *, source_kind):
    from .regroup import VERSION, regroup
    from .regroup_sources import build_units
    from .preparation import record_quality_counts

    doc, store = ctx.document, ctx.store
    meta, spec = doc.metadata, ctx.plan.review_spec
    if meta.get("regroup_spec") == spec and meta.get("regroup_stage") == "ready":
        return
    protected = store.referenced_cue_ids(doc.media_path, doc.language)
    protected.update(meta.get("cue_corrections", {}))
    provided = meta.get("provided_translations", {})
    protected.update(provided)
    if ctx.reuse_existing_transcript:
        protected.update(c.id for c in doc.cues if c.explanation and c.translation)
    sources = acquisition_cues(doc, protected, store)
    units, origin = build_units(sources, source_kind=source_kind, metadata=meta,
                                audit_dir=store.data_dir / "asr-audit", protected_ids=protected)
    if not units:
        raise ValueError("未找到有效对白。请检查字幕轨、音轨或所选时间范围。")
    ctx.check_cancel()
    ctx.report(Progress("智能分块重组", stage="智能分块重组", stage_code="review"))
    resume = meta.get("regroup_checkpoint") if meta.get("regroup_spec") == spec else None
    meta.update(regroup_spec=spec, regroup_stage="running", stage="reviewing", stage_code="review")
    if resume is None:
        meta.pop("regroup_checkpoint", None)
    store.save_document(doc)

    def checkpoint(state):
        meta["regroup_checkpoint"] = state
        store.save_document(doc)

    try:
        result, audit, lineage = regroup(
            units, doc.language, provider=spec["provider"], model=spec["model"],
            profile=ctx.plan.review_profile, max_workers=ctx.settings.llm_workers,
            protected_ids=protected, source_cues=sources, quiet_intervals=meta.get("asr_quiet_intervals", ()),
            cancelled=ctx.cancelled, checkpoint=checkpoint, resume=resume, progress=ctx.report,
        )
        ctx.check_cancel()
    except Exception:
        meta.update(regroup_stage="partial", stage="partial")
        store.save_document(doc)
        raise

    # Write the replayable word stream once. Subsequent lesson checkpoints
    # reference this local artifact rather than serializing all words each time.
    evidence = {"version": VERSION, "origin": origin, "units": units, "outputs": lineage["outputs"]}
    relative = _persist_evidence(store, evidence)
    outputs = lineage["outputs"]
    dropped = origin.get("dropped_indices", [])
    counts = {
        "version": VERSION, "source_kind": source_kind, "input_cue_count": len(sources),
        "output_cue_count": len(result), "input_unit_count": len(units),
        "coalesced_count": sum(max(0, len(set(row["source_indices"])) - 1) for row in outputs.values()),
        "split_runon_count": len({i for row in outputs.values() if row.get("timing_basis") == "text_proportional"
                                 for i in row["source_indices"]}),
        "repaired_timing_count": sum(u.get("timing_basis") in (
            "bounded_estimate", "neighbor_anchor", "neighbor_bounded", "pause_bounded_estimate") for u in units),
        "sub_500ms_count": sum(round(c.end*1000) - round(c.start*1000) < 500 for c in result),
        "short_fragment_count": sum(sum(ch.isalnum() for ch in c.text) <= 3 for c in result),
        "dropped_count": len(dropped), "fallback_chunk_count": audit["fallback_chunk_count"],
    }
    for key in TEACHING_METADATA:
        if not (ctx.reuse_existing_transcript and key in ("translation_review", "model_responses",
                                                         "analysis_batches", "checkpoint_events")):
            meta.pop(key, None)
    doc.cues = result
    raw_display = meta.get("raw_display_text", {})
    meta.update(
        regroup_stage="ready", regroup_audit=audit, regroup_source_audit=origin, regroup_evidence=relative,
        pre_llm_audit=counts, pre_llm_lineage={
            "sources": [asdict(c) for c in sources], **lineage,
            "dropped_indices": dropped, "dropped_ids": [sources[i].id for i in dropped],
        },
        stage="reviewed", review_stage="ready", review_spec=spec, review_finished_at=time.time(),
        reviewed_count=len(result), review_completed_ids=[c.id for c in result],
        reviewed_text={c.id: c.text for c in result},
        raw_display_text={c.id: raw_display.get(c.id, c.text) if c.id in protected
                          else outputs[c.id]["raw_text"] for c in result},
        reviewed_segmented_texts={c.id: c.segmented_texts for c in result if c.segmented_texts},
        review_records={c.id: {"state": "uncertain" if c.uncertain else "regrouped",
                               "changed": c.text != outputs[c.id]["raw_text"],
                               "reason": outputs[c.id].get("reason", "")} for c in result},
        completed_ids=[], annotated_count=0, error="",
    )
    meta.pop("regroup_checkpoint", None)
    if "subtitle_plan" in meta:
        meta["subtitle_plan"]["prepared_count"] = len(result)
    store.save_document(doc)
    record_quality_counts(store, doc, counts, source_kind, ctx.log_event)
