"""Bounded text batches with one repair, cancellation and checkpoint lifecycle.

Workers only return validated values and request accounting. All publication and
checkpoint callbacks run on the calling thread, including when another job fails.
"""

from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from copy import deepcopy
from dataclasses import dataclass, field
from threading import Event


@dataclass
class PartialBatchResult[T]:
    completed: list[T]
    errors: dict[str, str]


@dataclass(frozen=True)
class RepairPolicy:
    split_invalid: bool = True
    max_attempts: int = 2
    stop_singleton_after_retry: bool = True
    split_partial_after_retry: bool = True
    retry_invalid_singleton: bool = False
    reset_attempt_on_split: bool = False


@dataclass(frozen=True)
class BatchJob:
    index: int
    indices: tuple[int, ...]
    feedback: dict[str, str] = field(default_factory=dict)
    attempt: int = 0
    parent: int | None = None


@dataclass
class BatchResponse:
    value: object = None
    requests: list[dict] = field(default_factory=list)
    error: Exception | None = None


@dataclass
class BatchOutcome:
    job: BatchJob
    completed: list
    errors: dict[str, str]
    requests: list[dict]
    status: str
    failure: Exception | None = None
    children: list[tuple[tuple[int, ...], dict[str, str], int]] = field(default_factory=list)


