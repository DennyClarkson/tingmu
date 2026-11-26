import json
from pathlib import Path
import sys

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


def test_notes_cli_combines_audited_measurements_with_tagged_bilingual_history(metadata, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_REF_TYPE", "tag")
    monkeypatch.setenv("GITHUB_REF_NAME", "v0.12.0")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/tingmu")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "step.md"))
    Path("pyproject.toml").write_text('[project]\nversion = "0.12.0"', encoding="utf-8")
    Path("release-summary.json").write_text(json.dumps({
        "version": "0.12.0",
        "archive": "Tingmu-0.12.0-windows-x64-portable.zip",
        "zip_bytes": 1024**2,
        "unpacked_bytes": 3 * 1024**2,
        "file_count": 1242,
        "sha256": "b" * 64,
        "model_payloads": 0,
    }), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["release_metadata.py", "--summary", "release-summary.json"])
    metadata.main()
    notes = Path("release-notes.md").read_text(encoding="utf-8")
    assert notes == Path("step.md").read_text(encoding="utf-8")
    for expected in (
        "Tingmu-0.12.0-windows-x64-portable.zip", "1.00 MiB", "3.00 MiB", "1,242", "b" * 64, "SHA256SUMS.txt",
        "Tingmu/Tingmu.exe", "随包模型权重 / Bundled model weights | **0**",
        "暂不下载，直接进入", "Skip downloads and enter", "0 B",
        "三语界面", "three-language interface", "首次贡献者 / New contributors", "@DennyClarkson",
        "https://github.com/example/tingmu/pull/1", "https://github.com/example/tingmu/pull/2",
        "https://github.com/example/tingmu/pull/3", "https://github.com/example/tingmu/commits/v0.12.0",
    ):
        assert expected in notes


def test_later_release_compares_the_specified_tags(metadata):
    notes = metadata.change_history("1.2.3-rc.1", {
        "previous_tag": "v1.2.2", "changes": [{"zh": "改进播放。", "en": "Improved playback."}],
    }, "https://github.com/example/tingmu")
    assert "改进播放。" in notes and "Improved playback." in notes
    assert "[完整更新记录 / Full changelog](https://github.com/example/tingmu/compare/v1.2.2...v1.2.3-rc.1)" in notes


@pytest.mark.parametrize("entries", [
    [], [{"zh": "改进播放。"}], [{"en": "Improved playback."}], [{"zh": "改进播放。", "en": "改进播放。"}],
])
def test_release_requires_paired_change_text(metadata, entries):
    with pytest.raises(ValueError):
        metadata.change_history("1.2.3", {"previous_tag": "v1.2.2", "changes": entries}, "https://example.com")
