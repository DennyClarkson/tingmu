"""Assemble the portable judge demo from ready workspace documents."""
import argparse
from contextlib import closing
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from tingmu.global_models import PROFILE_FIELDS
from tingmu.media import probe_media
from tingmu.models import StudyDocument, preparation_identity_settings
from tingmu.portable_paths import MANIFEST, remap_study_records
from tingmu.quick.catalog_media import catalog_path
from tingmu.storage import Store, atomic_json, media_fingerprint
from tingmu.workspaces import MARKER, WorkspaceCatalog


def read(path):
    return json.loads(path.read_bytes())


def copy_workspace(src, dst, public, language, location):
    settings = read(src / "settings.json")
    if settings["source_language"] != "zh-CN" or settings["target_language"] != language:
        raise ValueError("Workspace languages changed")
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("notebooks", "exports", "regroup-audit", "asr-audit"):
        if (src / name).is_dir():
            shutil.copytree(src / name, dst / name)
    for name in (MARKER, "quick-layout.json"):
        if (src / name).is_file():
            shutil.copy2(src / name, dst / name)
    settings.update(developer_mode=False, library_path=str(dst / "downloads"), auto_prepare_media=False,
                    allow_audio_upload=False)
    atomic_json(dst / "settings.json", settings)
    atomic_json(dst / MANIFEST, {"version": 1, "root": str(location)})
    with closing(sqlite3.connect((src / "study.db").as_uri() + "?mode=ro", uri=True)) as source:
        with closing(sqlite3.connect(dst / "study.db")) as target:
            source.backup(target)
    store = Store(dst)
    mapping = {}
    items = []
    try:
        for path in sorted((src / "documents").glob("*.json")):
            record = read(path)
            if record["metadata"].get("stage") != "ready" or record["language"] != asdict(store._workspace_settings().language):
                continue
            doc = StudyDocument.from_dict(record)
            media = Path(doc.media_path)
            if media_fingerprint(media) != doc.metadata["media_fingerprint"]:
                raise ValueError("Prepared media changed: " + media.name)
            with media.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            seconds = round(float(probe_media(media)["format"]["duration"]))
            item = dict(id=language + "-" + digest[:12], title=media.stem, language=language,
                        duration=f"{seconds // 60}:{seconds % 60:02d}", suffix=media.suffix.lower(),
                        credit="体验工作区", bytes=media.stat().st_size, sha256=digest,
                        mtime_ns=media.stat().st_mtime_ns)
            items.append(item)
            dest = catalog_path(dst, item)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(media, dest)
            shutil.copy2(media, public / (item["id"] + item["suffix"]))
            mapping[str(media)] = str(dest)
            doc.media_path = str(dest)
            store.save_document(doc)
            shareable = doc.to_dict()
            shareable["media_path"] = media.name
            atomic_json(public / (item["id"] + ".lesson.json"), shareable)
            target, _ = store.material_paths(str(dest), doc.language)
            for ext in (".srt", ".translation.srt"):
                if path.with_suffix(ext).is_file():
                    shutil.copy2(path.with_suffix(ext), target.with_suffix(ext))
            restored = store.load_document(str(dest), doc.language)
            if not restored or restored.cues != doc.cues:
                raise ValueError("Prepared material did not round-trip: " + media.name)
            print(language, media.name, len(doc.cues), "cues", flush=True)
        if not items:
            raise ValueError("No ready material for " + language)
        with store._connection(immediate=True) as db:
            remap_study_records(db, lambda value: mapping.get(value, value))
        library = read(src / "media-library.json")
        library["items"] = [{**row, "media_path": mapping[row["media_path"]]} for row in library["items"]
                            if row["media_path"] in mapping]
        present = {row["media_path"] for row in library["items"]}
        library["items"].extend({"media_path": path} for path in mapping.values() if path not in present)
        library["ignored"] = {}
        atomic_json(dst / "media-library.json", library)
        queue = read(src / "preparation-queue.json")
        queue["jobs"] = [j for j in queue["jobs"] if j["media_path"] in mapping and j["status"] == "completed"]
        for job in queue["jobs"]:
            job["media_path"] = mapping[job["media_path"]]
            job["settings"].update(library_path=settings["library_path"], allow_audio_upload=False)
            spec = preparation_identity_settings(job["settings"])
            parts = (["existing-lessons", job["media_path"], job["fingerprint"], spec] if job.get("reuse_existing_transcript")
                     else [job["media_path"], job["fingerprint"], spec, job.get("subtitle_path"), None])
            if not job.get("reuse_existing_transcript") and "subtitle_inputs" in job:
                parts.append(job["subtitle_inputs"])
            job["id"] = hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:24]
        atomic_json(dst / "preparation-queue.json", queue)
        last = read(src / "home-history.json").get("path") if (src / "home-history.json").exists() else None
        atomic_json(dst / "home-history.json", {"path": mapping.get(last, next(iter(mapping.values())))})
    finally:
        store.close()
    return items


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("source", type=Path)
    cli.add_argument("program", type=Path)
    cli.add_argument("destination", type=Path)
    args = cli.parse_args()
    src, program, out = (p.resolve() for p in (args.source, args.program, args.destination))
    rows = WorkspaceCatalog(src).summaries()
    selected = []
    for lang in ("yue", "en"):
        row, = [r for r in rows if (r["source_language"], r["target_language"]) == ("zh-CN", lang)]
        selected.append(row)
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        raise ValueError("Choose an empty destination for the judge demo")
    for name in ("app", "runtime"):
        shutil.copytree(program / name, out / name)
    for name in ("Tingmu.exe", "tingmu-portable.json", "LICENSE", "THIRD_PARTY_NOTICES.md", "使用说明.md"):
        shutil.copy2(program / name, out / name)
    public = out / "demo-source"
    public.mkdir()
    data = out / "data"
    catalog = read(src / "workspaces.json")
    catalog["items"] = [row for row in catalog["items"] if any(r["id"] == row["id"] for r in selected)]
    catalog["active"] = selected[0]["id"]
    items = []
    for row in selected:
        original = Path(row["path"])
        target = data / original.relative_to(src)
        items.extend(copy_workspace(original, target, public, row["target_language"], out if target == data else target))
    atomic_json(public / "catalog.json", items)
    atomic_json(data / "workspaces.json", catalog)
    if (src / "model-profiles.json").exists():
        registry = read(src / "model-profiles.json")
        for key, profile in registry["profiles"].items():
            public_fields = PROFILE_FIELDS - {"_protected_key"}
            row = {k: v for k, v in profile.items() if k in public_fields}
            if row.get("key_source") == "manual":
                row["key_source"] = "environment"
            registry["profiles"][key] = row
        atomic_json(data / "model-profiles.json", registry)
    print(out)


if __name__ == "__main__":
    main()
