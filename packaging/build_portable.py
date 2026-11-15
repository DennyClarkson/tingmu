"""Build a real, relocatable Windows folder distribution from verified local deps."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

from qml_runtime import qml_asset_manifest, validate_qml_runtime

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from tingmu import __version__  # noqa: E402
from tingmu.runtime import configure_local_caches  # noqa: E402

configure_local_caches()
OUT = ROOT / "dist" / "Tingmu"


def copy_tree(source, target, *, ignore=None):
    print(f"Copying {source.name} -> {target.relative_to(OUT)}", flush=True)
    shutil.copytree(source, target, dirs_exist_ok=True, symlinks=False, ignore=ignore)


def copy_runtime(environment, target):
    base = Path(subprocess.check_output([str(environment / "Scripts/python.exe"), "-c",
                                        "import sys; print(sys.base_prefix)"], text=True).strip())
    target.mkdir(parents=True, exist_ok=True)
    for source in base.iterdir():
        if source.is_file():
            shutil.copy2(source, target / source.name)
    ignore = shutil.ignore_patterns("__pycache__", "site-packages", "test", "tests", "idlelib", "tkinter")
    copy_tree(base / "Lib", target / "Lib", ignore=ignore)
    copy_tree(base / "DLLs", target / "DLLs", ignore=shutil.ignore_patterns("tcl*", "tk*", "_tkinter*"))
    packages = target / "Lib/site-packages"
    copy_tree(environment / "Lib/site-packages", packages,
              ignore=shutil.ignore_patterns("__pycache__", "_editable_impl_tingmu*", "_virtualenv*", "*.pyc"))
    # CPython's ._pth mode isolates this runtime from registry, PYTHONPATH and user packages.
    (target / "python312._pth").write_text(".\nDLLs\nLib\nLib/site-packages\n../../app\nimport site\n", encoding="utf-8")
    for path in packages.glob("*.pth"):
        if any(line.strip() and not line.startswith(("#", "import ")) and Path(line.strip()).is_absolute()
               for line in path.read_text(encoding="utf-8").splitlines()):
            raise RuntimeError(f"Nonportable package search path: {path}")


def copy_models(source, target):
    # HF Windows caches can contain duplicate blobs, snapshots and download chunks.
    # Actual resolved snapshot files + refs suffice for offline snapshot resolution.
    for model in source.glob("models--*"):
        for name in ("refs", "snapshots"):
            if (model / name).exists():
                copy_tree(model / name, target / model.name / name,
                          ignore=shutil.ignore_patterns("*.incomplete", "*.lock"))


def patch_nagisa(portable):
    """DyNet's narrow filename API cannot read a Unicode absolute Windows path.

    Keep the pinned model/tokenizer intact; only load its ASCII filename relative
    to a Unicode-safe Python chdir. Qwen is imported in its isolated ASR process.
    """
    path = portable / "runtime/asr/Lib/site-packages/nagisa/model.py"
    source = path.read_text(encoding="utf-8")
    marker = "# Tingmu portable: Unicode-safe DyNet model loading."
    if marker in source:
        return
    original = "            model.populate(params)"
    if source.count(original) != 1:
        raise RuntimeError("Unexpected Nagisa version; review Unicode model loading patch")
    replacement = "\n".join([
        "            " + marker,
        "            import os",
        "            previous_directory = os.getcwd()",
        "            model_path = os.path.abspath(params)",
        "            try:",
        "                os.chdir(os.path.dirname(model_path))",
        "                model.populate(os.path.basename(model_path))",
        "            finally:",
        "                os.chdir(previous_directory)",
    ])
    path.write_text(source.replace(original, replacement), encoding="utf-8")


def import_data():
    source = Path(os.environ["LOCALAPPDATA"]) / "Tingmu"
    target = OUT / "data"
    target.mkdir(parents=True, exist_ok=True)
    if (target / "import-manifest.json").exists():
        print("Existing imported user data retained; import already recorded.", flush=True)
        return
    for name in ("study.db", "settings.json", "documents"):
        if (target / name).exists():
            raise RuntimeError("Refusing to overwrite existing portable user data: " + name)
    files = []
    for item in source.iterdir():
        if item.name.startswith("study.db"):
            continue
        if item.is_dir():
            copy_tree(item, target / item.name)
        elif item.is_file():
            shutil.copy2(item, target / item.name)
        if item.is_file():
            files.append({"file": item.name, "sha256": hashlib.sha256(item.read_bytes()).hexdigest()})
    db_source = source / "study.db"
    if db_source.exists():
        with sqlite3.connect(db_source.as_uri() + "?mode=ro", uri=True) as src:
            with sqlite3.connect(target / "study.db") as dst:
                src.backup(dst)
                assert dst.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    (target / "import-manifest.json").write_text(json.dumps({
        "copied_from": str(source), "source_preserved": True, "database_method": "SQLite online backup",
        "files": files, "timestamp": datetime.now(timezone.utc).isoformat(),
    }, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-app", action="store_true")
    parser.add_argument("--import-user-data", action="store_true")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if not args.refresh_app:
        copy_runtime(ROOT / ".venv", OUT / "runtime/python")
        copy_runtime(ROOT / ".cache/qwen-venv", OUT / "runtime/asr")
        copy_models(ROOT / ".cache/qwen-models", OUT / ".cache/qwen-models")
        copy_models(ROOT / ".cache/models", OUT / ".cache/models")
        (OUT / "bin").mkdir(exist_ok=True)
        for executable in ("ffmpeg", "ffprobe"):
            source = shutil.which(executable)
            if not source:
                raise RuntimeError(f"Missing {executable}")
            shutil.copy2(source, OUT / "bin" / f"{executable}.exe")
        ffmpeg_folder = Path(shutil.which("ffmpeg")).parent.parent
        for filename in ("LICENSE", "LICENSE.txt"):
            if (ffmpeg_folder / filename).exists():
                shutil.copy2(ffmpeg_folder / filename, OUT / "bin" / "FFmpeg-LICENSE.txt")
    copy_tree(ROOT / "src/tingmu", OUT / "app/tingmu", ignore=shutil.ignore_patterns("__pycache__"))
    patch_nagisa(OUT)
    shutil.copy2(ROOT / "packaging/bootstrap.py", OUT / "app/bootstrap.py")
    shutil.copy2(ROOT / "packaging/bootstrap_logging.py", OUT / "app/bootstrap_logging.py")
    shutil.copy2(ROOT / "packaging/qml_runtime.py", OUT / "app/qml_runtime.py")
    shutil.copy2(ROOT / "packaging/portable_smoke.py", OUT / "app/portable_smoke.py")
    qml_runtime = validate_qml_runtime(OUT)
    qml_assets = qml_asset_manifest(OUT)
    # A distinct process filename keeps Windows from grouping this app with IDLE
    # and makes native accessibility/window ownership unambiguous.
    runtime_source = OUT / "runtime/python/pythonw.exe"
    runtime_target = OUT / "runtime/python/TingmuRuntime.exe"
    if not runtime_target.exists() or runtime_target.read_bytes() != runtime_source.read_bytes():
        shutil.copy2(runtime_source, runtime_target)
    launcher_hash = hashlib.sha256((ROOT / "packaging/Launcher.cs").read_bytes()).hexdigest()
    prior_manifest = OUT / "tingmu-portable.json"
    prior = json.loads(prior_manifest.read_text("utf-8")) if prior_manifest.exists() else {}
    if not args.refresh_app or not (OUT / "Tingmu.exe").is_file() or prior.get("launcher_source_sha256") != launcher_hash:
        compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
                        "/r:System.Windows.Forms.dll", f"/out:{OUT / 'Tingmu.exe'}",
                        str(ROOT / "packaging/Launcher.cs")], check=True)
    if args.import_user_data:
        import_data()
    manifest = {"format": 2, "name": "听幕", "version": __version__, "python": sys.version.split()[0],
                "launcher_source_sha256": launcher_hash,
                "built": datetime.now(timezone.utc).isoformat(), "entry": "Tingmu.exe",
                "data": "data", "cache": ".cache", "runtime": "runtime/python", "asr": "runtime/asr",
                "frontend": "Qt Quick/QML", "qml_runtime": qml_runtime, "qml_assets": qml_assets}
    (OUT / "tingmu-portable.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    shutil.copy2(ROOT / "docs/portable.md", OUT / "使用说明.md")
    print(f"Portable folder ready: {OUT}", flush=True)


if __name__ == "__main__":
    main()
