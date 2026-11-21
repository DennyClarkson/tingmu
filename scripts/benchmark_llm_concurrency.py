"""Run a live, text-only DeepSeek Flash concurrency comparison in isolated stores."""
import argparse
from collections import Counter
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch

from tingmu import agent
from tingmu.media import load_subtitles, save_srt
from tingmu.models import MAX_LLM_CONCURRENCY, AppSettings, Cue, StudyDocument
from tingmu.pipeline import prepare_media
from tingmu.review import REVIEW_PROMPT
from tingmu.storage import Store, canonical_path, media_fingerprint
from tingmu.translation_review import translation_verified


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send the selected subtitle text to DeepSeek")
    parser.add_argument("--asr-checkpoint", action="store_true", help="Start with unchanged production ASR IDs, raw text and timestamps")
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--document", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=96)
    parser.add_argument("--workers", nargs="+", type=int, default=[4, 1], choices=range(1, MAX_LLM_CONCURRENCY + 1))
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required for provider calls")
    project_dir = Path(__file__).resolve().parents[1]
    args.output = args.output.resolve()
    if not args.output.is_relative_to(project_dir) or args.output == project_dir:
        parser.error("--output must be a directory inside the project")
    source = StudyDocument.from_dict(json.loads(args.document.read_text(encoding="utf-8")))
    if canonical_path(args.media) != canonical_path(source.media_path):
        parser.error("The supplied media path does not match the source document")
    fingerprint = media_fingerprint(args.media)
    if not fingerprint or source.metadata.get("media_fingerprint") != fingerprint:
        parser.error("The supplied media fingerprint does not match the source document")
    if not 1 <= args.count <= len(source.cues):
        parser.error("Requested cue count is outside the source document")
    raw = [replace(c, text=c.original_text or c.text) for c in source.cues[:args.count]]
    args.output.mkdir(parents=True, exist_ok=True)
    sidecar = args.output / "raw.srt"
    if args.asr_checkpoint:
        original = [Cue(c.id, c.start, c.end, c.text, original_text=c.text, speaker=c.speaker) for c in raw]
    else:
        if not sidecar.exists():
            save_srt(raw, sidecar)
        original = load_subtitles(sidecar)

    def identity(cue):
        return (cue.id, cue.original_text or cue.text, cue.start, cue.end)

    assert len(original) == args.count and all(
        c.text == r.text and abs(c.start-r.start) <= .00051 and abs(c.end-r.end) <= .00051
        for c, r in zip(original, raw))
    source_bytes = (json.dumps([identity(c) for c in original], ensure_ascii=False).encode()
                    if args.asr_checkpoint else sidecar.read_bytes())
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    for workers in args.workers:
        store = Store(args.output / f"workers-{workers}")
        settings = AppSettings(source_language=source.language.source, target_language=source.language.target,
                               level=source.language.level, provider="deepseek", model="deepseek-flash",
                               review_provider="deepseek", review_model="deepseek-flash",
                               llm_high_concurrency=workers > 1, llm_max_concurrency=workers)
        cached = store.load_document(str(args.media.resolve()), settings.language)
        if cached and cached.metadata.get("stage") == "ready":
            raise SystemExit(f"Worker count {workers} already finished. Use a fresh output directory for a new comparison.")
        initial_stage = cached.metadata.get("stage") if cached else "fresh"
        if args.asr_checkpoint:
            spec = source.metadata["transcript_spec"]
            settings.asr_model = spec["asr_model"]
            settings.audio_track = spec["audio_track"]
            settings.suppress_bgm = spec["suppress_bgm"]
            settings.speaker_turns = spec["speaker_turns"]
            if not cached:
                store.save_document(StudyDocument(str(args.media.resolve()), settings.language, original, metadata={
                    "transcript_spec": spec, "completed_ids": [], "stage": "transcribed",
                    "raw_display_text": {c.id: c.text for c in original},
                }))
        lock = threading.Lock()
        started = time.monotonic()
        counts = Counter()
        peaks = Counter()
        durations = Counter()
        returned_models = set()
        active = 0
        stage_starts = {}
        stage_ends = {}
        provider_request = agent.provider_request

        def measured(config, path, body=None, **kwargs):
            nonlocal active
            prompt = body["messages"][0]["content"]
            if "final bilingual subtitle translation reviewer" in prompt:
                stage = "translation"
            elif prompt.startswith(REVIEW_PROMPT):
                stage = "review"
            else:
                stage = "teaching"
            call_start = time.monotonic()
            with lock:
                active += 1
                peaks[stage] = max(peaks[stage], active)
                counts[stage] += 1
                stage_starts.setdefault(stage, call_start)
            try:
                response = provider_request(config, path, body, **kwargs)
                with lock:
                    returned_models.add(response.get("model"))
                return response
            finally:
                with lock:
                    active -= 1
                    end = time.monotonic()
                    durations[stage] += end - call_start
                    stage_ends[stage] = end

        journal = args.output / f"attempts-{workers}.jsonl"
        error = ""
        try:
            with (patch.object(agent, "provider_request", measured),
                  patch("tingmu.pipeline.transcribe", side_effect=RuntimeError("Missing ASR checkpoint"))):
                doc = prepare_media(args.media, settings, store, subtitle_path=None if args.asr_checkpoint else sidecar,
                                    force_asr=args.asr_checkpoint and spec.get("force_asr", False),
                                    progress=lambda msg: print(f"[{workers}] {msg}", flush=True))
            review = doc.metadata["translation_review"]
            assert doc.metadata["stage"] == "ready" and len(doc.cues) == args.count
            assert [identity(c) for c in doc.cues] == [identity(c) for c in original]
            assert all(c.text.strip() and c.translation.strip() and c.explanation.strip() and
                       c.tokens and "".join("".join(t.text for t in c.tokens).split()) == "".join(c.text.split()) and
                       translation_verified(c, review) for c in doc.cues)
            assert len(doc.metadata["review_completed_ids"]) == len(doc.metadata["completed_ids"]) == args.count
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            summary = dict(workers=workers, initial_stage=initial_stage, cue_count=args.count, source_sha256=source_hash,
                           run_id=args.output.resolve().name, source_document=str(args.document.resolve()),
                           media=str(args.media.resolve()), media_fingerprint=fingerprint,
                           store_path=str(store.data_dir.resolve()),
                           source_mode="raw_asr_checkpoint" if args.asr_checkpoint else "srt",
                           seconds=round(time.monotonic()-started, 3), calls=counts, peaks=peaks,
                           request_seconds={s: round(v, 3) for s, v in durations.items()},
                           stage_wall_seconds={s: round(stage_ends[s]-t, 3) for s, t in stage_starts.items()},
                           returned_models=sorted(returned_models), error=error)
            line = json.dumps(summary, ensure_ascii=False)
            with journal.open("a", encoding="utf8") as stream:
                stream.write(line + "\n")
            print(line, flush=True)


if __name__ == "__main__":
    main()
