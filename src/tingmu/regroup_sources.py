"""Build the positional evidence ledger for optional text-only subtitle regrouping."""

from bisect import bisect_left
from collections import Counter
import json
import math
from pathlib import Path

from .acoustic import alignment_text_ranges
from .pre_llm import ASR_KINDS, CAPTION_KINDS, repair_source_times, source_rows

_MAX_AUDIT_BYTES = 64 * 1024 * 1024


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("invalid_time")
    return float(value)


def _array(value):
    if not isinstance(value, list):
        raise ValueError("invalid_array")
    return value


def _read_audit(directory, hint):
    if not directory or not isinstance(hint, str) or not hint:
        raise ValueError("audit_unavailable")
    root = Path(directory).resolve(strict=True)
    hinted = Path(hint)
    if hinted.drive and not hinted.is_absolute() or ".." in hinted.parts:
        raise ValueError("audit_outside_workspace")
    candidate = hinted if hinted.is_absolute() else root / hinted
    # Reject outside paths lexically before even probing a network share.
    if not candidate.is_relative_to(root):
        raise ValueError("audit_outside_workspace")
    path = candidate.resolve(strict=True)
    if not path.is_relative_to(root) or path.suffix.lower() != ".json":
        raise ValueError("audit_outside_workspace")
    with path.open("rb") as stream:
        if path.stat().st_size > _MAX_AUDIT_BYTES:
            raise ValueError("audit_size_limit")
        blob = stream.read(_MAX_AUDIT_BYTES + 1)
    if len(blob) > _MAX_AUDIT_BYTES:
        raise ValueError("audit_size_limit")
    raw = json.loads(blob)
    if not isinstance(raw, dict) or raw.get("complete") is not True:
        raise ValueError("incomplete_audit")
    return raw


def _spans(words, first, last, offset):
    times = []
    for word in words:
        a, b = _time(word[first]), _time(word[last])
        if a < 0 or b < a:
            raise ValueError("invalid_span")
        times.append((a + offset, b + offset))
    return times


def _repaired(times, report, origin):
    """Use only a worker's explicitly recorded per-unit repairs."""
    result = list(times)
    for row in _array(report.get("timing_repairs", [])):
        a, b = row["unit_start"], row["unit_end"]
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(times):
            raise ValueError("invalid_repair_range")
        spans = row.get("unit_spans")
        if spans is None and b == a + 1:
            spans = [row.get("estimated_span")]
        if spans is None:
            continue
        if not isinstance(spans, list) or len(spans) != b - a:
            raise ValueError("invalid_repair_spans")
        for i, pair in enumerate(spans, a):
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("invalid_repair_span")
            x, y = map(_time, pair)
            if x < 0 or x > y:
                raise ValueError("invalid_repair_span")
            result[i] = (x + origin, y + origin)
    return result


def _partition(cues, ids, pieces):
    """Every published cue must match contiguous whole words, including punctuation."""
    cursor = 0
    for ident in ids:
        cue = cues[ident]
        first = cursor
        size = 0
        while cursor < len(pieces) and size < len(cue.original_text):
            size += len(pieces[cursor])
            cursor += 1
        if cursor == first or "".join(pieces[first:cursor]) != cue.original_text:
            raise ValueError("word_text_mismatch")
        yield ident, first, cursor
    if cursor != len(pieces):
        raise ValueError("unmapped_units")