class ConcurrentBatchScheduler:
    """Schedule source-index jobs and drain every issued response before raising.

    ``execute(job, stopped)`` returns BatchResponse. ``on_update`` receives the
    scheduler and outcome after its ordered accounting is updated. ``checkpoint``
    receives an independent cumulative result list, before any repair submission.
    Content failures alone enter RepairPolicy; transport errors never retry here.
    """

    def __init__(self, *, keys, plans, execute, max_workers, result_key,
                 recoverable, make_error, policy=None, cancelled=None,
                 check_cancel=None, stop_event=None, cancelled_error=None,
                 feedback=None, before_submit=None, on_update=None,
                 checkpoint=None, thread_name="tingmu-text"):
        self.keys = tuple(keys)
        self.execute = execute
        self.max_workers = max_workers
        self.result_key = result_key
        self.recoverable = recoverable
        self.make_error = make_error
        self.policy = policy or RepairPolicy()
        self.cancelled = cancelled
        self.check_cancel = check_cancel
        self.stop_event = stop_event if stop_event is not None else Event()
        self.cancelled_error = cancelled_error or (lambda exc: False)
        self.before_submit, self.on_update, self.checkpoint = before_submit, on_update, checkpoint
        self.thread_name = thread_name
        self.pending_errors = dict(feedback or {})
        self.jobs = [BatchJob(i, tuple(indices), {self.keys[n]: self.pending_errors[self.keys[n]]
                     for n in indices if self.keys[n] in self.pending_errors}) for i, indices in enumerate(plans)]
        self.outcomes = {}
        self.completed = {}
        self.errors = []
        self.callback_error = None
        self._checkpoint_failed = False
        self.checkpoint_metadata = {"final_retry_attempted": False, "final_retry_succeeded": False}

    def stopped(self):
        if self.cancelled and self.cancelled():
            self.stop_event.set()
        return self.stop_event.is_set()

    def stop(self, error=None):
        self.stop_event.set()
        if error is not None:
            self.callback_error = self.callback_error or error

    def ordered_results(self):
        return [self.completed[key] for key in self.keys if key in self.completed]

    def _observe_cancel(self):
        if self.check_cancel:
            try:
                self.check_cancel()
            except Exception as exc:
                self.stop(exc)
        self.stopped()

    def _run(self, job):
        try:
            return self._evaluate(job)
        except Exception as exc:
            # Invalid worker envelopes or repair classifiers are terminal too;
            # they must not escape the coordinator's drain/accounting lifecycle.
            self.stop_event.set()
            return BatchOutcome(job, [], {}, [], "failed", exc)

    def _evaluate(self, job):
        try:
            response = self.execute(job, self.stopped)
        except Exception as exc:
            response = BatchResponse(error=exc)
        value, failure = response.value, response.error
        requests = deepcopy(response.requests)
        errors, children = {}, []
        status = "completed"
        partial = isinstance(value, PartialBatchResult)
        completed = list(value.completed if partial else (value or []))
        if partial:
            errors = dict(value.errors)
        if failure is not None and self.recoverable(failure):
            if self.policy.split_invalid and len(job.indices) > 1:
                errors = {self.keys[i]: str(failure) for i in job.indices}
                middle = len(job.indices) // 2
                attempt = 0 if self.policy.reset_attempt_on_split else job.attempt
                children = [(part, {self.keys[i]: errors[self.keys[i]] for i in part}, attempt)
                            for part in (job.indices[:middle], job.indices[middle:])]
                status = "split"
            elif self.policy.retry_invalid_singleton:
                errors = {self.keys[i]: str(failure) for i in job.indices}
                partial, failure = True, None
            elif len(job.indices) == 1:
                if self.policy.split_invalid:
                    errors = {self.keys[job.indices[0]]: str(failure)}
                failure = self.make_error(job.indices[0], str(failure), failure)
        if partial:
            remaining = tuple(i for i in job.indices if self.keys[i] in errors)
            terminal = job.attempt >= self.policy.max_attempts or (
                self.policy.stop_singleton_after_retry and job.attempt and len(job.indices) == 1)
            if remaining and terminal:
                failure = self.make_error(remaining[0], errors[self.keys[remaining[0]]], None)
            elif remaining:
                parts = [remaining]
                if self.policy.split_partial_after_retry and job.attempt and len(remaining) > 1:
                    middle = len(remaining) // 2
                    parts = [remaining[:middle], remaining[middle:]]
                children = [(part, {self.keys[i]: errors[self.keys[i]] for i in part}, job.attempt + 1)
                            for part in parts]
                status = "partial"
        if failure is not None and not children:
            status = "cancelled" if self.cancelled_error(failure) else "failed"
            self.stop_event.set()  # A terminal worker prevents the next submission immediately.
        return BatchOutcome(job, completed, errors, requests, status, failure, children)

    def _publish(self, outcome):
        self.outcomes[outcome.job.index] = outcome
        self.pending_errors.update(outcome.errors)
        for result in outcome.completed:
            try:
                key = self.result_key(result)
                self.completed[key] = result
                self.pending_errors.pop(key, None)
            except Exception as exc:
                self.stop(exc)
        if outcome.failure is not None and not outcome.children and not self.cancelled_error(outcome.failure):
            self.errors.append(outcome.failure)
        if self.on_update:
            try:
                self.on_update(self, outcome)
            except Exception as exc:
                self.stop(exc)

    def _save(self):
        if not self.checkpoint or self._checkpoint_failed:
            return
        try:
            self.checkpoint(deepcopy(self.ordered_results()))
        except Exception as exc:
            self._checkpoint_failed = True
            self.checkpoint_metadata["error_type"] = type(exc).__name__
            self.stop(exc)

    def run(self):
        queued = deque(self.jobs)
        pending = {}
        try:
            with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix=self.thread_name) as pool:
                while pending or (queued and not self.stopped()):
                    self._observe_cancel()
                    while queued and len(pending) < self.max_workers and not self.stopped():
                        job = queued.popleft()
                        if self.before_submit:
                            try:
                                self.before_submit(job)
                            except Exception as exc:
                                self.stop(exc)
                        self._observe_cancel()
                        if self.stopped():
                            break
                        pending[pool.submit(self._run, job)] = job
                    if not pending:
                        break
                    done, _ = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                    changed = False
                    for future in sorted(done, key=lambda item: pending[item].index):
                        job = pending.pop(future)
                        try:
                            outcome = future.result()
                        except Exception as exc:
                            self.stop_event.set()
                            outcome = BatchOutcome(job, [], {}, [], "failed", exc)
                        try:
                            self._publish(outcome)
                        except Exception as exc:
                            self.stop(exc)
                        changed |= bool(outcome.completed or outcome.errors)
                        children = []
                        for indices, feedback, attempt in outcome.children:
                            child = BatchJob(len(self.jobs), indices, feedback, attempt, outcome.job.index)
                            self.jobs.append(child)
                            children.append(child)
                        queued.extendleft(reversed(children))
                    if changed:
                        self._save()
        finally:
            self.stop_event.set()
        if self._checkpoint_failed and self.checkpoint and self.completed:
            self.checkpoint_metadata["final_retry_attempted"] = True
            try:
                self.checkpoint(deepcopy(self.ordered_results()))
                self.checkpoint_metadata["final_retry_succeeded"] = True
            except Exception as exc:
                self.checkpoint_metadata["final_retry_error_type"] = type(exc).__name__
        if self.callback_error is not None:
            raise self.callback_error
        if self.errors:
            raise self.errors[0]
        return self.ordered_results()
