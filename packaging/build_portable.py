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
import tempfile

from filelock import FileLock

from build_clean_install import (
    RELEASE_DOCUMENTS, copy_app, copy_release_documents, ignore_runtime, release_manifest, validate_core_release,
)
from bundle_files import compile_launcher, copy_packages, launcher_hash, precompile, publish, unlinked

ROOT = Path(__file__).resolve().parent.parent
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
    copy_packages(environment / "Lib/site-packages", packages, target.name,
                  clean_ignore=ignore_runtime)
    # CPython's ._pth mode isolates this runtime from registry, PYTHONPATH and user packages.
    (target / "python312._pth").write_text(".\nDLLs\nLib\nLib/site-packages\n../../app\nimport site\n", encoding="utf-8")
    for path in packages.glob("*.pth"):
        if any(line.strip() and not line.startswith(("#", "import ")) and Path(line.strip()).is_absolute()
               for line in path.read_text(encoding="utf-8").splitlines()):
            raise RuntimeError(f"Nonportable package search path: {path}")


def copy_missing_tree(source, target):
    """Add complete cache files while preserving every existing destination file."""
    unlinked(target).mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        if item.name.endswith((".incomplete", ".lock")):
            continue
        destination = unlinked(target / item.name)
        if item.is_dir():
            copy_missing_tree(item, destination)
        elif not destination.exists():
            # Publish only after the complete weight has been copied. A download
            # completing concurrently wins the final name without being replaced.
            with tempfile.TemporaryDirectory(prefix=".tingmu-copy-", dir=target) as staging:
                temporary = Path(staging) / item.name
                shutil.copy2(item, temporary)
                try:
                    if os.name == "nt":
                        temporary.rename(destination)  # Windows refuses an existing target.
                    else:
                        os.link(temporary, destination)
                except FileExistsError:
                    pass


def import_data():
    source = Path(os.environ["LOCALAPPDATA"]) / "Tingmu"
    target = unlinked(OUT / "data")
    target.mkdir(parents=True, exist_ok=True)
    if unlinked(target / "import-manifest.json").exists():
        print("Existing imported user data retained; import already recorded.", flush=True)
        return
    for name in ("study.db", "settings.json", "documents"):
        if unlinked(target / name).exists():
            raise RuntimeError("Refusing to overwrite existing portable user data: " + name)
    files = []
    for item in source.iterdir():
        if item.name.startswith("study.db"):
            continue
        destination = unlinked(target / item.name)
        if item.is_dir():
            copy_missing_tree(item, destination)
        elif item.is_file() and not destination.exists():
            shutil.copy2(item, destination)
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


def build(refresh_app):
    marker_name = "tingmu-portable.json"
    marker_path = unlinked(OUT / marker_name)
    previous = json.loads(marker_path.read_text("utf-8")) if marker_path.is_file() else {}
    with tempfile.TemporaryDirectory(prefix=".tingmu-build-", dir=OUT) as folder:
        stage = Path(folder)
        names = []
        runtime_root = OUT if refresh_app else stage
        python = runtime_root / "runtime/python"
        named_exe = python / "TingmuRuntime.exe"
        if refresh_app:
            if not previous or not unlinked(named_exe).is_file():
                raise ValueError("Refresh requires an existing portable runtime and manifest")
        else:
            copy_runtime(ROOT / ".venv", python)
            shutil.copy2(python / "pythonw.exe", named_exe)
            # runtime/asr is owned by the on-demand installer.
            unlinked(OUT / "runtime").mkdir(exist_ok=True)
            names.append("runtime/python")
        copy_app(stage)
        copy_release_documents(ROOT, stage)
        source = ROOT / "packaging/Launcher.cs"
        digest = launcher_hash(source)
        launcher = unlinked(OUT / "Tingmu.exe")
        if previous.get("launcher_source_sha256") == digest and launcher.is_file():
            shutil.copy2(launcher, stage / launcher.name)
        else:
            compile_launcher(source, stage / launcher.name)
        bytecode = precompile(stage / "app", unlinked(python / "python.exe"), include_runtime=not refresh_app)
        marker = {**(previous if refresh_app else {"clean_install": True}), "format": 2, "name": "听幕",
                  "python": sys.version.split()[0], "launcher_source_sha256": digest,
                  "startup_bytecode": bytecode, "entry": "Tingmu.exe", "frontend": "Qt Quick/QML",
                  "data": "data", "cache": ".cache", "runtime": "runtime/python"}
        if not refresh_app:
            marker["weight_audit"] = validate_core_release(stage)
        manifest = release_manifest(marker, stage, runtime_root)
        (stage / marker_name).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        publish(stage, OUT, (*names, "app", launcher.name, *RELEASE_DOCUMENTS, marker_name))


def main():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, default=OUT)
    parser.add_argument("--refresh-app", action="store_true")
    parser.add_argument("--import-user-data", action="store_true")
    args = parser.parse_args()
    if args.import_user_data and not args.refresh_app:
        parser.error("--import-user-data is available only when refreshing an existing installation")
    sys.path.insert(0, str(ROOT / "src"))
    from tingmu.runtime import configure_local_caches
    configure_local_caches()
    OUT = unlinked(Path(os.path.abspath(args.destination)))
    OUT.mkdir(parents=True, exist_ok=True)
    with FileLock(unlinked(OUT / ".tingmu-build.lock"), timeout=0):
        build(args.refresh_app)
        if args.import_user_data:
            import_data()
    print(f"Portable folder ready: {OUT}", flush=True)


if __name__ == "__main__":
    main()