def _index_block(index, cues, grouping, pieces, times, raw_times, block, basis):
    consumed = 0
    for ident, first, stop in grouping:
        if not (type(first) is int and type(stop) is int and consumed <= first < stop <= len(pieces)):
            raise ValueError("invalid_source_range")
        consumed = stop
        if ident not in cues:
            continue
        c = cues[ident]
        if ident in index or "".join(pieces[first:stop]) != c.original_text:
            raise ValueError("source_text_mismatch")
        atoms = []
        previous = c.start
        for n in range(first, stop):
            a, b = times[n]
            # Raw model ticks may sit at the rounded edge of a published cue.
            if a < c.start - 0.025 or b > c.end + 0.025 or a < previous - 0.025:
                break
            a = max(c.start, round(a, 3), previous)
            b = max(a, min(c.end, round(b, 3)))
            if b > c.end:
                break
            previous = b
            raw_a, raw_b = raw_times[n]
            repaired = abs(a - raw_a) > 0.001 or abs(b - raw_b) > 0.001
            atoms.append(
                dict(
                    text=pieces[n],
                    original_text=pieces[n],
                    start=a,
                    end=b,
                    reported_start=raw_a,
                    reported_end=raw_b,
                    block=block,
                    raw_index=n,
                    uncertain=c.uncertain or repaired or a == b,
                    timing_basis="bounded_estimate" if repaired else basis,
                    literal=True,
                )
            )
        if len(atoms) == stop - first:
            index[ident] = atoms
        elif c.end > c.start:
            index[ident] = [
                dict(
                    text=c.original_text,
                    original_text=c.original_text,
                    start=c.start,
                    end=c.end,
                    reported_start=raw_times[first][0],
                    reported_end=raw_times[stop - 1][1],
                    raw_index=first,
                    raw_indices=list(range(first, stop)),
                    block=block,
                    timing_basis="source_span",
                    literal=False,
                    uncertain=True,
                )
            ]


def _qwen_index(audit, originals):
    by_id = {c.id: c for c in originals}
    starts = [c.start for c in originals]
    index = {}
    for i, block in enumerate(_array(audit["chunks"])):
        if not block["text"].strip():
            continue
        origin, duration = _time(block["start"]), _time(block["duration"])
        if duration <= 0 or origin < 0:
            raise ValueError("invalid_chunk")
        if block.get("alignment_error") and not block.get("cue_timings"):
            continue
        words = _array(block["alignment"])
        raw_times = _spans(words, "start_time", "end_time", origin)
        if any(b > origin + duration + 0.025 for _, b in raw_times):
            raise ValueError("unit_outside_chunk")
        locations = alignment_text_ranges(block["text"], [word["text"] for word in words])
        edges = [0] + [a for a, _ in locations[1:]] + [len(block["text"])]
        pieces = [block["text"][a:b] for a, b in zip(edges, edges[1:])]
        times = _repaired(raw_times, block, origin)
        if "cue_timings" in block:
            grouping = [(r["cue_id"], r["unit_start"], r["unit_end"]) for r in _array(block["cue_timings"])]
        else:
            # Older audits lack IDs. Only bind a window by exact transcript and timing.
            first = bisect_left(starts, origin - 0.001)
            stop = bisect_left(starts, origin + duration - 0.0005)
            chunk = originals[first:stop]
            if "".join(c.original_text for c in chunk) != block["text"]:
                continue
            grouping = _partition(by_id, [c.id for c in chunk], pieces)
        _index_block(index, by_id, grouping, pieces, times, raw_times, i, "forced_alignment")
    return index


def _whisper_index(audit, originals):
    by_id = {c.id: c for c in originals}
    index = {}
    for i, seg in enumerate(_array(audit["segments"])):
        ids = _array(seg["cue_ids"])
        if not ids or not all(ident in by_id for ident in ids):
            continue
        words = _array(seg["words"])
        if not words or seg.get("alignment_error"):
            if len(ids) == 1:
                ident = ids[0]
                c = by_id[ident]
                a, b = seg.get("start"), seg.get("end")
                if (
                    seg["text"] == c.original_text
                    and type(a) in (int, float)
                    and type(b) in (int, float)
                    and math.isfinite(a)
                    and math.isfinite(b)
                    and a <= c.start + 0.025
                    and b >= c.end - 0.025
                    and a >= 0
                    and b > a
                    and c.end > c.start
                ):
                    index[ident] = [
                        dict(
                            text=c.original_text,
                            original_text=c.original_text,
                            start=c.start,
                            end=c.end,
                            reported_start=a,
                            reported_end=b,
                            block=i,
                            raw_index=i,
                            uncertain=True,
                            timing_basis="whisper_segment",
                            literal=False,
                        )
                    ]
            continue
        raw = seg["text"]
        pieces = [w["text"] for w in words]
        if "".join(pieces).strip() != raw.strip():
            continue
        pieces[0] = raw[: len(raw) - len(raw.lstrip())] + pieces[0].lstrip()
        pieces[-1] = pieces[-1].rstrip() + raw[len(raw.rstrip()) :]
        if "".join(by_id[ident].original_text for ident in ids) != raw:
            continue
        grouping = _partition(by_id, ids, pieces)
        times = _spans(words, "start", "end", 0.0)
        fixed = _repaired(times, seg, _time(seg.get("repair_time_origin", seg["start"])))
        _index_block(index, by_id, grouping, pieces, fixed, times, i, "whisper_word")
    return index


