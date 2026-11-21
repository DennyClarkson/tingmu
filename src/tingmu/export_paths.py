"""Keep exported material in the owning study folder."""
from pathlib import Path, PureWindowsPath
import re


def export_path(workspace, requested, suffixes):
    root = Path(workspace).resolve()
    folder = root / "exports"
    if folder.resolve() != folder:
        raise ValueError("导出文件夹不能指向其他位置。")
    path = Path(requested)
    if not path.is_absolute() and path.parent == Path("."):
        path = folder / path
    destination = path.resolve()
    if (not destination.is_relative_to(folder) or destination == folder
            or destination.suffix.lower() not in suffixes
            or any(PureWindowsPath(part).is_reserved() or re.search(r'[<>:"|?*\x00-\x1f]', part)
                   for part in destination.relative_to(folder).parts)
            or destination.exists() and destination.stat().st_nlink > 1):
        raise ValueError(f"请选择当前工作区 exports 文件夹内的文件（{'、'.join(suffixes)}）。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination
