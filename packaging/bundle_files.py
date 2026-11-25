"""Shared build-time assembly steps for the base and first-run distributions."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from pe_bundle import copy_pyside_runtime
from runtime_pruning import site_packages_ignore


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


def launcher_hash(source):
    return hashlib.sha256(source.read_bytes()).hexdigest()


def compile_launcher(source, target):
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    subprocess.run([str(compiler), "/nologo", "/target:winexe", "/platform:x64", "/optimize+",
                    f"/out:{target}", str(source)], check=True)


def precompile(app, interpreter, *, include_runtime):
    command = [str(interpreter), "-B", str(Path(__file__).with_name("compile_startup.py")), str(app)]
    if include_runtime:
        command.append("--runtime")
    return json.loads(subprocess.check_output(command, text=True))
