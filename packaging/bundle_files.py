"""Shared build-time assembly steps for the base and first-run distributions."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from pe_bundle import copy_pyside_runtime
from runtime_pruning import check_wheel_licenses, site_packages_ignore


def copy_packages(source, destination, runtime, clean_ignore=None):
    select = site_packages_ignore(source, runtime)

    def ignore(directory, names):
        omitted = select(directory, names)
        if clean_ignore:
            omitted.update(clean_ignore(directory, names))
        if runtime == "python" and Path(directory) == source:
            omitted.add("PySide6")
        return omitted

    shutil.copytree(source, destination, ignore=ignore)
    if runtime == "python":
        copy_pyside_runtime(source / "PySide6", destination / "PySide6")
        build_packages = Path(__file__).resolve().parents[1] / ".venv/Lib/site-packages"
        check_wheel_licenses(destination, copy_from=(source, build_packages))


def launcher_icon(source):
    return source.parents[1] / "src/tingmu/quick/brand/app-icon.ico"


def launcher_hash(source):
    """Fingerprint both compiler inputs so an old or differently branded EXE is rebuilt."""
    inputs = (source, launcher_icon(source))
    return hashlib.sha256(b"".join(hashlib.sha256(path.read_bytes()).digest() for path in inputs)).hexdigest()


def compile_launcher(source, target):
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
                    f"/win32icon:{launcher_icon(source)}", f"/out:{target}", str(source)], check=True)


def precompile(app, interpreter, *, include_runtime):
    command = [str(interpreter), "-B", str(Path(__file__).with_name("compile_startup.py")), str(app)]
    if include_runtime:
        command.append("--runtime")
    return json.loads(subprocess.check_output(command, text=True))


def unlinked(path):
    if path.resolve() != Path(os.path.abspath(path)) or path.is_symlink() or path.is_junction():
        raise ValueError(f"Unexpected linked build destination: {path}")
    return path


def publish(stage, destination, names):
    """Roll back the whole program on a failed rename; keep blocked recovery copies."""
    for name in names:
        unlinked(destination / name)
        if not (stage / name).exists():
            raise FileNotFoundError(stage / name)
    # A rollback blocked by a Windows file lock must survive stage cleanup.
    backup = Path(tempfile.mkdtemp(prefix=".tingmu-previous-", dir=destination))
    saved = []
    installed = []
    try:
        for name in names:
            target = destination / name
            if target.exists():
                (backup / name).parent.mkdir(parents=True, exist_ok=True)
                target.rename(backup / name)
                saved.append(name)
            (stage / name).rename(target)
            installed.append(name)
    except BaseException:
        failures = []
        for name in reversed(installed):
            try:
                (destination / name).rename(stage / name)
            except OSError as exc:
                failures.append(exc)
        for name in reversed(saved):
            try:
                (backup / name).rename(destination / name)
            except OSError as exc:
                failures.append(exc)
        if failures:
            raise RuntimeError(f"Rollback blocked. Previous program files are in {backup}") from failures[0]
        shutil.rmtree(backup)
        raise
    shutil.rmtree(backup)
