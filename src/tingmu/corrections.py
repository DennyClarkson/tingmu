"""Revise one frozen display subtitle and the lesson of its acoustic source."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import re
import uuid

from .app_actions import ActionError
from .language import learning_text
from .timeline import SubtitleTimeline, split_display_text


@lru_cache(maxsize=1)
def _simplifier():
    from opencc import OpenCC
    return OpenCC("t2s")


def _lesson_strings(args, language):
    translation, explanation = args["translation"].strip(), args["explanation"].strip()
    if language.source.lower() in {"zh-cn", "zh-hans", "zh-sg"}:
        convert = _simplifier().convert
        translation = convert(translation)
        # The lesson prose uses the known language; quoted study examples retain
        # the target spelling for comparison with the subtitle.
        parts = re.split(r"(「[^」]*」|『[^』]*』|`[^`]*`|“[^”]*”)", explanation)
        explanation = "".join(text if i % 2 else convert(text) for i, text in enumerate(parts))
    return translation, explanation


def freeze_target(document, source, display_id):
    previous = document.metadata.get("cue_corrections", {}).get(source.id, {})
    content = [asdict(source), previous.get("revision", ""), document.metadata.get("media_fingerprint")]
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"source_id": source.id, "display_id": display_id, "revision": digest}


def _locate(document, target):
    if not document or not target:
        raise ActionError("本次请求没有可修订的字幕，请定位到一句后重试。", "missing_context")
    matches = [c for c in document.cues if c.id == target["source_id"]]
    if len(matches) != 1 or freeze_target(document, matches[0], target["display_id"]) != target:
        raise ActionError("这句字幕已变化，请重新打开该句发起修订。", "revision_conflict")
    source = matches[0]
    parts = SubtitleTimeline([source]).cues
    index = next((i for i, c in enumerate(parts) if c.id == target["display_id"]), None)
    if index is None:
        raise ActionError("这句字幕的分段已变化，请重新选择。", "revision_conflict")
    return source, [c.text for c in parts], index


def read_target(document, target):
    source, parts, index = _locate(document, target)
    return {"revision": target["revision"], "selected_text": parts[index],
            "source_prefix": "".join(parts[:index]), "source_suffix": "".join(parts[index + 1:]),
            "original_text": source.original_text, "speaker": getattr(source, "speaker", ""),
            "translation": source.translation, "explanation": source.explanation,
            "start": source.start, "end": source.end, "uncertain": source.uncertain}


def revise_target(store, context, args, script, cancelled):
    from .agent import AnnotationValidationError, validate_annotations
    from .providers import RequestCancelled
    with store._material_write():
        path, _ = store.material_paths(context.media_path, context.language)
        store._recover_material(path)
        document = store.load_document(context.media_path, context.language)
        source, parts, index = _locate(document, context.correction)
        if args["revision"] != context.correction["revision"]:
            raise ActionError("修订版本不匹配，请重新读取此句。", "revision_conflict")
        if any(not args[k].strip() for k in ("selected_text", "translation", "explanation")):
            raise ActionError("请提供完整的当前字幕、整句译文和讲解。", "invalid_arguments")
        selected = learning_text(args["selected_text"], document.language, script)
        changed = selected != parts[index]
        if changed and ("\n" in selected or "\r" in selected):
            raise ActionError("修订的显示字幕须为单行。", "invalid_arguments")
        if changed:
            parts[index:index + 1] = split_display_text(selected)
        text = "".join(parts)
        translation, explanation = _lesson_strings(args, document.language)
        lesson = replace(source, text=text, translation=translation, explanation=explanation,
                         segmented_texts=parts if changed else source.segmented_texts)
        if changed and not args.get("tokens"):
            raise ActionError("修改字幕正文时请同时提供完整源句的分词与读音。", "invalid_arguments")
        if args.get("tokens") is not None:
            row = {"id": source.id, "text": text, "translation": lesson.translation,
                   "explanation": lesson.explanation, "uncertain": source.uncertain, "tokens": args["tokens"]}
            try:
                lesson.tokens = validate_annotations(json.dumps({"cues": [row]}, ensure_ascii=False),
                                                     [source], document.language)[0].tokens
            except AnnotationValidationError as exc:
                raise ActionError(str(exc), "invalid_arguments") from None
        if cancelled and cancelled():
            raise RequestCancelled("修订已停止。")
        # Patch only owned fields in the persisted cue. An older/newer producer
        # may have attached alignment evidence outside the current dataclass.
        payload = json.loads(path.read_bytes())
        row = next(item for item in payload["cues"] if item["id"] == source.id)
        row.update(text=lesson.text, original_text=source.original_text,
                   translation=lesson.translation, explanation=lesson.explanation)
        if changed:
            row["segmented_texts"] = lesson.segmented_texts
        if args.get("tokens") is not None:
            row["tokens"] = [asdict(token) for token in lesson.tokens]
        store._backup_material(path)
        document.cues[document.cues.index(source)] = lesson
        review = document.metadata.get("translation_review", {})
        if review and source != lesson:
            review.get("verified", {}).pop(source.id, None)
            review.get("provided", {}).pop(source.id, None)
            if source.translation != lesson.translation or source.text != lesson.text:
                document.metadata.get("provided_translations", {}).pop(source.id, None)
            review.get("segments", {}).pop(source.id, None)
            review["status"] = "partial"
            document.metadata["lesson_status"] = "draft"
        document.metadata.setdefault("cue_corrections", {})[source.id] = {
            "revision": uuid.uuid4().hex, "updated_at": datetime.now(timezone.utc).isoformat(),
            "display_id": context.correction["display_id"], "text": lesson.text,
            "segmented_texts": lesson.segmented_texts, "guidance": context.guidance, "model": context.model}
        store._save_document(document, payload=payload)
        return document
