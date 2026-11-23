"""Assemble a first-run Windows portable folder from the existing bundled runtimes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import tomllib

from qml_runtime import qml_asset_manifest, validate_qml_runtime

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "dist/Tingmu"
PACKAGE_ASSETS = {
    "tagger.pickle", "nagisa_v001.dict", "nagisa_v001.hp", "nagisa_v001.model",
    "trained_models", "test_data", "tests", "test", "__pycache__", "direct_url.json",
}
EXCLUDED_EXTENSIONS = {
    ".onnx", ".safetensors", ".ckpt", ".gguf", ".pt", ".tflite", ".weights", ".whl",
    ".wav", ".mp3", ".mp4", ".mkv", ".webm", ".avi", ".mov", ".ogg", ".flac", ".pyc",
}


def ignore_runtime(directory, names):
    ignored = []
    for name in names:
        path = Path(directory) / name
        if name in PACKAGE_ASSETS or path.suffix.lower() in EXCLUDED_EXTENSIONS:
            ignored.append(name)
        elif name in {"model.bin", "pytorch_model.bin"} or (name.startswith("tingmu-") and name.endswith(".dist-info")):
            ignored.append(name)
        elif path.is_symlink() or path.is_junction():
            raise ValueError(f"Unexpected runtime link: {path}")
    return ignored


def patch_aligner(root):
    # Nagisa eagerly opens its Japanese model on import. Hardware detection must
    # work before the user has chosen to download any ASR or tokenizer weights.
    packages = root / "runtime/asr/Lib/site-packages"
    aligner = packages / "qwen_asr/inference/qwen3_forced_aligner.py"
    code = aligner.read_text("utf-8")
    needle = "        words = nagisa.tagging(text).words"
    if code.count("\nimport nagisa\n") != 1 or code.count(needle) != 1:
        raise ValueError("Review the Qwen tokenizer import for this dependency version")
    aligner.write_text(code.replace("\nimport nagisa\n", "\n").replace(needle, "        import nagisa\n" + needle),
                       encoding="utf-8")

    tagger = packages / "nagisa/tagger.py"
    code = tagger.read_text("utf-8")
    needle = "base + '/data/nagisa_v001."
    if code.count(needle) != 3:
        raise ValueError("Review the Nagisa asset locations for this dependency version")
    cache = "from pathlib import Path\nasset_dir = str(Path(__file__).resolve().parents[5] / '.cache/qwen-support/nagisa-0.2.11')"
    code = code.replace("sys.path.append(base)", "sys.path.append(base)\n" + cache)
    tagger.write_text(code.replace(needle, "asset_dir + '/nagisa_v001."), encoding="utf-8")


def copy_app(root):
    shutil.copytree(ROOT / "src/tingmu", root / "app/tingmu", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("bootstrap.py", "bootstrap_logging.py", "portable_smoke.py", "qml_runtime.py"):
        shutil.copy2(ROOT / "packaging" / name, root / "app" / name)


def release_manifest(marker, app_root, runtime_root):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    return {**marker, "version": version, "built": datetime.now(timezone.utc).isoformat(),
            "qml_runtime": validate_qml_runtime(runtime_root), "qml_assets": qml_asset_manifest(app_root)}


def refresh(destination):
    """Swap only the program and its manifest after the replacement is validated."""
    manifest_path = destination / "tingmu-portable.json"
    marker = json.loads(manifest_path.read_text("utf-8"))
    app = destination / "app"
    launcher_hash = hashlib.sha256((ROOT / "packaging/Launcher.cs").read_bytes()).hexdigest()
    if (app.resolve() != app or not (app / "bootstrap.py").is_file() or not (destination / "Tingmu.exe").is_file()
            or marker.get("launcher_source_sha256") != launcher_hash):
        raise ValueError("Expected an existing portable app with the current launcher")
    with tempfile.TemporaryDirectory(prefix=".tingmu-update-", dir=destination) as folder:
        stage = Path(folder)
        copy_app(stage)
        replacement = release_manifest(marker, stage, destination)
        staged_marker = stage / manifest_path.name
        staged_marker.write_text(json.dumps(replacement, ensure_ascii=False, indent=2), encoding="utf-8")
        # Keep the backup outside the temporary directory so a blocked rollback
        # on Windows cannot discard the last working program.
        previous = stage.with_name(stage.name + ".previous")
        if previous.resolve().parent != destination or previous.exists():
            raise ValueError("Unexpected application backup path")
        app.rename(previous)
        try:
            (stage / "app").rename(app)
            try:
                staged_marker.replace(manifest_path)
            except OSError:
                app.rename(stage / "app")
                raise
        except OSError:
            previous.rename(app)
            raise
        shutil.rmtree(previous)
    print(f"Updated portable application: {destination}")


def build(destination):
    if destination.exists():
        raise FileExistsError(f"Choose a new folder. Destination already exists: {destination}")
    marker = json.loads((BUNDLE / "tingmu-portable.json").read_text("utf-8"))
    launcher_hash = hashlib.sha256((ROOT / "packaging/Launcher.cs").read_bytes()).hexdigest()
    if marker["launcher_source_sha256"] != launcher_hash:
        raise ValueError("Refresh the portable launcher before building the clean package")
    with tempfile.TemporaryDirectory(prefix=".tingmu-build-", dir=destination.parent) as staging:
        root = Path(staging) / "Tingmu"
        root.mkdir()
        print("Copying independent Windows and ASR runtimes...", flush=True)
        shutil.copytree(BUNDLE / "runtime", root / "runtime", ignore=ignore_runtime)
        patch_aligner(root)
        (root / "bin").mkdir()
        for name in ("ffmpeg.exe", "ffprobe.exe", "FFmpeg-LICENSE.txt"):
            shutil.copy2(BUNDLE / "bin" / name, root / "bin" / name)
        copy_app(root)
        shutil.copy2(BUNDLE / "Tingmu.exe", root / "Tingmu.exe")
        marker = release_manifest({**marker, "clean_install": True}, root, root)
        (root / "tingmu-portable.json").write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
        root.rename(destination)
    print(f"Clean install ready: {destination}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="Portable folder on the project disk")
    parser.add_argument("--refresh", action="store_true", help="Update program files in an existing, closed installation")
    args = parser.parse_args()
    (refresh if args.refresh else build)(args.destination.resolve())
