"""Compatibility entry points for the explicit, resumable preparation stages."""
from copy import deepcopy
from pathlib import Path
from threading import Event

from .agent import ANNOTATION_VERSION, LearningAgent, annotate_local
from .agent import AnnotationValidationError as AnnotationValidationError
from .language import normalize_cues
from .media import load_subtitles, transcribe
from .models import AppSettings, StudyDocument
from .review import REVIEW_VERSION, TranscriptReviewer
from .storage import Store
from .translation_review import TRANSLATION_REVIEW_VERSION
from . import preparation
from .preparation import PreparationCancelled as PreparationCancelled
from .preparation import _compatible_asr_cache as _compatible_asr_cache
from .preparation import _evidence_progress as _evidence_progress
from .preparation import _needs_text_configuration as _needs_text_configuration


def find_subtitle(media_path: Path, target_language: str) -> Path | None:
    for stem in (f"{media_path.stem}.{target_language}", media_path.stem):
        for extension in (".srt", ".ass", ".vtt", ".ssa"):
            candidate = media_path.with_name(stem + extension)
            if candidate.is_file():
                return candidate
    return None



def _ports():
    # Read compatibility globals at call time so integrations can replace an
    # inference/annotation backend without importing the stage implementation.
    return preparation.PreparationPorts(
        find_subtitle=find_subtitle, transcribe=transcribe, load_subtitles=load_subtitles,
        normalize_cues=normalize_cues, annotate_local=annotate_local,
        LearningAgent=LearningAgent, TranscriptReviewer=TranscriptReviewer,
        ANNOTATION_VERSION=ANNOTATION_VERSION, REVIEW_VERSION=REVIEW_VERSION,
        TRANSLATION_REVIEW_VERSION=TRANSLATION_REVIEW_VERSION,
        review_document=_review_document, verify_document=_verify_document,
    )


def _review_document(document, settings, store, spec, report, check_cancel, cancelled, profile=None):
    return preparation.review_document(document, settings, store, spec, report,
                                       check_cancel, cancelled, profile, ports=_ports())


def _verify_document(document, agent, store, report, check_cancel):
    from .translation_verifier import verify_document
    return verify_document(document, agent, store, report, check_cancel, event_factory=Event)


def prepare_media(media_path: Path, settings: AppSettings, store: Store,
                  subtitle_path: Path | None = None, start: float = 0,
                  duration: float | None = None, progress=None, cancelled=None,
                  force_asr: bool = False, audio_permission=None, allow_model_downloads: bool = False,
                  reuse_existing_transcript: bool = False) -> StudyDocument:
    ctx = preparation.PreparationContext(
        media_path=media_path, settings=deepcopy(settings), store=store, ports=_ports(),
        subtitle_path=subtitle_path, start=start, duration=duration, progress=progress,
        cancelled=cancelled, force_asr=force_asr, audio_permission=audio_permission,
        allow_model_downloads=allow_model_downloads, reuse_existing_transcript=reuse_existing_transcript,
    )
    preparation.plan_preparation(ctx)
    preparation.resolve_models(ctx)
    preparation.acquire_transcript(ctx)
    if ctx.finished:
        return ctx.document
    preparation.run_review_stage(ctx)
    try:
        preparation.run_teaching_stage(ctx)
        preparation.run_verification_stage(ctx)
        return preparation.publish_document(ctx)
    except Exception as exc:
        if ctx.teaching_initialized:
            preparation.checkpoint_failure(ctx, exc)
        raise
