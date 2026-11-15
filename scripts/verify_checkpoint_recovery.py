"""Opt-in, text-only full-job recovery in an isolated copy of a saved checkpoint.

Never edits the supplied data directory or invokes ASR. Reports contain counts
and validation results, not subtitle text or credentials.
"""
import argparse
from dataclasses import fields
import hashlib
import json
from pathlib import Path
import shutil
import time

from tingmu.batch import PreparationQueue
from tingmu.diagnostics import AppLog
from tingmu.models import AppSettings
import tingmu.pipeline as pipeline
from tingmu.storage import Store, atomic_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source = args.data.resolve()
    output = args.output.resolve()
    if source == output or source in output.parents or output in source.parents:
        raise ValueError("Validation output must be separate from the original data directory")
    jobs = json.loads((source / "preparation-queue.json").read_text(encoding="utf-8"))["jobs"]
    job = next(j for j in jobs if j["id"] == args.job)
    allowed = {f.name for f in fields(AppSettings)}
    settings = AppSettings(**{k: v for k, v in job["settings"].items() if k in allowed})
    # Store construction is read-only for existing document data, but use a raw
    # JSON scan here so validation cannot migrate or initialize the user's DB.
    from tingmu.models import StudyDocument
    from tingmu.storage import canonical_path
    original = None
    for path in (source / "documents").glob("*.json"):
        value = StudyDocument.from_dict(json.loads(path.read_text(encoding="utf-8")))
        if canonical_path(value.media_path) == canonical_path(job["media_path"]) and value.language == settings.language:
            original, original_path = value, path
            break
    if original is None:
        raise ValueError("No saved recognition checkpoint for this job")
    output.mkdir(parents=True, exist_ok=True)
    store = Store(output / "data")
    baseline_path = output / "baseline.json"
    if not baseline_path.exists():
        shutil.copy2(original_path, baseline_path)
        store.save_document(original)
        atomic_json(store.data_dir / "preparation-queue.json", {"version": 1, "jobs": [job]})
        if (source / "model-profiles.json").exists():
            shutil.copy2(source / "model-profiles.json", store.data_dir / "model-profiles.json")
    else:
        saved = json.loads((store.data_dir / "preparation-queue.json").read_text(encoding="utf-8"))["jobs"]
        baseline_value = StudyDocument.from_dict(json.loads(baseline_path.read_text(encoding="utf-8")))
        if (len(saved) != 1 or saved[0]["id"] != job["id"]
                or saved[0]["settings"] != job["settings"]
                or saved[0].get("force_asr") != job.get("force_asr")
                or canonical_path(baseline_value.media_path) != canonical_path(job["media_path"])
                or baseline_value.language != settings.language
                or baseline_path.read_bytes() != original_path.read_bytes()):
            raise ValueError("Existing validation output belongs to a different job, settings or source checkpoint")
    baseline_bytes = baseline_path.read_bytes()
    baseline = StudyDocument.from_dict(json.loads(baseline_bytes))
    queue = PreparationQueue(store)
    queue.retry(job["id"])
    log = AppLog(store.data_dir)

    def forbid_recognition(*args, **kwargs):
        raise AssertionError("Recovery attempted to repeat recognition/subtitle import")

    pipeline.transcribe = pipeline.load_subtitles = forbid_recognition
    started = time.monotonic()
    try:
        result = queue.run(lambda message: print(message, flush=True),
                           on_error=lambda error: log.exception(error, "recovery_error"))[0]
        document = store.load_document(job["media_path"], settings.language)
        metadata = document.metadata
        def identity(c):
            return c.id, c.start, c.end, c.original_text
        checks = {
            "queue_completed": result["status"] == "completed",
            "document_ready": metadata.get("stage") == "ready",
            "review_complete": metadata.get("reviewed_count") == len(document.cues),
            "teaching_complete": metadata.get("annotated_count") == len(document.cues),
            "source_identity_time_and_raw_preserved": [identity(c) for c in baseline.cues] == [identity(c) for c in document.cues],
            "original_recognition_preserved": baseline.metadata["raw_display_text"] == metadata["raw_display_text"],
            "exact_display_segments": all("".join(c.segmented_texts) == c.text for c in document.cues),
            "teacher_preserved_reviewed_text": all(metadata.get("reviewed_text", {}).get(c.id) == c.text for c in document.cues),
            "all_cues_have_teaching": all(c.tokens and c.translation and c.explanation for c in document.cues),
            "original_document_not_written": hashlib.sha256(original_path.read_bytes()).hexdigest() == hashlib.sha256(baseline_bytes).hexdigest(),
        }
        report = {"live_text_only": True, "recognition_disabled": True,
                  "elapsed_seconds": round(time.monotonic() - started, 2), "checks": checks,
                  "original_cues": len(baseline.cues), "initial_reviewed": baseline.metadata.get("reviewed_count", 0),
                  "reviewed": metadata.get("reviewed_count", 0), "annotated": metadata.get("annotated_count", 0),
                  "status": result["status"], "error_type": result.get("error", "")}
        atomic_json(output / "validation.json", report)
        print(json.dumps(report, ensure_ascii=False), flush=True)
        return 0 if all(checks.values()) else 1
    finally:
        log.close()


if __name__ == "__main__":
    raise SystemExit(main())
