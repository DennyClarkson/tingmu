"""Shared scheduler guarantees are independent of prompts and provider clients."""

from copy import deepcopy
from dataclasses import replace
import threading
from unittest.mock import Mock

import pytest

from tingmu.agent import LearningAgent, _words, batch_worker
from tingmu.batch_scheduler import BatchResponse, ConcurrentBatchScheduler, PartialBatchResult, RepairPolicy
from tingmu.models import Cue, LanguagePair, StudyDocument
from tingmu.providers import ProviderError
from tingmu.translation_review import TRANSLATION_REVIEW_VERSION
from tingmu.translation_verifier import verify_document


def _scheduler(keys, plans, execute, **kwargs):
    return ConcurrentBatchScheduler(
        keys=keys, plans=plans, execute=execute, max_workers=kwargs.pop("max_workers", 1),
        result_key=lambda value: value, recoverable=lambda error: isinstance(error, ValueError),
        make_error=lambda index, message, original: original or ValueError(f"{index}: {message}"), **kwargs)


def test_shared_scheduler_repairs_only_invalid_rows_after_cumulative_checkpoint():
    calls, checkpoints, published = [], [], []

    def execute(job, stopped):
        calls.append(job.indices)
        if job.indices == (0, 1, 2, 3):
            return BatchResponse(PartialBatchResult(["a", "c"], {"b": "bad row", "d": "bad row"}))
        assert checkpoints[-1] == ["a", "c"]
        assert job.feedback == {"b": "bad row", "d": "bad row"}
        return BatchResponse(["b", "d"])

    coordinator = threading.get_ident()

    def publish(scheduler, outcome):
        assert threading.get_ident() == coordinator
        published.append((outcome.status, outcome.job.parent))

    runner = _scheduler("abcd", [(0, 1, 2, 3)], execute,
                        on_update=publish, checkpoint=lambda rows: checkpoints.append(rows))
    assert runner.run() == list("abcd")
    assert calls == [(0, 1, 2, 3), (1, 3)]
    assert checkpoints == [["a", "c"], list("abcd")]
    assert published == [("partial", None), ("completed", 0)]
    assert not runner.pending_errors


def test_shared_repair_policy_bounds_partial_singletons_and_splits_identity_errors():
    calls = []

    def execute(job, stopped):
        calls.append((job.indices, job.attempt))
        if len(job.indices) > 1:
            return BatchResponse(error=ValueError("ambiguous identities"))
        if job.indices == (0,):
            return BatchResponse(["a"])
        return BatchResponse(PartialBatchResult([], {"b": "invalid translation"}))

    runner = _scheduler("ab", [(0, 1)], execute, policy=RepairPolicy())
    with pytest.raises(ValueError, match="invalid translation"):
        runner.run()
    assert calls == [((0, 1), 0), ((0,), 0), ((1,), 0), ((1,), 1)]
    assert runner.ordered_results() == ["a"]
    assert runner.pending_errors == {"b": "invalid translation"}
    assert [runner.outcomes[i].status for i in sorted(runner.outcomes)] == ["split", "completed", "partial", "failed"]


def test_fatal_provider_error_stops_submission_and_drains_success_in_source_order():
    wave = threading.Barrier(2)
    stop = threading.Event()
    calls, checkpoints = [], []

    def execute(job, stopped):
        calls.append(job.index)
        wave.wait(timeout=5)
        if job.index == 0:
            return BatchResponse(error=ProviderError("rate limited", status=429))
        assert stop.wait(5)
        return BatchResponse(["b"], [{"request": "second"}])

    runner = _scheduler("abcd", [(0,), (1,), (2,), (3,)], execute,
                        max_workers=2, stop_event=stop, checkpoint=checkpoints.append)
    with pytest.raises(ProviderError, match="rate limited"):
        runner.run()
    assert sorted(calls) == [0, 1]
    assert runner.ordered_results() == ["b"]
    assert runner.outcomes[1].requests == [{"request": "second"}]
    assert checkpoints == [["b"]]


