"""Select installed wheel contents for the two independent Windows runtimes."""
import base64
import csv
import hashlib
from importlib.metadata import distributions
import io
from pathlib import Path
import shutil
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


PROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"
# qwen-asr 0.0.6 imports these web frontends in cli/demo*.py only. Keep
# uncertain scientific/audio dependencies and every CUDA DLL.
ASR_DEMO_AND_BUILD = {
    "gradio", "gradio-client", "hf-gradio", "fastapi", "uvicorn", "starlette",
    "flask", "werkzeug", "cython", "pygments",
}


# huggingface_hub supports standard HTTP downloads without the optional Xet backend.
ON_DEMAND = {"faster-whisper", "ctranslate2", "onnxruntime", "av", "tokenizers", "hf-xet"}


def _main_closure(installed):
    project = tomllib.loads(PROJECT.read_text(encoding="utf-8"))["project"]
    required = [Requirement(s) for s in project["dependencies"]]
    pending = [(r, "") for r in required]
    visited = set()
    while pending:
        requirement, parent_extra = pending.pop()
        if requirement.marker and not requirement.marker.evaluate({"extra": parent_extra}):
            continue
        name = canonicalize_name(requirement.name)
        if name in ON_DEMAND or name.startswith("nvidia-"):
            continue
        if name not in installed:
            raise ValueError(f"Missing runtime distribution: {name}")
        for extra in {""} | requirement.extras:
            if (name, extra) in visited:
                continue
            visited.add((name, extra))
            pending.extend((Requirement(spec), extra) for spec in installed[name].requires or ())
    return {name for name, extra in visited}


def _owned_paths(distribution):
    record = distribution.read_text("RECORD")
    if record is None:
        raise ValueError(f"Missing wheel RECORD for {distribution.metadata['Name']}")
    for row in csv.reader(io.StringIO(record)):
        path = row[0].replace("\\", "/")
        # Wheels can also own executables outside site-packages.
        if not path.startswith("/") and ".." not in path.split("/"):
            yield path


def check_wheel_licenses(packages, *, copy_from=()):
    """Keep upstream notices even when a header, doc or test directory was pruned."""
    for info in packages.glob("*.dist-info"):
        with (info / "RECORD").open(encoding="utf-8", newline="") as record:
            for name, digest, _ in csv.reader(record):
                path = Path(name)
                if path.anchor or ".." in path.parts:
                    continue
                if not (path.parts[:2] == (info.name, "licenses") or
                        path.name.lower().split(".")[0] in {"authors", "copying", "copyright", "license", "licence", "notice"}):
                    continue
                target = packages / path
                if not target.is_file():
                    # An older portable source can itself be missing a pruned notice.
                    # Only a byte-for-byte RECORD match may be recovered from another installation.
                    source = next((root / path for root in copy_from if (root / path).is_file()), None)
                    if source is None or not digest:
                        raise ValueError(f"Missing wheel license: {target}")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                if digest:
                    algorithm, expected = digest.split("=", 1)
                    actual = hashlib.new(algorithm, target.read_bytes()).digest()
                    if base64.urlsafe_b64encode(actual).rstrip(b"=").decode("ascii") != expected:
                        raise ValueError(f"Wheel license differs from its RECORD: {target}")


def _ancestors(path):
    while path:
        yield path
        path = path.rpartition("/")[0]


def _distribution_exclusions(installed, remove):
    omitted = {ancestor for name in remove for path in _owned_paths(installed[name])
               for ancestor in _ancestors(path)}
    roots = {path for path in omitted if "/" not in path}
    # Shared namespaces (e.g. nvidia) and even co-owned files have to survive
    # when another installed runtime wheel still ships them.
    for name in installed.keys() - remove:
        for path in _owned_paths(installed[name]):
            if path.partition("/")[0] in roots:
                omitted.difference_update(_ancestors(path))
    return omitted


def site_packages_ignore(packages: Path, runtime: str):
    """Build a shutil.copytree ignore callback once, against the source tree."""
    packages = Path(packages)
    installed = {canonicalize_name(d.metadata["Name"]): d for d in distributions(path=[str(packages)])}
    if runtime == "python":
        remove = installed.keys() - _main_closure(installed)
    elif runtime == "asr":
        qwen = installed.get("qwen-asr")
        remove = ASR_DEMO_AND_BUILD & installed.keys() if qwen and qwen.version == "0.0.6" else set()
    else:
        raise ValueError(f"Unknown runtime: {runtime}")
    omitted = _distribution_exclusions(installed, remove)

    def ignore(directory, names):
        relative = Path(directory).relative_to(packages)
        prefix = "" if not relative.parts else relative.as_posix() + "/"
        ignored = set()
        for name in names:
            path = prefix + name
            lower = name.lower()
            # librosa uses lazy_loader.attach_stub to read these at import time.
            runtime_stub = path.startswith("librosa/") and name == "__init__.pyi"
            if (path in omitted or lower in {"__pycache__", "include", "test", "tests", "doc", "docs"}
                    or lower.endswith((".pdb", ".lib", ".pyc"))
                    or (lower.endswith(".pyi") and not runtime_stub)
                    or (not prefix and lower.startswith(("_virtualenv.", "_editable_impl_tingmu")))):
                ignored.add(name)
        return ignored

    return ignore
