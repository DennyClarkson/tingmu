"""Import a locally generated asr_probe report without repeating GPU inference."""

import argparse
import json
from pathlib import Path

from tingmu.agent import annotate_local
from tingmu.models import AppSettings, Cue, StudyDocument
from tingmu.storage import Store, media_fingerprint


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("media", type=Path)
    parser.add_argument("--data-dir", type=Path)
    args = parser.parse_args()
    raw = json.loads(args.report.read_text(encoding="utf-8"))
    if not raw.get("success"):
        raise ValueError("Only successful ASR reports can be imported")
    media = args.media.resolve()
    settings = AppSettings(library_path=str(media.parent), asr_model=raw["model"],
                           audio_track=raw["audio_track"], device="cuda")
    store = Store(args.data_dir)
    cues = [Cue(**c) for c in raw["cues"]]
    for cue in cues:
        if cue.end - cue.start > 15:
            cue.uncertain = True
    duration = raw.get("duration_seconds")
    if raw.get("start_seconds", 0) == 0 and duration is not None and duration >= raw.get("media_duration_seconds", float("inf")):
        duration = None
    doc = StudyDocument(str(media), settings.language, annotate_local(cues, settings.language), metadata={
        "stage": "transcribed", "completed_ids": [],
        "raw_display_text": {c.id: c.text for c in cues},
        "media_fingerprint": media_fingerprint(media),
        "transcript_spec": {"audio_track": raw["audio_track"], "asr_model": raw["model"], "target": "yue",
                            "start": raw.get("start_seconds", 0), "duration": duration,
                            "subtitle": None, "subtitle_fingerprint": None},
        "asr_device": raw["requested_device"], "asr_wall_seconds": raw["wall_seconds"],
        "asr_runtime": raw.get("actual_runtime", {}),
        "asr_audit_path": str(args.report.with_suffix(".alignment.json").resolve()),
        "alignment_warnings": raw.get("alignment_warnings", []),
        "quality_note": "机器转写，未逐句听音校对；粤语可能被识别成书面语。",
    })
    path = store.save_document(doc)
    store.save_settings(settings)
    print(f"Imported {len(cues)} cues: {path}")


if __name__ == "__main__":
    main()
