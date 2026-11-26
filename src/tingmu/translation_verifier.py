"""Translation validation adapter for the common bounded batch scheduler."""

from copy import deepcopy
from threading import Event

from .agent import AnnotationValidationError, TextBatchTask, batch_worker, splittable_batch_error
from .batch_scheduler import BatchResponse, ConcurrentBatchScheduler, RepairPolicy
from .providers import RequestCancelled
from .task_progress import Progress, batch_progress
from .translation_review import lesson_digest, translation_available
from .studio_metrics import verified_examples


def verify_document(document, agent, store, report, check_cancel, *, event_factory=Event, on_delta=None, cancelled=None):
    review = document.metadata["translation_review"]
    total = len(document.cues) - len(review.get("provided", {}))
    review["status"] = "running"
    document.metadata["stage"] = "verifying"
    store.save_document(document)
    positions = {cue.id: index for index, cue in enumerate(document.cues)}
    context_texts = [cue.text for cue in document.cues]
    pending_errors = review.setdefault("pending_errors", {})
    plans = [tuple(index for index in range(start, min(start + 24, len(document.cues)))
                   if not translation_available(document.cues[index], review))
             for start in range(0, len(document.cues), 24)]
    plans = [indices for indices in plans if indices]
    template = batch_worker(agent)
    progress_failed = False

    def progress(message):
        nonlocal progress_failed
        if not progress_failed:
            try:
                report(message)
            except Exception as exc:
                progress_failed = True
                scheduler.stop(exc)

    def execute(job, stopped):
        worker = batch_worker(template)
        if isinstance(worker, TextBatchTask):
            worker.bind_batch(job.index + 1, on_delta, cancelled)
        worker.allow_partial_translations = True
        worker.repair_feedback = dict(job.feedback)
        pending = deepcopy([document.cues[index] for index in job.indices])
        neighbors = {index for number in job.indices
                     for index in range(max(0, number - 3), min(len(context_texts), number + 4))}
        context = [context_texts[index] for index in sorted(neighbors)]
        try:
            result = worker.verify_translations(pending, document.language, context)
        except Exception as exc:
            # Transport failures have no validated request record. Content errors
            # keep provider metadata and enter the same repair planner as teaching.
            requests = [worker.last_response_metadata] if splittable_batch_error(exc) and worker.last_response_metadata else []
            return BatchResponse(requests=requests, error=exc)
        return BatchResponse(result, [worker.last_response_metadata])

    def located_error(index, message, original):
        error = original if original is not None else AnnotationValidationError(message)
        error.cue_number, error.cue_start = index + 1, document.cues[index].start
        return error

    def before_submit(job):
        event = batch_progress(job.index + 1, job.indices[0] + 1, job.indices[-1] + 1,
                               len(job.indices), repair=bool(job.feedback))
        progress(Progress(f"逐句译文核验 · {event}", **event.details))

    def publish(scheduler, outcome):
        job = outcome.job
        for cue, segments in outcome.completed:
            document.cues[positions[cue.id]] = cue
            if segments:
                review["segments"][cue.id] = segments
            else:
                review["segments"].pop(cue.id, None)
            review["verified"][cue.id] = lesson_digest(cue, segments)
        if outcome.requests or outcome.completed:
            metadata = outcome.requests[-1] if outcome.requests else {}
            review["responses"].append(dict(metadata, cue_start_index=job.indices[0],
                requested_count=len(job.indices), verified_count=len(outcome.completed), repair_attempt=job.attempt))
            review["responses"].sort(key=lambda row: row.get("cue_start_index", -1))
        event = batch_progress(job.index + 1, job.indices[0] + 1, job.indices[-1] + 1,
                               len(job.indices), done=len(outcome.completed), status=outcome.status, repair=bool(job.feedback))
        progress(Progress(f"译稿已核验 {len(review['verified'])}/{total} 句", **event.details,
                          done=len(review["verified"]), total=total, unit="句",
                          verified_examples=verified_examples([cue for cue, _ in outcome.completed], review)))
        if outcome.children:
            first = next(index for index in job.indices if document.cues[index].id in outcome.errors)
            progress(f"译文核验：第 {first + 1} 句 · {outcome.errors[document.cues[first].id]} "
                     f"仅重试 {len(outcome.errors)} 句。")

    scheduler = ConcurrentBatchScheduler(
        keys=[cue.id for cue in document.cues], plans=plans, execute=execute, max_workers=template.max_workers,
        result_key=lambda result: result[0].id, recoverable=splittable_batch_error, make_error=located_error,
        policy=RepairPolicy(stop_singleton_after_retry=False, split_partial_after_retry=False,
                            retry_invalid_singleton=True, reset_attempt_on_split=True),
        check_cancel=check_cancel, cancelled=cancelled, cancelled_error=lambda exc: isinstance(exc, RequestCancelled),
        stop_event=event_factory(), feedback=pending_errors,
        before_submit=before_submit, on_update=publish, checkpoint=lambda _: store.save_document(document),
        thread_name="tingmu-translation")
    review["pending_errors"] = scheduler.pending_errors
    review["checkpoint_metadata"] = scheduler.checkpoint_metadata
    agent.checkpoint_metadata = scheduler.checkpoint_metadata
    scheduler.run()
    check_cancel()
    if cancelled and cancelled():
        raise RequestCancelled("译文核验已取消。")
    if not all(translation_available(cue, review) for cue in document.cues):
        raise ValueError("仍有译文未通过核验。")
    review["status"] = "ready"
