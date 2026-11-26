"""Build, relocate, test and archive the Windows x64 portable release from .venv."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib
import zipfile

from filelock import FileLock

from build_clean_install import RELEASE_DOCUMENTS, validate_core_release
from bundle_files import publish, unlinked

ROOT = Path(__file__).resolve().parents[1]
MAX_BYTES = 350 * 1024**2
REQUIRED_CHECKS = {
    "qml_window", "application_icon", "isolated_data", "portable_imports", "bundled_open_fonts", "first_run_guide",
    "first_run_skip_persisted", "workspace_llm_concurrency", "notebook_markdown_preview", "no_new_bytecode",
}
MEDIA_CHECKS = {"decode", "seek", "rate", "audio_switch", "pause"}
SMOKE_LOGS = ("portable-smoke.json", "application.log", "application.log.1", "application.log.2",
              "events.jsonl", "events.jsonl.1", "events.jsonl.2", "events.jsonl.3")
FIRST_RUN = "首次启动检测可复用的本机依赖。可选 0 B 直接进入，需要时再安装对应运行时和模型。"


def project_version(expected):
    version = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+-]*", version):
        raise ValueError(f"Unsafe version for release filename: {version!r}")
    if expected is not None and expected != version:
        raise ValueError(f"Expected version {expected!r}, pyproject.toml has {version!r}")
    return version


def build_portable(destination, log):
    environment = ROOT / ".venv"
    python = environment / "Scripts/python.exe"
    if not python.is_file():
        raise FileNotFoundError(f"Missing {python}; run uv sync --locked --extra dev")
    probe = subprocess.check_output([
        python, "-I", "-c", "import json,sys,sysconfig; print(json.dumps([sys.implementation.name,"
        "sys.version_info[:2],sysconfig.get_platform(),sys.prefix]))",
    ], text=True)
    implementation, version, platform, prefix = json.loads(probe)
    if (implementation, version, platform, Path(prefix).resolve()) != (
        "cpython", [3, 12], "win-amd64", environment.resolve()
    ):
        raise ValueError("Release builds require the project's Windows x64 CPython 3.12 .venv")
    subprocess.run([python, "-E", "-s", "-X", "utf8", ROOT / "packaging/build_portable.py",
                    "--destination", destination], env={**os.environ, "PYTHONUTF8": "1"},
                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, check=True)
    # This lock belongs exclusively to this newly built tree. The builder has exited.
    (destination / ".tingmu-build.lock").unlink(missing_ok=True)


def audit_bundle(root, version):
    expected = {"Tingmu.exe", "app", "runtime", "tingmu-portable.json", *RELEASE_DOCUMENTS}
    if {p.name for p in root.iterdir()} != expected:
        raise ValueError("Unexpected release root contents")
    if {p.name for p in (root / "runtime").iterdir()} != {"python"}:
        raise ValueError("Unexpected runtime contents")
    if not (root / "Tingmu.exe").is_file() or not (root / "app/bootstrap.py").is_file():
        raise ValueError("Missing portable launcher or bootstrap")
    manifest = json.loads((root / "tingmu-portable.json").read_text("utf-8"))
    if manifest.get("clean_install") is not True or manifest.get("version") != version:
        raise ValueError("Invalid version or clean_install in portable manifest")
    models = manifest.get("weight_audit", {}).get("model_payloads")
    pe_count = manifest.get("qml_runtime", {}).get("pe_files_checked")
    if type(models) is not int or models != 0 or type(pe_count) is not int or pe_count <= 0:
        raise ValueError("Missing or failing PE/model audit in portable manifest")
    validate_core_release(root, ROOT)
    files = []
    size = 0
    for path in root.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Unexpected link in release: {path}")
        if (path.name in {".cache", ".git", ".pytest_cache", ".ruff_cache", ".env", "portable-smoke.json"}
                or path.suffix.lower() in {".log", ".db", ".sqlite", ".sqlite3", ".tmp", ".mkv"}):
            raise ValueError(f"Unexpected private or temporary release content: {path}")
        if path.is_file():
            files.append(path.relative_to(root).as_posix())
            size += path.stat().st_size
    if size > MAX_BYTES:
        raise ValueError(f"Unpacked release is {size} bytes, above the {MAX_BYTES} byte budget")
    return sorted(files), size, pe_count


def check_smoke(report, version, with_media):
    required = REQUIRED_CHECKS | (MEDIA_CHECKS if with_media else set())
    if report.get("tray_available") is True:
        required = required | {"system_tray"}
    checks = report.get("checks", {})
    failed = sorted(key for key in required | checks.keys() if checks.get(key) is not True)
    if (report.get("ready") is not True or report.get("version") != version or failed
            or any(report.get(key) != [] for key in ("qml_warnings", "errors", "new_bytecode", "blocked_network_events"))):
        raise ValueError(f"Portable smoke failed (checks: {failed}); see diagnostics/portable-smoke.json")


def run_smoke(clone, media, diagnostics):
    # Keep the host's Python, FFmpeg, Qwen, credentials and Qt plugins out of this launch.
    windows = Path(os.environ["SystemRoot"])
    temp = clone.parent / "tmp"
    temp.mkdir()
    env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "COMSPEC", "PATHEXT") if k in os.environ}
    env.update(PATH=os.pathsep.join(map(str, [windows / "System32", windows])),
               TEMP=str(temp), TMP=str(temp), USERPROFILE=str(temp), LOCALAPPDATA=str(temp),
               APPDATA=str(temp), QT_QPA_PLATFORM="offscreen", QT_QUICK_BACKEND="software",
               QT_OPENGL="software", QT_MEDIA_BACKEND="ffmpeg", QT_FFMPEG_DECODING_HW_DEVICE_TYPES=",",
               HF_HUB_OFFLINE="1", HF_DATASETS_OFFLINE="1")
    command = [clone / "Tingmu.exe", "--portable-smoke"]
    if media:
        command.extend(["--smoke-media", media])
    try:
        with (diagnostics / "launcher.log").open("w", encoding="utf-8") as log:
            with subprocess.Popen(command, cwd=clone, env=env, stdout=log, stderr=subprocess.STDOUT) as process:
                try:
                    code = process.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    # The windowed launcher waits on TingmuRuntime.exe. Stop its entire tree.
                    subprocess.run([windows / "System32/taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                                   stdout=log, stderr=subprocess.STDOUT)
                    process.kill()
                    raise
                if code:
                    raise RuntimeError(f"Tingmu.exe exited with code {code}; see {diagnostics}")
    finally:
        for name in SMOKE_LOGS:
            log_path = clone / "data/logs" / name
            if log_path.is_file():
                shutil.copyfile(log_path, diagnostics / name)
    return json.loads((diagnostics / "portable-smoke.json").read_text("utf-8"))


def write_archive(root, files, destination):
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.mkdir("Tingmu/", mode=0o755)
        for relative in files:
            # ZipInfo uses the DOS epoch and zipfile sets the UTF-8 flag for non-ASCII names.
            info = zipfile.ZipInfo("Tingmu/" + relative)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            with (root / relative).open("rb") as source, archive.open(info, "w") as member:
                shutil.copyfileobj(source, member, length=1024 * 1024)


def build_release(output, expected=None, media=None):
    version = project_version(expected)
    if media is not None:
        media = media.resolve(strict=True)
        if not media.is_file():
            raise ValueError("--smoke-media must point to a file")
    output = unlinked(Path(os.path.abspath(output)))
    output.mkdir(parents=True, exist_ok=True)
    cache = ROOT / ".cache"
    cache.mkdir(exist_ok=True)
    diagnostics = unlinked(output / "diagnostics")
    diagnostics.mkdir(exist_ok=True)
    with FileLock(unlinked(diagnostics / ".build.lock"), timeout=0), tempfile.TemporaryDirectory(
        prefix="release-", dir=cache
    ) as staging:
        stage = Path(staging)
        for name in (*SMOKE_LOGS, "build.log", "launcher.log"):
            unlinked(diagnostics / name).unlink(missing_ok=True)
        clean = stage / "Tingmu"
        print(f"Building Tingmu {version} from .venv (log: {diagnostics / 'build.log'})", flush=True)
        with (diagnostics / "build.log").open("wb") as log:
            build_portable(clean, log)
        files, unpacked, pe_count = audit_bundle(clean, version)
        print("Running relocated offline launcher smoke...", flush=True)
        clone = stage / "听幕 搬迁验证"
        shutil.copytree(clean, clone)
        report = run_smoke(clone, media, diagnostics)
        check_smoke(report, version, media is not None)
        original_bytecode = {name for name in files if name.endswith(".pyc")}
        added = {p.relative_to(clone).as_posix() for p in clone.rglob("*.pyc")} - original_bytecode
        if added:
            raise ValueError(f"New bytecode after launcher smoke: {sorted(added)}")
        name = f"Tingmu-{version}-windows-x64-portable.zip"
        print(f"Archiving pristine build as {name}", flush=True)
        # Stage on the output filesystem for rollback-safe publication across drive letters.
        with tempfile.TemporaryDirectory(prefix=".release-", dir=output) as folder:
            artifacts = Path(folder)
            archive = artifacts / name
            write_archive(clean, files, archive)
            with archive.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            summary = {"version": version, "archive": name, "file_count": len(files),
                       "unpacked_bytes": unpacked, "zip_bytes": archive.stat().st_size,
                       "sha256": digest, "pe_files_checked": pe_count, "model_payloads": 0,
                       "first_run": FIRST_RUN}
            (artifacts / "SHA256SUMS.txt").write_text(f"{digest}  {name}\n", encoding="utf-8", newline="\n")
            (artifacts / "release-summary.json").write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
            names = (name, "SHA256SUMS.txt", "release-summary.json")
            for asset in names:
                if (output / asset).is_dir():
                    raise IsADirectoryError(output / asset)
            publish(artifacts, output, names)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Directory for release assets and diagnostics")
    parser.add_argument("--expect-version", help="Require an exact match with project.version")
    parser.add_argument("--smoke-media", type=Path, help="Two-audio-track local video for full playback smoke")
    args = parser.parse_args()
    print(json.dumps(build_release(args.output, args.expect_version, args.smoke_media), indent=2))
