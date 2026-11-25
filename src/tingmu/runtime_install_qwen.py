"""Prepare the relocatable Qwen interpreter and its pinned Japanese tokenizer."""

import os
from pathlib import Path
import shutil

from .runtime import confined_path


def prepare(root, stage):
    base = confined_path(root, "runtime/python")
    if not (base / "python.exe").is_file():
        raise FileNotFoundError("安装 Qwen3-ASR 需要便携版自带的 Python 3.12")
    for name in ("python.exe", "python312.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll"):
        shutil.copy2(confined_path(root, base / name), confined_path(stage, name))
    packages = confined_path(stage, "Lib/site-packages")
    packages.mkdir(parents=True)
    configure(root, stage, stage)
    return packages


def configure(root, stage, location):
    # ._pth isolates this interpreter from PYTHONPATH, the registry and user sites.
    # At runtime both the shared stdlib and this interpreter move with the launcher.
    base = root / "runtime/python"
    paths = [".", os.path.relpath(base / "DLLs", location), os.path.relpath(base / "Lib", location),
             "Lib/site-packages", os.path.relpath(root / "app", location), "import site"]
    confined_path(stage, "python312._pth").write_text("\n".join(paths) + "\n", encoding="utf-8")


def patch_nagisa(packages):
    path = confined_path(packages, "nagisa/model.py")
    source = path.read_text(encoding="utf-8")
    marker = "# Tingmu portable: Unicode-safe DyNet model loading."
    if marker in source:
        return
    original = "            model.populate(params)"
    if source.count(original) != 1:
        raise ValueError("Review the pinned Nagisa model loader")
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


def patch_aligner(packages):
    aligner = confined_path(packages, "qwen_asr/inference/qwen3_forced_aligner.py")
    code = aligner.read_text("utf-8")
    needle = "        words = nagisa.tagging(text).words"
    lazy = "        import nagisa\n" + needle
    if code.count("\nimport nagisa\n") == 1 and code.count(needle) == 1:
        aligner.write_text(code.replace("\nimport nagisa\n", "\n").replace(needle, lazy), encoding="utf-8")
    elif "\nimport nagisa\n" in code or code.count(lazy) != 1:
        raise ValueError("Review the pinned Qwen tokenizer import")

    tagger = confined_path(packages, "nagisa/tagger.py")
    code = tagger.read_text("utf-8")
    needle = "base + '/data/nagisa_v001."
    cache = ("from pathlib import Path\nasset_dir = str(Path(__file__).resolve().parents[5] / "
             "'.cache/qwen-support/nagisa-0.2.11')")
    portable = "asset_dir + '/nagisa_v001."
    if code.count(needle) == 3 and code.count("sys.path.append(base)") == 1:
        code = code.replace("sys.path.append(base)", "sys.path.append(base)\n" + cache)
        tagger.write_text(code.replace(needle, portable), encoding="utf-8")
    elif needle in code or cache not in code or code.count(portable) != 3:
        raise ValueError("Review the pinned Nagisa asset locations")


def finish(packages):
    patch_nagisa(packages)
    patch_aligner(packages)
    # The workspace owns these tokenizer assets, obtained through qwen_support.
    for name in ("nagisa_v001.dict", "nagisa_v001.hp", "nagisa_v001.model"):
        confined_path(packages, Path("nagisa/data") / name).unlink(missing_ok=True)
    # uv may install absolute console launchers. Qwen is invoked as a library.
    for name in ("bin", "Scripts"):
        directory = confined_path(packages, name)
        if directory.is_dir():
            shutil.rmtree(directory)
