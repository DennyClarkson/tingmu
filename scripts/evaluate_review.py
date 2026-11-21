"""Opt-in text-only review benchmark; transcript output stays private."""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from tingmu.models import Cue, LanguagePair  # noqa: E402
from tingmu.review import REVIEW_VERSION, TranscriptReviewer  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--input", type=Path, required=True, help="Local ASR JSON with cues")
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--models", nargs="+", choices=["deepseek-flash", "gpt-6-astra", "gpt-6.1-sol"],
                        default=["deepseek-flash"])
    parser.add_argument("--output", type=Path, default=ROOT / "reports/review-evaluation.json")
    args = parser.parse_args()
    if not args.live:
        parser.error("Explicit --live is required for paid text-only model calls.")
    if not 1 <= args.count <= 20 or args.offset < 0:
        parser.error("Use 1–20 cues and a nonnegative offset for this bounded benchmark.")
    data = json.loads(args.input.read_text(encoding="utf-8-sig"))
    raw = data["cues"] if isinstance(data, dict) else data
    cues = [Cue(**{k: row[k] for k in ("id", "start", "end", "text", "original_text", "uncertain")
                   if k in row}) for row in raw[args.offset:args.offset + args.count]]
    if not cues:
        parser.error("No cues in selected range.")
    language = LanguagePair()
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "review_version": REVIEW_VERSION,
              "scope": "Bounded text-only review; no audio; no human reference or accuracy claim.", "results": []}
    private = ROOT / "reports/private/review-evaluation"
    private.mkdir(parents=True, exist_ok=True)
    for model in args.models:
        reviewer = TranscriptReviewer(provider="deepseek" if model == "deepseek-flash" else "openai", model=model)
        started = time.monotonic()
        item = {"requested_model": model, "input_cues": len(cues), "success": False}
        try:
            output = reviewer.review(cues, language)
            item.update(success=True, checks={
                "ids": [c.id for c in output] == [c.id for c in cues],
                "times": [(c.start, c.end) for c in output] == [(c.start, c.end) for c in cues],
                "originals": [c.original_text for c in output] == [c.original_text for c in cues],
                "teaching_reset": all(not c.tokens and not c.translation and not c.explanation for c in output),
            })
            item["states"] = {state: sum(m["state"] == state for m in reviewer.review_metadata.values())
                              for state in ("corrected", "retained", "uncertain", "reviewed_unchanged")}
            (private / f"{model}.json").write_text(json.dumps({
                "input": [asdict(c) for c in cues], "output": [asdict(c) for c in output],
                "review_metadata": reviewer.review_metadata,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            item["error_type"] = type(exc).__name__
        item["wall_seconds"] = round(time.monotonic() - started, 3)
        item["requests"] = [{k: v for k, v in r.items() if k not in ("review_results", "validation_error")}
                            for r in reviewer.response_metadata]
        item["context"] = reviewer.context_metadata
        report["results"].append(item)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(item, ensure_ascii=True), flush=True)
    return 0 if all(r["success"] for r in report["results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
