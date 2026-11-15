"""Join a completed Qwen raw checkpoint and a real tail retry without re-ASR.

Original failure/partial artifacts are copied before replacement. Alignment
errors stay visible and their raw text is retained; no timestamps are guessed.
"""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tingmu.media import _alignment_to_cues, save_srt, MediaError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("tail_report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    partial_path = args.report.with_suffix(".alignment.json")
    partial = json.loads(partial_path.read_text(encoding="utf-8"))
    tail = json.loads(args.tail_report.read_text(encoding="utf-8"))
    tail_alignment = json.loads(args.tail_report.with_suffix(".alignment.json").read_text(encoding="utf-8"))
    assert not report["success"] and tail["success"]
    assert all(report[k] == tail[k] for k in ("model", "language", "audio_track"))
    last_end = partial["chunks"][-1]["start"] + partial["chunks"][-1]["duration"]
    assert abs(last_end - tail["start_seconds"]) < 0.01, "Tail does not adjoin original checkpoint"
    chunks = partial["chunks"] + tail_alignment["chunks"]
    source = f"{args.media.resolve()}|{report['audio_track']}|{report['model']}|{report['language']}"
    cues, warnings = [], []
    for index, chunk in enumerate(chunks):
        try:
            batch = _alignment_to_cues(chunk["text"], [SimpleNamespace(**unit) for unit in chunk["alignment"]],
                                       source=source, offset=chunk["start"], duration=chunk["duration"], chunk_index=index)
            if chunk["text"].strip():
                assert "".join(c.original_text for c in batch) == chunk["text"], "Raw text changed"
            cues.extend(batch)
        except MediaError as exc:
            chunk["alignment_error"] = str(exc)
            warnings.append({"start": chunk["start"], "duration": chunk["duration"], "error": str(exc)})
    source_start, source_end = report["start_seconds"], report["start_seconds"] + report["duration_seconds"]
    assert abs(chunks[0]["start"] - source_start) < 0.01
    assert abs(chunks[-1]["start"] + chunks[-1]["duration"] - source_end) < 0.05
    assert all(source_start <= cue.start < cue.end <= source_end for cue in cues)
    failed_backup = args.report.with_name(args.report.stem + "-initial-failure.json")
    partial_backup = args.report.with_name(args.report.stem + "-initial-partial.alignment.json")
    shutil.copy2(args.report, failed_backup)
    shutil.copy2(partial_path, partial_backup)
    initial_wall = report["wall_seconds"]
    report["initial_failure"] = {"error": report.pop("error"), "wall_seconds": initial_wall,
                                 "artifact": failed_backup.name}
    report.update({"success": True, "recovered_from_checkpoints": True,
                   "processed_entire_requested_audio": True, "cue_count": len(cues),
                   "cues": [asdict(cue) for cue in cues], "uncertain_count": sum(c.uncertain for c in cues),
                   "timestamps_in_requested_range": True, "alignment_warnings": warnings,
                   "wall_seconds": round(initial_wall + tail["wall_seconds"], 3),
                   "resume_run": {"artifact": args.tail_report.name, "wall_seconds": tail["wall_seconds"]},
                   "actual_runtime": dict(tail["actual_runtime"], peak_cuda_allocated_bytes_scope="tail_resume_only")})
    report["realtime_factor_including_model_load_download"] = round(report["wall_seconds"] / report["duration_seconds"], 4)
    report["events"].extend(dict(event, elapsed_seconds=round(initial_wall + event["elapsed_seconds"], 3)) for event in tail["events"])
    raw_text = "".join(chunk["text"] for chunk in chunks)
    report["particle_occurrences_not_accuracy"] = {c: raw_text.count(c) for c in "喎啫㗎嘅咗啦唔冇係喺嚟咩哋"}
    partial_path.write_text(json.dumps({"complete": True, "recovered_from_checkpoints": True,
                                       "model": partial["model"], "runtime": report["actual_runtime"],
                                       "chunks": chunks}, ensure_ascii=False, indent=2), encoding="utf-8")
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    save_srt(cues, args.report.with_suffix(".srt"))
    print(json.dumps({k: report[k] for k in ("success", "cue_count", "uncertain_count", "wall_seconds", "alignment_warnings", "particle_occurrences_not_accuracy")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
