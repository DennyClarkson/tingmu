"""Opt-in real local-ASR/cloud-text batch validation, with pause/restart evidence."""

import argparse
from pathlib import Path
import json
import time

from tingmu.batch import PreparationQueue
from tingmu.models import AppSettings
from tingmu.storage import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("reports/private/batch-v2.json"))
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for real text API calls")
    paths = sorted(args.folder.resolve().glob("*.mkv"))
    if len(paths) < 2:
        parser.error("Provide at least two clips")
    store = Store(args.data_dir)
    settings = AppSettings(asr_model="qwen3-asr-1.7b", device="cuda", review_enabled=True)
    queue = PreparationQueue(store)
    queue.enqueue(paths, settings)
    started = time.monotonic()

    def progress(message):
        print(message, flush=True)
        if "已准备 " in message:
            queue.pause()

    queue.run(progress=progress)
    first_run = queue.snapshot()
    resumed = PreparationQueue(Store(args.data_dir))
    resumed.run(progress=lambda message: print(message, flush=True))
    report = {"clip_count": len(paths), "first_run_statuses": [j["status"] for j in first_run],
              "final_jobs": resumed.snapshot(), "wall_seconds": round(time.monotonic() - started, 3),
              "all_complete": all(j["status"] == "completed" for j in resumed.snapshot())}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "final_jobs"}, ensure_ascii=False), flush=True)
    return 0 if report["all_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
