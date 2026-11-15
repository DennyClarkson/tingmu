"""Reproducible local ASR smoke/throughput probe; never sends media to a server."""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version, PackageNotFoundError
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tingmu.media import probe_media, transcribe, save_srt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media", type=Path)
    parser.add_argument("--start", type=float, default=180)
    parser.add_argument("--duration", type=float, default=120)
    parser.add_argument("--audio-track", type=int, default=0)
    parser.add_argument("--language", default="yue")
    parser.add_argument("--model", default="large-v3-turbo")
    parser.add_argument("--device", choices=["auto", "cuda", "cpu"], default="cuda")
    parser.add_argument("--initial-prompt", default=None)
    parser.add_argument("--output", type=Path, default=Path("reports/private/asr_probe.json"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    events = []
    began = time.perf_counter()
    def progress(message):
        event = {"elapsed_seconds": round(time.perf_counter() - began, 3), "message": message}
        events.append(event)
        print(f"[{event['elapsed_seconds']:.1f}s] {message}", flush=True)
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "media_filename": args.media.name,
              "start_seconds": args.start, "duration_seconds": args.duration, "audio_track": args.audio_track,
              "language": args.language, "model": args.model, "requested_device": args.device,
              "initial_prompt": args.initial_prompt,
              "packages": {}, "events": events, "quality_status": "机器原始识别，未人工校对；无参考逐字稿，不能计算 WER/CER。"}
    for package in ["faster-whisper", "ctranslate2", "nvidia-cublas-cu12", "nvidia-cudnn-cu12", "nvidia-cuda-runtime-cu12"]:
        try:
            report["packages"][package] = version(package)
        except PackageNotFoundError:
            report["packages"][package] = None
    try:
        gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=10)
        report["gpu"] = gpu.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        report["gpu"] = "nvidia-smi unavailable"
    try:
        media = probe_media(args.media)
        report["media_duration_seconds"] = float(media.get("format", {}).get("duration", 0))
        report["audio_tracks"] = [{"ordinal": t["ordinal"], "index": t["index"], "codec_name": t.get("codec_name"),
                                  "tags": t.get("tags", {})} for t in media["audio_tracks"]]
        cues = transcribe(args.media, args.language, args.model, args.device, args.audio_track,
                          args.start, args.duration, progress=progress, initial_prompt=args.initial_prompt,
                          raw_output=args.output.with_suffix(".alignment.json"))
        report["success"] = True
        report["cues"] = [asdict(cue) for cue in cues]
        report["cue_count"] = len(cues)
        report["uncertain_count"] = sum(cue.uncertain for cue in cues)
        report["timestamps_in_requested_range"] = all(args.start <= c.start < c.end <= args.start + args.duration for c in cues)
        alignment_path = args.output.with_suffix(".alignment.json")
        if alignment_path.is_file() and "qwen3-asr" in args.model.lower():
            alignment = json.loads(alignment_path.read_text(encoding="utf-8"))
            report["actual_runtime"] = alignment["runtime"]
            report["alignment_warnings"] = [{"start": c["start"], "duration": c["duration"],
                                             "error": c["alignment_error"]} for c in alignment["chunks"]
                                            if c.get("alignment_error")]
        transcript = "".join(c.text for c in cues)
        report["particle_occurrences_not_accuracy"] = {c: transcript.count(c) for c in "喎啫㗎嘅咗啦唔冇係喺嚟咩哋"}
        save_srt(cues, args.output.with_suffix(".srt"))
    except Exception as exc:
        report["success"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        print(report["error"], file=sys.stderr)
    finally:
        report["wall_seconds"] = round(time.perf_counter() - began, 3)
        report["realtime_factor_including_model_load_download"] = round(report["wall_seconds"] / args.duration, 4)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report: {args.output.resolve()}", flush=True)
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