def _positive_atoms(detail, barriers):
    """Keep zero-tick spelling with a measured neighbor; never invent a word onset."""
    result, pending = [], []
    for atom in detail:
        group = dict(atom, raw_indices=list(atom.get("raw_indices", [atom["raw_index"]])))
        if group["end"] == group["start"]:
            pending.append(group)
            continue
        result.append(group)
    if not pending:
        return result
    by_index = {u["raw_index"]: u for u in result}
    measured = sorted(by_index)
    for u in pending:
        pos = bisect_left(measured, u["raw_index"])
        candidates = []
        for k in measured[max(0, pos - 1) : pos + 1]:
            neighbor = by_index[k]
            edge = neighbor["end"] if k < u["raw_index"] else neighbor["start"]
            lo, hi = sorted((edge, u["start"]))
            if hi - lo < 0.25 and not barriers.turn_between(lo, hi) and not barriers.quiet_between(lo, hi):
                candidates.append((hi - lo, k))
        if not candidates:
            return []
        neighbor = by_index[min(candidates)[1]]
        neighbor["raw_indices"].append(u["raw_index"])
        neighbor["uncertain"] = True
        neighbor["timing_basis"] = "neighbor_anchor"
    parts = {u["raw_index"]: u["text"] for u in detail}
    for u in result:
        u["raw_indices"].sort()
        u["text"] = u["original_text"] = "".join(parts[n] for n in u["raw_indices"])
    if "".join(u["text"] for u in result) != "".join(u["text"] for u in detail):
        return []
    return result