def test_checkpoint_failure_drains_and_final_retry_keeps_original_failure():
    wave = threading.Barrier(2)
    release = threading.Event()
    writes = []

    def execute(job, stopped):
        wave.wait(timeout=5)
        if job.index == 1:
            assert release.wait(5)
        return BatchResponse(["ab"[job.index]])

    def checkpoint(rows):
        writes.append(rows)
        if len(writes) == 1:
            release.set()
            raise OSError("first disk failure")

    runner = _scheduler("abc", [(0,), (1,), (2,)], execute, max_workers=2, checkpoint=checkpoint)
    with pytest.raises(OSError, match="first disk failure"):
        runner.run()
    assert writes == [["a"], ["a", "b"]]
    assert runner.checkpoint_metadata == {
        "error_type": "OSError", "final_retry_attempted": True, "final_retry_succeeded": True}


@pytest.mark.parametrize("failure", ["envelope", "result_key"])
def test_unexpected_worker_or_accounting_error_still_drains_valid_results(failure):
    wave = threading.Barrier(2)
    stop = threading.Event()
    writes = []

    def execute(job, stopped):
        wave.wait(timeout=5)
        if job.index == 0:
            return object() if failure == "envelope" else BatchResponse([{}])
        assert stop.wait(5)
        return BatchResponse(["b"])

    runner = _scheduler("ab", [(0,), (1,)], execute, max_workers=2,
                        stop_event=stop, checkpoint=writes.append)
    with pytest.raises((AttributeError, TypeError)):
        runner.run()
    assert runner.ordered_results() == ["b"]
    assert writes[-1] == ["b"]
    assert len(runner.outcomes) == 2


def test_worker_fork_isolates_nested_configuration_metadata_and_preserves_explicit_hooks():
    agent = LearningAgent("local")
    agent.provided_translations = {"a": {"text": "original"}}
    agent.reference_hints = {"b": ["reference"]}
    agent.repair_feedback = {"a": "missing"}
    agent._complete = Mock(return_value="synthetic response")
    left, right = batch_worker(agent), batch_worker(agent)
    assert left.client is not right.client and left.client is not agent.client
    left.provided_translations["a"]["text"] = "changed"
    left.reference_hints["b"].append("changed")
    left.repair_feedback["a"] = "changed"
    left.response_metadata.append({"left": True})
    assert right.provided_translations == agent.provided_translations == {"a": {"text": "original"}}
    assert right.reference_hints == agent.reference_hints == {"b": ["reference"]}
    assert right.repair_feedback == agent.repair_feedback == {"a": "missing"}
    assert not right.response_metadata and not agent.response_metadata
    assert left._complete is right._complete is agent._complete


def test_unicode_local_tokens_preserve_non_bmp_and_combining_sequences_without_qt(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert not name.startswith("PySide6"), "local text segmentation must not import Qt"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    source = "Cafe\u0301 l’été 𠮷 👩\u200d💻你好!"
    assert _words(source) == ["Cafe\u0301", "l’été", "𠮷", "👩\u200d💻", "你", "好", "!"]
    assert "".join(_words(source)) == "".join(source.split())


def test_translation_checkpoint_failure_preserves_both_paid_results_and_retries_cumulatively():
    from types import SimpleNamespace

    wave = threading.Barrier(2)
    release = threading.Event()
    calls, snapshots = [], []
    cues = [Cue(str(i), i, i + 1, "source", translation="draft", explanation="lesson") for i in range(72)]
    review = {"version": TRANSLATION_REVIEW_VERSION, "verified": {}, "segments": {}, "responses": []}
    document = StudyDocument("synthetic.mp4", LanguagePair("en", "en"), cues, metadata={"translation_review": review})

    class Verifier:
        max_workers = 2

        def verify_translations(self, pending, language, context):
            calls.append(pending[0].id)
            wave.wait(timeout=5)
            if pending[0].id == "24":
                assert release.wait(5)
            return [(replace(cue, translation="checked"), []) for cue in pending]

    def save(doc):
        snapshots.append(deepcopy(doc.metadata["translation_review"]["verified"]))
        if len(snapshots) == 2:
            release.set()
            raise OSError("translation checkpoint failed")

    with pytest.raises(OSError, match="translation checkpoint failed"):
        verify_document(document, Verifier(), SimpleNamespace(save_document=save), lambda _: None, lambda: None)
    assert sorted(calls) == ["0", "24"]
    assert len(snapshots[-1]) == 48
    assert len(review["verified"]) == 48
    assert [row["cue_start_index"] for row in review["responses"]] == [0, 24]
    assert review["checkpoint_metadata"]["final_retry_attempted"]
    assert review["checkpoint_metadata"]["final_retry_succeeded"]
