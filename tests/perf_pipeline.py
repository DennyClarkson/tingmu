"""Supplemental offline queue benchmark. Point PYTHONPATH at the source tree under test."""

import argparse
from collections import Counter
from contextlib import closing
import json
import os
from pathlib import Path
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns
from unittest.mock import patch
import wave


def no_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise RuntimeError("Offline pipeline benchmark attempted network access")


def stamp(milliseconds):
    seconds, ms = divmod(milliseconds, 1000)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"


def fixtures(directory, clips, cues):
    directory.mkdir()
    paths = []
    for clip in range(clips):
        path = directory / f"sample-{clip}.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 1, 8000, 0, "NONE", "NONE"))
            audio.writeframes(bytes([128]) * (8000 * cues))
        srt = "".join(f"{i+1}\n{stamp(i*1000)} --> {stamp(i*1000+850)}\n"
                      f"Local study sentence {i+1} in recording {clip+1}.\n\n" for i in range(cues))
        path.with_suffix(".en.srt").write_text(srt, encoding="utf-8")
        paths.append(path)
    return paths


def sample(directory, clips, cues):
    from tingmu.batch import PreparationQueue
    from tingmu.models import AppSettings
    from tingmu.storage import Store

    paths = fixtures(directory / "inputs", clips, cues)
    settings = AppSettings(source_language="zh-CN", target_language="en", preparation_workflow="classic",
                           provider="local", review_enabled=False, text_models_enabled=False,
                           cloud_text_enabled=False, allow_audio_upload=False)
    with closing(Store(directory / "data")) as store:
        store.save_settings(settings)
        queue = PreparationQueue(store)
        assert len(queue.enqueue(paths, settings)) == clips
        syncs = 0
        stages = Counter()
        real_sync = os.fsync
        real_save = store.save_document

        def sync(fd):
            nonlocal syncs
            real_sync(fd)
            syncs += 1

        def save(document, **kwargs):
            result = real_save(document, **kwargs)
            stages[document.metadata["stage"]] += 1
            return result

        with patch("os.fsync", sync), patch.object(store, "save_document", save):
            start = perf_counter_ns()
            jobs = queue.run(allow_model_downloads=False)
            elapsed = (perf_counter_ns() - start) / 1e9

        assert len(jobs) == clips and all(job["status"] == "completed" for job in jobs), jobs
        assert stages["ready"] == clips and sum(stages.values()) > clips
        for media in paths:
            doc = store.load_document(str(media), settings.language)
            assert doc.metadata["stage"] == "ready" and doc.metadata["lesson_status"] == "local_only"
            assert len(doc.cues) == cues and all(cue.tokens for cue in doc.cues)
            assert doc.cues[0].start == 0 and abs(doc.cues[-1].end - (cues - .15)) < .001
        assert len(list((store.data_dir / "documents").glob("*.srt"))) == clips
        return {"seconds": elapsed, "cues_per_second": clips * cues / elapsed,
                "python_fsyncs": syncs, "document_save_stages": dict(stages)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-src", type=Path, required=True, help="Directory containing tingmu/")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--clips", type=int, default=4)
    parser.add_argument("--cues", type=int, default=200)
    args = parser.parse_args()
    if min(args.repeats, args.clips, args.cues) < 1:
        parser.error("The workload and sample count must be positive.")
    sys.addaudithook(no_network)
    import tingmu

    source = Path(tingmu.__file__).resolve()
    expected = args.expected_src.resolve() / "tingmu" / "__init__.py"
    if source != expected:
        raise RuntimeError(f"Expected {expected}, imported {source}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    runs = []
    for _ in range(args.repeats + 1):
        with TemporaryDirectory(dir=args.output.parent) as run:
            runs.append(sample(Path(run), args.clips, args.cues))
    warmup, samples = runs[0], runs[1:]
    result = {"source": str(source), "python": sys.version.split()[0], "clips": args.clips,
              "cues_per_clip": args.cues, "warmup": warmup, "samples": samples,
              "medians": {key: median(row[key] for row in samples)
                          for key in ("seconds", "cues_per_second", "python_fsyncs")}}
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["medians"]), source, flush=True)


if __name__ == "__main__":
    main()
