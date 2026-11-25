from pathlib import Path

import pytest


@pytest.fixture
def metadata(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / ".github"))
    import release_metadata

    return release_metadata


@pytest.mark.parametrize("version", ["0.12.0", "0.12.1-rc.1", "1.2.3+cpu-x64"])
def test_release_tag_round_trip(metadata, version):
    assert metadata.release_version(version, "v" + version) == version
    assert metadata.release_version(version, None) == version


@pytest.mark.parametrize(("version", "tag"), [
    ("0.12.0", "v0.12.1"),
    ("0.12.1-rc.1", "v0.12.1rc1"),
    ("1.2.3", "1.2.3"),
    ("01.2.3", None),
    ("1.2.3-rc.01", None),
    ("1.2.3\nnew=value", None),
])
def test_malformed_or_mismatched_release_tag_is_rejected(metadata, version, tag):
    with pytest.raises(ValueError):
        metadata.release_version(version, tag)


def test_chinese_release_notes_use_archive_measurements(metadata):
    notes = metadata.format_notes({
        "archive": "Tingmu-1.2.3-windows-x64-portable.zip",
        "zip_bytes": 1024**2,
        "unpacked_bytes": 3 * 1024**2,
        "file_count": 42,
        "sha256": "b" * 64,
    })
    for expected in ("1.00 MiB", "3.00 MiB", "42", "b" * 64, "Tingmu.exe", "0 模型权重随包", "0 B"):
        assert expected in notes