def build_units(cues, *, source_kind, metadata, audit_dir, protected_ids=()):
    """Return absolute, indexed model-input units plus content-free provenance counts."""
    cues = list(cues)
    if source_kind not in CAPTION_KINDS | ASR_KINDS | {"legacy"}:
        raise ValueError("Unknown subtitle source kind")
    protected_ids = (
        set(protected_ids)
        | set(metadata.get("provided_translations", {}))
        | set(metadata.get("cue_corrections", {}))
    )
    protected_ids.update(c.id for c in cues if c.start == c.end and any(ch.isalnum() for ch in c.text))
    turns = metadata.get("speaker_changes", ())
    quiet = metadata.get("asr_quiet_intervals", ())
    rows, dropped, folded, barriers = source_rows(
        cues,
        source_kind=source_kind,
        protected_ids=protected_ids,
        speaker_changes=turns,
        quiet_intervals=quiet,
    )
    report = {
        "source_kind": source_kind,
        "source_count": len(cues),
        "dropped_indices": dropped,
        "rolling_fold_count": folded,
    }
    index = {}
    if source_kind in {"local_qwen", "local_whisper"}:
        try:
            audit = _read_audit(audit_dir, metadata.get("asr_audit_path"))
            reader = _qwen_index if source_kind == "local_qwen" else _whisper_index
            index = reader(audit, cues)
            report["audit_status"] = "read"
        except (
            ValueError,
            TypeError,
            KeyError,
            IndexError,
            AttributeError,
            OSError,
            OverflowError,
            RecursionError,
        ) as exc:
            known = {"audit_unavailable", "incomplete_audit", "audit_size_limit", "audit_outside_workspace"}
            report["audit_status"] = (
                str(exc) if type(exc) is ValueError and str(exc) in known else "invalid_or_missing_audit"
            )
    # Timed API/caption segments have no finer anchors. Keep the evidence
    # ledger untouched and use the same micro-overlap and duration hygiene
    # as offline preparation before exposing their boundaries to a model.
    if not index:
        repair_source_times(rows, barriers)
    units = []
    fallbacks = 0
    for row in rows:
        c = row.cue
        source_index = row.indices[0]
        original_units = index.get(c.id, []) if not row.locked else []
        detail = _positive_atoms(original_units, barriers)
        if detail:
            atoms = [dict(u) for u in detail]
            fallbacks += int(detail[0]["timing_basis"] == "source_span")
        else:
            acoustic = source_kind in {"local_whisper", "local_qwen"} and not row.locked
            fallbacks += int(acoustic)
            basis = (
                "protected_source"
                if row.locked
                else "caption_updates"
                if len(row.indices) > 1
                else "caption_event"
                if source_kind in CAPTION_KINDS
                else "api_segment"
                if source_kind in {"local_api", "external_api"}
                else "source_span"
            )
            text = (
                c.text
                if row.locked or len(row.indices) > 1 or source_kind in CAPTION_KINDS
                else c.original_text
            )
            atoms = [
                dict(
                    text=text,
                    original_text=c.original_text,
                    start=c.start,
                    end=c.end,
                    literal=False,
                    timing_basis=row.basis if row.changes & {"micro_overlap_trim", "timing_estimate"} else basis,
                    uncertain=c.uncertain or acoustic,
                )
            ]
            if basis == "api_segment":
                source = metadata.get("asr_segment_sources", {}).get(c.id, {})
                atoms[0]["raw_index"] = source.get("segment_index", source_index)
                atoms[0]["block"] = source.get("chunk")
            if basis in ("caption_updates", "caption_event"):
                events = metadata.get("subtitle_import", {}).get("events", {})
                atoms[0]["event_indices"] = [
                    events.get(cues[n].id, {}).get("event_index", n) for n in row.indices
                ]
            if original_units:
                atoms[0].update(
                    block=original_units[0]["block"],
                    raw_index=original_units[0]["raw_index"],
                    raw_indices=[u["raw_index"] for u in original_units],
                )
        for atom in atoms:
            if atom["start"] == atom["end"]:
                # A legacy spoken source with no measurable extent is held
                # exactly as stored. It cannot participate in a model batch.
                row.locked = True
                atom.update(timing_basis="unplaced_source", literal=False, uncertain=True)
            before = False
            speaker_hint, quiet_ms = False, 0
            if units:
                prev = units[-1]
                gap = atom["start"] - prev["end"]
                quiet_ms = round(
                    1000
                    * max((b - a for a, b in barriers.pauses_in(prev["end"], atom["start"])), default=0.0)
                )
                prev_mid = (prev["start"] + prev["end"]) / 2
                mid = (atom["start"] + atom["end"]) / 2
                speaker_hint = barriers.turn_between(prev_mid, mid)
                # An inferred turn inside a word is noisy. Assign it once,
                # nearest a seam, without isolating that word on both sides.
                # A pause inside a raw atom likewise cannot locate a word cut.
                before = (
                    prev["protected"]
                    or row.locked
                    or (prev["speaker"] and c.speaker and prev["speaker"] != c.speaker)
                    or gap >= 1.8
                    or quiet_ms >= 1200
                )
            atom.update(
                source_id=cues[source_index].id,
                source_index=source_index,
                source_indices=list(row.indices),
                speaker=c.speaker,
                protected=row.locked,
                boundary_before=bool(before),
                speaker_change_hint=speaker_hint,
                quiet_hint_ms=quiet_ms,
            )
            units.append(atom)
    report.update(
        unit_count=len(units),
        fallback_source_count=fallbacks,
        timing_bases=dict(Counter(u["timing_basis"] for u in units)),
    )
    return units, report
