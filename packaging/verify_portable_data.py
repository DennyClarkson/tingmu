"""Verify prepared bundled examples through the shipped Store after relocation."""
import argparse
import hashlib
import json
from pathlib import Path

from tingmu.models import LanguagePair
from tingmu.runtime import portable_root
from tingmu.storage import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = portable_root()
    assert root is not None
    store = Store()
    rows = []
    for media in sorted((root / "examples").glob("*.mkv")):
        document = store.load_document(str(media), LanguagePair())
        assert document is not None, f"Prepared document missing: {media.name}"
        assert document.metadata["stage"] == "ready", media.name
        audit = Path(document.metadata["asr_audit_path"])
        assert audit.is_relative_to(root) and audit.is_file(), str(audit)
        preserved = [[cue.id, cue.start, cue.end, cue.original_text] for cue in document.cues]
        rows.append({"media": media.name, "cues": len(document.cues),
                     "raw_identity_sha256": hashlib.sha256(json.dumps(preserved).encode()).hexdigest(),
                     "fingerprint": document.metadata["media_fingerprint"],
                     "audit_exists": True, "prepared": True})
    assert len(rows) == 3
    report = {"root": str(root), "success": True, "examples": rows,
              "study_entry_count": len(store.entries())}
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"success": True, "cue_counts": [row["cues"] for row in rows]}))


if __name__ == "__main__":
    main()
