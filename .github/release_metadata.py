"""Resolve the release identity before building, and format notes from the audited archive."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib


def release_version(project_version, tag):
    number = r"(?:0|[1-9][0-9]*)"
    identifier = r"[0-9A-Za-z-]+"
    identifiers = rf"{identifier}(?:\.{identifier})*"
    pattern = rf"{number}\.{number}\.{number}(?:-(?P<pre>{identifiers}))?(?:\+{identifiers})?"
    match = re.fullmatch(pattern, project_version)
    if not match or any(part.isdigit() and len(part) > 1 and part.startswith("0")
                        for part in (match["pre"] or "").split(".")):
        raise ValueError(f"Expected a semantic release version, got {project_version!r}")
    if tag is not None and tag != f"v{project_version}":
        raise ValueError(f"Tag {tag!r} does not match project.version {project_version!r}")
    return project_version


def change_history(version, changes, repository_url):
    tag = f"v{version}"
    previous = changes["previous_tag"]
    if previous:
        release_version(previous.removeprefix("v"), previous)
        if previous == tag:
            raise ValueError("previous_tag must differ from the release tag")
    history_url = f"{repository_url}/compare/{previous}...{tag}" if previous else f"{repository_url}/commits/{tag}"
    entries = changes.get("changes", [])
    if not entries:
        raise ValueError(f"Add at least one bilingual change entry for {tag}")

    lines = ["## 更新内容 / What’s changed", ""]
    contributors = []
    for entry in entries:
        zh, en = entry.get("zh", "").strip(), entry.get("en", "").strip()
        if zh == en or not re.search(r"[\u3400-\u9fff]", zh) or not re.search(r"[A-Za-z]", en):
            raise ValueError(f"Each change for {tag} needs both Chinese (zh) and English (en) text")
        references = []
        if author := entry.get("author"):
            references.append(f"@{author}")
        if pr := entry.get("pr"):
            references.append(f"[#{pr}]({repository_url}/pull/{pr})")
        if entry.get("first_contribution"):
            if not author or not pr:
                raise ValueError("A first contribution needs an author and pull request")
            contributors.append(" · ".join(references))
        lines.extend([f"- {zh}  ", f"  {en}"])
        if references:
            lines[-1] += " " + " · ".join(references)

    if contributors:
        lines.extend(["", "## 首次贡献者 / New contributors", "", *(f"- {item}" for item in contributors)])
    lines.extend(["", f"[完整更新记录 / Full changelog]({history_url})", ""])
    return "\n".join(lines)


def format_notes(summary, changes, repository_url):
    mib = 1024**2
    return (
        "## Windows x64 便携版 / Windows x64 portable edition\n\n"
        "| 项目 / Package details | 实测值 / Audited value |\n"
        "| --- | --- |\n"
        f"| 压缩包 / ZIP archive | `{summary['archive']}` |\n"
        f"| 下载大小 / Download size | {summary['zip_bytes'] / mib:.2f} MiB |\n"
        f"| 解压大小 / Unpacked size | {summary['unpacked_bytes'] / mib:.2f} MiB |\n"
        f"| 文件数 / Files | {summary['file_count']:,} |\n"
        f"| ZIP SHA-256 (`SHA256SUMS.txt`) | `{summary['sha256']}` |\n"
        "| 随包模型权重 / Bundled model weights | **0** |\n\n"
        "### 安装与首次启动 / Installation and first launch\n\n"
        "将整个 ZIP 解压到可写目录，双击 `Tingmu/Tingmu.exe`。首次启动会离线检测本机工具。"
        "选择「暂不下载，直接进入」，即可用 **0 B** 下载开始播放。"
        "运行时可稍后按需安装，模型权重可在工作区设置中单独下载。"
        "在「视图 → 界面语言 / Language」可切换界面语言。\n\n"
        "Extract the entire ZIP to a writable folder and double-click `Tingmu/Tingmu.exe`. "
        "On first launch, Tingmu checks for local tools offline. Choose **Skip downloads and enter** "
        "(暂不下载，直接进入) to play a video with **0 B** of additional downloads. "
        "To switch to English, choose **视图 → 界面语言 / Language → English**. You can install runtimes "
        "later and download model weights separately in workspace settings.\n\n"
        + change_history(summary["version"], changes, repository_url)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, help="Generate bilingual notes beside this audited summary")
    args = parser.parse_args()
    with Path("pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]
    tag = os.environ["GITHUB_REF_NAME"] if os.environ.get("GITHUB_REF_TYPE") == "tag" else None
    version = release_version(project["version"], tag)
    if args.summary:
        summary = json.loads(args.summary.read_text(encoding="utf-8"))
        if summary["version"] != version or summary["model_payloads"] != 0:
            raise ValueError("Release summary does not match the validated version or model policy")
        changes_file = Path(__file__).parent / "release-changes" / f"v{version}.toml"
        with changes_file.open("rb") as stream:
            changes = tomllib.load(stream)
        server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
        repository = os.environ.get("GITHUB_REPOSITORY", "DennyClarkson/tingmu")
        notes = format_notes(summary, changes, f"{server}/{repository}")
        args.summary.with_name("release-notes.md").write_text(notes, encoding="utf-8")
        if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
            with Path(step_summary).open("a", encoding="utf-8") as stream:
                stream.write(notes)
    elif outputs := os.environ.get("GITHUB_OUTPUT"):
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        with Path(outputs).open("a", encoding="utf-8") as stream:
            print(f"version={version}", f"commit={commit}", sep="\n", file=stream)
    print(f"Validated release version {version}")


if __name__ == "__main__":
    main()
