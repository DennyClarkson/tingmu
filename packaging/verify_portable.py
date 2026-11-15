"""Run using the shipped interpreter after moving the whole portable directory."""
import argparse
from dataclasses import asdict
from importlib.metadata import version
import json
import os
from pathlib import Path
import shutil
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    import tingmu
    from tingmu.runtime import portable_root, cache_root, data_root
    from tingmu.media import transcribe, probe_media
    from tingmu.storage import Store
    root = portable_root()
    assert root is not None
    for path in sys.path:
        assert Path(path).is_relative_to(root), path
    assert Path(tingmu.__file__).is_relative_to(root)
    assert Path(sys.base_prefix).is_relative_to(root)
    assert Store().data_dir == root / "data"
    assert Path(shutil.which("ffmpeg")).is_relative_to(root)
    assert Path(shutil.which("ffprobe")).is_relative_to(root)
    metadata = {}
    began = time.perf_counter()
    cues = transcribe(args.media, "yue", "qwen3-asr-1.7b", "cuda", 0, 0, 10,
                      progress=print, metadata_out=metadata,
                      raw_output=args.output.with_suffix(".alignment.json"))
    assert cues, "No ASR cues"
    assert metadata["asr_device"] == "cuda", metadata
    report = {"passed": True, "root": str(root), "python": sys.version,
              "python_executable": sys.executable, "base_prefix": sys.base_prefix,
              "application_module": tingmu.__file__, "sys_path": sys.path,
              "data": str(data_root()), "cache": str(cache_root()),
              "qwen_python": os.environ["TINGMU_QWEN_PYTHON"],
              "qt": version("PySide6"), "ffmpeg": shutil.which("ffmpeg"),
              "ffprobe": shutil.which("ffprobe"), "asr_metadata": metadata,
              "cue_count": len(cues), "seconds": round(time.perf_counter() - began, 3),
              "media_duration": probe_media(args.media)["format"]["duration"],
              "cues": [asdict(cue) for cue in cues]}
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "cue_count": len(cues), "seconds": report["seconds"]}))


if __name__ == "__main__":
    main()
