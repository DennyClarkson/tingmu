"""Portable references to previously downloaded CTranslate2 Whisper model folders."""
from pathlib import Path

from .runtime import cache_root, confined_path


def model_reference(directory) -> str:
    root = confined_path(cache_root(), "models")
    try:
        folder = confined_path(root, directory)
    except ValueError:
        raise ValueError("请选择项目共享缓存 .cache/models 内的 CTranslate2 模型文件夹。") from None
    # The tokenizer is needed for a fully local load. HF snapshots may link into
    # their sibling blobs folder, which is also within this shared model cache.
    for name in ("model.bin", "config.json", "tokenizer.json"):
        file = confined_path(root, folder / name)
        if not file.is_file() or not file.stat().st_size:
            raise ValueError(f"该文件夹缺少可用的 {name}，请选择完整的 CTranslate2 Whisper 模型。")
    return "local:" + folder.relative_to(root).as_posix()


def resolve_local_model(model_id: str) -> Path:
    if not model_id.startswith("local:") or not model_id[6:]:
        raise ValueError("本地模型标识无效。")
    relative = Path(model_id[6:])
    if relative.is_absolute() or relative.drive or ".." in relative.parts:
        raise ValueError("本地模型须位于项目共享缓存内。")
    folder = confined_path(cache_root(), Path("models") / relative)
    model_reference(folder)
    return folder
