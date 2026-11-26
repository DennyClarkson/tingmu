"""Assemble a first-run Windows portable folder from the existing bundled runtimes."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import tomllib

from bundle_files import compile_launcher, copy_packages, launcher_hash, precompile
from qml_runtime import qml_asset_manifest, validate_qml_runtime

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "dist/Tingmu"
RELEASE_DOCUMENTS = {"使用说明.md": "docs/portable.md", "THIRD_PARTY_NOTICES.md": "THIRD_PARTY_NOTICES.md"}
PACKAGE_ASSETS = {
    "tagger.pickle", "nagisa_v001.dict", "nagisa_v001.hp", "nagisa_v001.model",
    "trained_models", "test_data", "tests", "test", "__pycache__", "direct_url.json",
}
WEIGHT_EXTENSIONS = {".onnx", ".safetensors", ".ckpt", ".gguf", ".pt", ".tflite", ".weights"}
EXCLUDED_EXTENSIONS = WEIGHT_EXTENSIONS | {".whl",
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


def validate_model_free_runtime(root, folders=("app", "runtime", "bin")):
    """Check the actual artifact after all copy/compile steps, including wheel data."""
    inspect_suffixes = WEIGHT_EXTENSIONS | {".bin", ".model", ".npz", ".pkl", ".pickle", ".joblib", ".pb", ".h5"}
    # These are numerical integration constants used by scipy.stats.qmc.
    sobol = "runtime/asr/Lib/site-packages/scipy/stats/_sobol_direction_numbers.npz"
    count = 0
    for folder in folders:
        for directory, _, names in os.walk(root / folder):
            for name in names:
                count += 1
                path = Path(directory) / name
                ext = path.suffix.lower()
                if ext in inspect_suffixes or name in PACKAGE_ASSETS:
                    if path.relative_to(root).as_posix() != sobol:
                        raise ValueError(f"Unexpected model payload in model-free distribution: {path}")
                if ext == ".pth":  # CPython path config is text; PyTorch checkpoints are binary.
                    if path.stat().st_size > 65536:
                        raise ValueError(f"Unexpected binary .pth in model-free distribution: {path}")
                    config = path.read_bytes()
                    if b"\x00" in config:
                        raise ValueError(f"Unexpected binary .pth in model-free distribution: {path}")
                    config.decode("utf-8")
    return {"files_checked": count, "model_payloads": 0}


def validate_core_release(root):
    """Reject even empty heavyweight package directories left behind by stale wheels."""
    for name in RELEASE_DOCUMENTS:
        document = root / name
        if not document.is_file() or not document.stat().st_size:
            raise ValueError(f"Missing or empty release document: {document}")
    packages = root / "runtime/python/Lib/site-packages"
    blocked = {"av", "ctranslate2", "faster_whisper", "hf_xet", "nvidia", "onnxruntime",
               "tokenizers", "torch", "qwen_asr"}
    for path in (root / "bin", root / "runtime/asr", root / ".cache", root / "data"):
        if path.exists():
            raise ValueError(f"Unexpected external runtime or user data in release: {path}")
    for path in packages.iterdir():
        name = path.name.lower().split("-")[0].split(".")[0]
        if name in blocked or name.startswith("nvidia_"):
            raise ValueError(f"Unexpected inference package in release: {path}")
    return validate_model_free_runtime(root)


def release_document_bytes(repository, name):
    source = repository / RELEASE_DOCUMENTS[name]
    if name != "使用说明.md":
        return source.read_bytes()
    # Keep the established portable filename. Both language links work offline.
    chinese = source.read_text("utf-8").replace("[English](portable.en.md)", "[English](#english)")
    english = (source.parent / "portable.en.md").read_text("utf-8").replace(
        "[简体中文](portable.md)", "[简体中文](#chinese)"
    )
    return (f'<a id="chinese"></a>\n\n{chinese.rstrip()}\n\n---\n\n'
            f'<a id="english"></a>\n\n{english.rstrip()}\n').encode("utf-8")


def copy_release_documents(repository, root):
    for name in RELEASE_DOCUMENTS:
        (root / name).write_bytes(release_document_bytes(repository, name))


def copy_app(root):
    source = ROOT / "src/tingmu"

    def ignore(directory, names):
        omitted = shutil.ignore_patterns("__pycache__", "*.pyc")(directory, names)
        if Path(directory) == source:
            omitted.add("ui")  # This distribution launches only the Qt Quick frontend.
        return omitted

    shutil.copytree(source, root / "app/tingmu", ignore=ignore)
    for name in ("bootstrap.py", "bootstrap_logging.py", "portable_smoke.py", "qml_runtime.py"):
        shutil.copy2(ROOT / "packaging" / name, root / "app" / name)


def release_manifest(marker, app_root, runtime_root):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    result = {**marker, "version": version, "built": datetime.now(timezone.utc).isoformat(),
              "qml_runtime": validate_qml_runtime(runtime_root), "qml_assets": qml_asset_manifest(app_root)}
    if app_root != runtime_root:  # App-only refresh keeps the installed runtime.
        if result.get("startup_bytecode"):
            caches = [p for folder in (app_root / "app", runtime_root / "runtime/python/Lib")
                      for p in folder.rglob("*.pyc")]
            result["startup_bytecode"] = {"files": len(caches), "bytes": sum(p.stat().st_size for p in caches)}
        # Validate just the new payload. User-installed engines stay outside the release audit.
        validate_model_free_runtime(app_root, ("app",))
    return result


def refresh(destination):
    """Swap only the program and its manifest after the replacement is validated."""
    manifest_path = destination / "tingmu-portable.json"
    marker = json.loads(manifest_path.read_text("utf-8"))
    app = destination / "app"
    digest = launcher_hash(ROOT / "packaging/Launcher.cs")
    if (app.resolve() != app or not (app / "bootstrap.py").is_file() or not (destination / "Tingmu.exe").is_file()
            or marker.get("launcher_source_sha256") != digest):
        raise ValueError("Expected an existing portable app with the current launcher")
    with tempfile.TemporaryDirectory(prefix=".tingmu-update-", dir=destination) as folder:
        stage = Path(folder)
        copy_app(stage)
        if marker.get("startup_bytecode"):
            precompile(stage / "app", destination / "runtime/python/python.exe", include_runtime=False)
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


def build(destination, source=None, *, bytecode=True):
    source = BUNDLE if source is None else source
    if destination.exists():
        raise FileExistsError(f"Choose a new folder. Destination already exists: {destination}")
    marker = json.loads((source / "tingmu-portable.json").read_text("utf-8"))
    launcher_source = ROOT / "packaging/Launcher.cs"
    digest = launcher_hash(launcher_source)
    with tempfile.TemporaryDirectory(prefix=".tingmu-build-", dir=destination.parent) as staging:
        root = Path(staging) / "Tingmu"
        root.mkdir()
        print("Copying core Windows runtime...", flush=True)
        src = source / "runtime/python"
        dst = root / "runtime/python"

        def outside_packages(directory, names):
            if Path(directory) == src / "Lib":
                return {"site-packages", *ignore_runtime(directory, names)}
            return ignore_runtime(directory, names)

        shutil.copytree(src, dst, ignore=outside_packages)
        copy_packages(src / "Lib/site-packages", dst / "Lib/site-packages", "python", ignore_runtime)
        copy_app(root)
        copy_release_documents(ROOT, root)
        if marker.get("launcher_source_sha256") == digest:
            shutil.copy2(source / "Tingmu.exe", root / "Tingmu.exe")
        else:
            compile_launcher(launcher_source, root / "Tingmu.exe")
        compiled = precompile(root / "app", root / "runtime/python/python.exe",
                              include_runtime=True) if bytecode else {}
        marker.pop("asr", None)
        marker = release_manifest({**marker, "clean_install": True, "launcher_source_sha256": digest,
                                   "startup_bytecode": compiled}, root, root)
        marker["weight_audit"] = validate_core_release(root)
        (root / "tingmu-portable.json").write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
        root.rename(destination)
    print(f"Clean install ready: {destination}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="Portable folder on the project disk")
    parser.add_argument("--source", type=Path, help="Base portable runtime folder (default: dist/Tingmu)")
    parser.add_argument("--no-bytecode", action="store_true", help="Build a source-only comparison copy")
    parser.add_argument("--refresh", action="store_true", help="Update program files in an existing, closed installation")
    args = parser.parse_args()
    if args.refresh:
        refresh(args.destination.resolve())
    else:
        build(args.destination.resolve(), args.source.resolve() if args.source else None,
              bytecode=not args.no_bytecode)
