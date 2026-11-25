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


def format_notes(summary):
    mib = 1024**2
    return (
        "## Windows x64 便携版\n\n"
        f"- 压缩包 `{summary['archive']}`，{summary['zip_bytes'] / mib:.2f} MiB。\n"
        f"- 解压后 {summary['unpacked_bytes'] / mib:.2f} MiB，共 {summary['file_count']:,} 个文件。\n"
        f"- ZIP 的 SHA-256 为 `{summary['sha256']}`，也可核对附件 `SHA256SUMS.txt`。\n"
        "- **0 模型权重随包**。完整解压 ZIP，双击 `Tingmu/Tingmu.exe`。\n\n"
        "首次启动会离线检测本机工具。选择「暂不下载，直接进入」，即可 **0 B** 下载开始播放。"
        "运行时可稍后按需安装，模型权重在工作区配置中单独下载。\n"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, help="Generate Chinese notes beside this audited summary")
    args = parser.parse_args()
    with Path("pyproject.toml").open("rb") as stream:
        project = tomllib.load(stream)["project"]
    tag = os.environ["GITHUB_REF_NAME"] if os.environ.get("GITHUB_REF_TYPE") == "tag" else None
    version = release_version(project["version"], tag)
    if args.summary:
        summary = json.loads(args.summary.read_text(encoding="utf-8"))
        if summary["version"] != version or summary["model_payloads"] != 0:
            raise ValueError("Release summary does not match the validated version or model policy")
        notes = format_notes(summary)
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
