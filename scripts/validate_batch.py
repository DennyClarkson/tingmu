"""Opt-in real local-ASR/cloud-text batch validation, with pause/restart evidence."""

import argparse
from dataclasses import asdict
from pathlib import Path
import json
import time

from tingmu.batch import PreparationQueue
from tingmu.models import AppSettings, preparation_identity_settings
from tingmu.storage import Store, canonical_path


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
    settings = AppSettings(asr_model="qwen3-asr-1.7b", device="cuda", review_enabled=True,
                           provider="deepseek", model="deepseek-flash",
                           review_provider="deepseek", review_model="deepseek-flash")
    queue = PreparationQueue(store)
    queue.enqueue(paths, settings)
    media = {canonical_path(path) for path in paths}
    spec = preparation_identity_settings(asdict(settings))
    ids = {j["id"] for j in queue.snapshot() if j["media_path"] in media
           and preparation_identity_settings(j["settings"]) == spec}
    started = time.monotonic()

    def progress(message):
        print(message, flush=True)
        if "已准备 " in message:
            queue.pause()

    queue.run(progress=progress, job_ids=ids)
    first_run = [job for job in queue.snapshot() if job["id"] in ids]
    resumed = PreparationQueue(Store(args.data_dir))
    resumed.run(progress=lambda message: print(message, flush=True), job_ids=ids)
    final = [job for job in resumed.snapshot() if job["id"] in ids]
    report = {"clip_count": len(paths), "first_run_statuses": [j["status"] for j in first_run],
              "final_jobs": final, "wall_seconds": round(time.monotonic() - started, 3),
              "all_complete": all(j["status"] == "completed" for j in final)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "final_jobs"}, ensure_ascii=False), flush=True)
    return 0 if report["all_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
