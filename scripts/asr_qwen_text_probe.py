"""Early Qwen text-quality probe without waiting for aligner weights.

Run with .cache/qwen-venv/Scripts/python.exe. Report windows are input ranges,
not inferred subtitle times; this file is deliberately not a cue document.
"""
import argparse
from importlib.metadata import version
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from tingmu.media import extract_audio, _read_pcm_audio, _cache_root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media", type=Path)
    parser.add_argument("--start", type=float, default=180)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--model", default="Qwen/Qwen3-ASR-1.7B")
    parser.add_argument("--output", type=Path, default=Path("reports/private/asr_qwen_text.json"))
    args = parser.parse_args()
    import torch
    from huggingface_hub import snapshot_download
    from qwen_asr import Qwen3ASRModel
    began = time.perf_counter()
    report = {"model": args.model, "language": "Cantonese", "device": "cuda", "dtype": "bfloat16",
              "source_start": args.start, "source_duration": args.duration, "chunks": [],
              "timing_kind": "input_windows_only_not_subtitle_alignment", "quality_status": "未经人工逐字校对",
              "packages": {p: version(p) for p in ["torch", "qwen-asr", "transformers"]}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        path = snapshot_download(args.model, cache_dir=_cache_root() / "qwen-models", local_files_only=True)
        model = Qwen3ASRModel.from_pretrained(path, dtype=torch.bfloat16, device_map="cuda:0",
                                             attn_implementation="sdpa", max_inference_batch_size=1,
                                             max_new_tokens=512)
        print(f"Model loaded in {time.perf_counter()-began:.2f}s", flush=True)
        report["model_load_seconds"] = round(time.perf_counter()-began, 3)
        with tempfile.TemporaryDirectory(prefix="qwen-text-", dir=_cache_root()) as directory:
            wav = extract_audio(args.media, Path(directory) / "audio.wav", start=args.start, duration=args.duration)
            audio = _read_pcm_audio(str(wav))
            for begin in range(0, len(audio), 30 * 16000):
                clip = audio[begin:begin + 30 * 16000]
                t = time.perf_counter()
                with torch.inference_mode():
                    result = model.transcribe(audio=(clip, 16000), language="Cantonese", return_time_stamps=False)[0]
                report["chunks"].append({"input_start": args.start + begin/16000,
                                          "input_end": args.start + (begin+len(clip))/16000,
                                          "text": result.text, "language": result.language,
                                          "wall_seconds": round(time.perf_counter()-t, 3)})
                print(json.dumps(report["chunks"][-1], ensure_ascii=False), flush=True)
                args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report["success"] = True
        text = "".join(c["text"] for c in report["chunks"])
        report["particle_occurrences_not_accuracy"] = {c: text.count(c) for c in "喎啫㗎嘅咗啦唔冇係喺嚟咩哋"}
        report["peak_cuda_allocated_bytes"] = torch.cuda.max_memory_allocated()
    except Exception as exc:
        report["success"] = False
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        report["wall_seconds"] = round(time.perf_counter()-began, 3)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
