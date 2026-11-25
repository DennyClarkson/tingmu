"""Recognized media filenames and classic subtitle sidecar selection."""

from pathlib import Path


MEDIA_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".m4v", ".wmv", ".flv",
    ".ts", ".mts", ".m2ts", ".mpg", ".mpeg", ".3gp", ".ogv",
    ".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma",
})
SUBTITLE_EXTENSIONS = frozenset({".srt", ".vtt", ".ass", ".ssa"})


def find_subtitle(media_path: Path, target_language: str) -> Path | None:
    for stem in (f"{media_path.stem}.{target_language}", media_path.stem):
        for extension in (".srt", ".ass", ".vtt", ".ssa"):
            candidate = media_path.with_name(stem + extension)
            if candidate.is_file():
                return candidate
    return None
