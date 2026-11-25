"""Compile relocatable startup bytecode with the interpreter that will read it."""
import importlib
import json
from pathlib import Path
import py_compile
import sys


def compile_startup(app, *, include_runtime):
    runtime = Path(sys.executable).resolve().parent
    paths = {path: "app/" + path.relative_to(app).as_posix() for path in app.rglob("*.py")
             if not path.is_relative_to(app / "tingmu/ui")}
    if include_runtime:
        # These imports exercise the standard-library portions of desktop startup
        # without constructing a workspace or executing application/SDK startup hooks.
        for name in ("argparse", "concurrent.futures.thread", "ctypes.wintypes", "dataclasses",
                     "datetime", "filelock", "hashlib", "logging.handlers", "platform", "random",
                     "shutil", "sqlite3", "subprocess", "tempfile", "typing", "urllib.parse", "uuid"):
            importlib.import_module(name)
        for module in tuple(sys.modules.values()):
            source = getattr(module, "__file__", None)
            if source:
                path = Path(source).resolve()
                if path.is_relative_to(runtime / "Lib") and path.suffix == ".py" and path.is_file():
                    paths[path] = "runtime/python/" + path.relative_to(runtime).as_posix()
    total = 0
    for path, portable_filename in sorted(paths.items()):
        # ZIP extraction can round or reset mtimes. Verify the source bytes on import.
        result = py_compile.compile(str(path), dfile=portable_filename, doraise=True,
                                    invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
        total += Path(result).stat().st_size
    return {"files": len(paths), "bytes": total}


if __name__ == "__main__":
    print(json.dumps(compile_startup(Path(sys.argv[1]).resolve(), include_runtime="--runtime" in sys.argv[2:])))
