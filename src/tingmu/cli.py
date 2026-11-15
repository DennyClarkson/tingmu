"""Desktop launcher and reproducible diagnostics/preparation commands."""

import argparse
import json
from pathlib import Path
import shutil
import sys


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="tingmu", description="听幕 · 看剧学语言")
    parser.add_argument("--data-dir", type=Path, help="学习数据目录")
    commands = parser.add_subparsers(dest="command")
    gui = commands.add_parser("gui", help="打开播放器")
    gui.add_argument("path", nargs="?", help="视频文件或文件夹")
    prepare = commands.add_parser("prepare", help="准备字幕与学习材料")
    prepare.add_argument("path", type=Path)
    prepare.add_argument("--subtitle", type=Path)
    prepare.add_argument("--source", default="zh-CN")
    prepare.add_argument("--target", default="yue")
    prepare.add_argument("--provider", choices=["local", "deepseek", "openai"], default="local")
    prepare.add_argument("--model")
    prepare.add_argument("--asr-model", default="large-v3-turbo")
    prepare.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    prepare.add_argument("--audio-track", type=int, default=0)
    prepare.add_argument("--start", type=float, default=0)
    prepare.add_argument("--duration", type=float)
    prepare.add_argument("--srt", type=Path)
    doctor = commands.add_parser("doctor", help="检查本地运行环境（不输出密钥）")
    doctor.add_argument("--media", type=Path)
    args = parser.parse_args(argv)
    if args.command == "doctor":
        from .agent import provider_status
        from .media import probe_media
        report = {"python": sys.version.split()[0], "ffmpeg": shutil.which("ffmpeg"),
                  "ffprobe": shutil.which("ffprobe"), "providers": provider_status()}
        if args.media:
            info = probe_media(args.media)
            report["media"] = {"duration": info.get("format", {}).get("duration"),
                               "streams": [{k: s[k] for k in ("index", "codec_name", "codec_type", "tags") if k in s}
                                           for s in info.get("streams", [])]}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "prepare":
        from .models import AppSettings
        from .pipeline import prepare_media
        from .storage import Store
        from .media import save_srt
        settings = AppSettings(source_language=args.source, target_language=args.target,
                               provider=args.provider, model=args.model or (
                                   "gpt-5.4-mini" if args.provider == "openai" else "deepseek-flash"),
                               cloud_text_enabled=args.provider != "local", asr_model=args.asr_model,
                               device=args.device, audio_track=args.audio_track)
        store = Store(args.data_dir)
        try:
            doc = prepare_media(args.path, settings, store, args.subtitle, args.start, args.duration,
                                progress=lambda s: print(s, flush=True))
            if args.srt:
                save_srt(doc.cues, args.srt)
            print(f"材料：{store.save_document(doc)}")
            return 0
        except KeyboardInterrupt:
            print("已中断", file=sys.stderr)
            return 130
        except Exception as exc:
            print(f"准备失败：{exc}", file=sys.stderr)
            return 1
    from .ui import run_app
    return run_app(getattr(args, "path", None), args.data_dir)


if __name__ == "__main__":
    raise SystemExit(main())
