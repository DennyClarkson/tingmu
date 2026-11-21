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
    prepare.add_argument("--source")
    prepare.add_argument("--target")
    prepare.add_argument("--provider", choices=["local", "workspace", "deepseek", "openai"], default="local",
                         help="local 使用本地词典，workspace 使用本工作区已分配的文本模型")
    prepare.add_argument("--model")
    prepare.add_argument("--asr-model", help="本机 ASR 模型")
    prepare.add_argument("--asr-backend", choices=["local", "local_api", "external"])
    prepare.add_argument("--asr-endpoint", help="完整的语音转写接口地址")
    prepare.add_argument("--external-asr-model")
    prepare.add_argument("--asr-key-env", help="保存语音服务密钥的环境变量名")
    prepare.add_argument("--asr-response-format", choices=["verbose_json", "diarized_json"])
    prepare.add_argument("--allow-audio-upload", action="store_true", help="本次命令允许发送所选音轨")
    prepare.add_argument("--suppress-bgm", action=argparse.BooleanOptionalAction)
    prepare.add_argument("--speaker-turns", action=argparse.BooleanOptionalAction)
    prepare.add_argument("--device", choices=["auto", "cpu", "cuda"])
    prepare.add_argument("--audio-track", type=int)
    prepare.add_argument("--start", type=float, default=0)
    prepare.add_argument("--duration", type=float)
    prepare.add_argument("--srt", type=Path)
    doctor = commands.add_parser("doctor", help="检查本地运行环境（不输出密钥）")
    doctor.add_argument("--media", type=Path)
    args = parser.parse_args(argv)
    from .runtime import inside_installation
    if args.data_dir:
        try:
            args.data_dir = inside_installation(args.data_dir)
        except ValueError as exc:
            parser.error(str(exc))
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
        from .pipeline import prepare_media
        from .storage import Store
        from .media import save_srt
        from .runtime import data_root
        from .workspaces import WorkspaceCatalog
        from .export_paths import export_path
        store = Store(args.data_dir or WorkspaceCatalog(data_root()).active_dir)
        settings = store.load_settings()
        settings.source_language = args.source or settings.source_language
        settings.target_language = args.target or settings.target_language
        if args.provider != "workspace":
            settings.provider = args.provider
            settings.cloud_text_enabled = args.provider != "local"
            settings.text_models_enabled = args.provider != "local"
            settings.annotation_profile_id = ""
            settings.model = args.model or ("gpt-5.4-mini" if args.provider == "openai" else "deepseek-flash")
        settings.allow_audio_upload = args.allow_audio_upload
        for name in ("asr_model", "device", "audio_track", "asr_backend", "suppress_bgm", "speaker_turns"):
            if getattr(args, name) is not None:
                setattr(settings, name, getattr(args, name))
        keys = {"asr_endpoint": "local_asr_endpoint", "asr_key_env": "local_asr_key_env",
                "external_asr_model": "local_asr_api_model", "asr_response_format": "local_asr_response_format"}
        for name, local_name in keys.items():
            if getattr(args, name) is not None:
                setattr(settings, local_name if settings.asr_backend == "local_api" else name, getattr(args, name))
        try:
            destination = export_path(store.data_dir, args.srt, (".srt",)) if args.srt else None
            doc = prepare_media(args.path, settings, store, args.subtitle, args.start, args.duration,
                                progress=lambda s: print(s, flush=True))
            if destination:
                save_srt(doc.cues, destination)
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
