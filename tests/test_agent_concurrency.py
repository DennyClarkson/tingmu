"""Offline concurrency checks with synchronized fake HTTP calls; no provider traffic."""

import json
import threading
import unittest
from unittest.mock import patch

from tingmu.agent import AgentError, LearningAgent
from tingmu.models import Cue, LanguagePair


def api_response(body):
    payload = json.loads(body["messages"][-1]["content"])
    rows = [
        {
            "id": c["id"],
            "text": c["text"],
            "translation": "meaning",
            "explanation": "usage",
            "uncertain": False,
            "tokens": [{"text": c["text"], "reading": "", "meaning": "", "learn": False}],
        }
        for c in payload["cues"]
    ]
    return {
        "model": "actual-" + rows[0]["id"],
        "usage": {"total_tokens": 10},
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"cues": rows})}}],
    }


class ConcurrentAnnotationTests(unittest.TestCase):
    def agent(self):
        with patch("tingmu.agent._config", return_value=("key", "https://example.invalid", "model")):
            agent = LearningAgent("openai")
        agent.chunk_size = 1
        return agent

    def cues(self, n=6):
        return [Cue(str(i), i, i + 1, "word" + str(i)) for i in range(n)]

    def test_two_inflight_out_of_order_is_sorted_and_callbacks_are_coordinator_only(self):
        agent = self.agent()
        cues = self.cues()
        coordinator = threading.get_ident()
        release_first = threading.Event()
        second_started = threading.Event()
        lock = threading.Lock()
        active = 0
        peak = 0
        worker_ids = set()
        payloads = {}
        snapshots = []
        callback_threads = []

        def request(worker, path, body):
            nonlocal active, peak
            payload = json.loads(body["messages"][-1]["content"])
            index = int(payload["cues"][0]["id"])
            with lock:
                active += 1
                peak = max(peak, active)
                worker_ids.add(id(worker))
                payloads[index] = payload
            try:
                if index == 0:
                    self.assertTrue(second_started.wait(3))
                    self.assertTrue(release_first.wait(3))
                elif index == 1:
                    second_started.set()
                return api_response(body)
            finally:
                with lock:
                    active -= 1

        def checkpoint(rows):
            callback_threads.append(threading.get_ident())
            snapshots.append([int(c.id) for c in rows])
            if snapshots[-1] == [1]:
                release_first.set()

        with patch.object(LearningAgent, "_request", request):
            out = agent.annotate(
                cues,
                LanguagePair("en", "en"),
                checkpoint=checkpoint,
                progress=lambda _: callback_threads.append(threading.get_ident()),
            )
        self.assertEqual([c.id for c in out], [c.id for c in cues])
        self.assertEqual(peak, 2)
        self.assertGreater(len(worker_ids), 1)
        self.assertEqual(snapshots[0], [1])
        self.assertEqual(snapshots[-1], list(range(6)))
        self.assertTrue(all(row == sorted(row) for row in snapshots))
        self.assertEqual(set(callback_threads), {coordinator})
        for index, payload in payloads.items():
            self.assertEqual(payload["context_before"], [c.text for c in cues[max(0, index - 2) : index]])
            self.assertEqual(payload["context_after"], [c.text for c in cues[index + 1 : index + 3]])
        self.assertEqual(len(agent.response_metadata), 6)
        for record in agent.response_metadata:
            self.assertEqual(record["returned_model"], "actual-" + str(record["batch_index"]))
            self.assertTrue(record["validation_passed"])

    def test_failure_stops_new_batches_but_preserves_other_inflight_success(self):
        agent = self.agent()
        first_started = threading.Event()
        failed = threading.Event()
        calls = []
        snapshots = []

        def request(worker, path, body):
            index = int(json.loads(body["messages"][-1]["content"])["cues"][0]["id"])
            calls.append(index)
            if index == 1:
                first_started.set()
                self.assertTrue(failed.wait(3))
                return api_response(body)
            self.assertEqual(index, 0)
            self.assertTrue(first_started.wait(3))
            failed.set()
            raise AgentError("provider unavailable")

        with patch.object(LearningAgent, "_request", request):
            with self.assertRaisesRegex(AgentError, "provider unavailable"):
                agent.annotate(
                    self.cues(),
                    LanguagePair("en", "en"),
                    checkpoint=lambda rows: snapshots.append([c.id for c in rows]),
                )
        self.assertEqual(sorted(calls), [0, 1])
        self.assertEqual(snapshots, [["1"]])
        self.assertEqual([b["status"] for b in agent.batch_metadata], ["failed", "completed"])
        self.assertEqual(agent.batch_metadata[0]["error"], "provider unavailable")
        self.assertEqual(agent.response_metadata[0]["returned_model"], "actual-1")

    def test_cancellation_drains_both_started_requests_without_scheduling_more(self):
        agent = self.agent()
        started = threading.Barrier(2)
        cancelled = threading.Event()
        calls = []
        snapshots = []

        def request(worker, path, body):
            index = int(json.loads(body["messages"][-1]["content"])["cues"][0]["id"])
            calls.append(index)
            started.wait(timeout=3)
            cancelled.set()
            return api_response(body)

        with patch.object(LearningAgent, "_request", request):
            with self.assertRaisesRegex(AgentError, "取消"):
                agent.annotate(
                    self.cues(),
                    LanguagePair("en", "en"),
                    cancelled=cancelled.is_set,
                    checkpoint=lambda rows: snapshots.append([c.id for c in rows]),
                )
        self.assertEqual(sorted(calls), [0, 1])
        self.assertEqual(snapshots[-1], ["0", "1"])
        self.assertEqual(len(agent.response_metadata), 2)
        self.assertTrue(all(b["status"] == "completed" for b in agent.batch_metadata))

    def test_transient_checkpoint_failure_drains_and_retries_full_cumulative_result(self):
        agent = self.agent()
        both_started = threading.Barrier(2)
        release_second = threading.Event()
        calls, writes = [], []

        def request(worker, path, body):
            index = json.loads(body["messages"][-1]["content"])["cues"][0]["id"]
            calls.append(index)
            both_started.wait(timeout=3)
            if index == "1":
                self.assertTrue(release_second.wait(3))
            return api_response(body)

        def checkpoint(rows):
            writes.append([cue.id for cue in rows])
            if len(writes) == 1:
                release_second.set()
                raise OSError("temporary disk write failure")

        with patch.object(LearningAgent, "_request", request):
            with self.assertRaisesRegex(OSError, "temporary disk"):
                agent.annotate(self.cues(5), LanguagePair("en", "en"), checkpoint=checkpoint)
        self.assertEqual(sorted(calls), ["0", "1"])
        self.assertEqual(writes, [["0"], ["0", "1"]])
        self.assertEqual([cue.id for cue in agent.completed_cues], ["0", "1"])
        self.assertEqual(len(agent.response_metadata), 2)
        self.assertEqual(len(agent.batch_metadata), 2)
        self.assertTrue(agent.checkpoint_metadata["final_retry_succeeded"])

    def test_cancellation_prevents_retry_after_invalid_response(self):
        agent = self.agent()
        agent.max_workers = 1
        cancelled = threading.Event()
        calls = 0

        def request(worker, path, body):
            nonlocal calls
            calls += 1
            cancelled.set()
            result = api_response(body)
            result["choices"][0]["message"]["content"] = "bad"
            return result

        with patch.object(LearningAgent, "_request", request):
            with self.assertRaisesRegex(AgentError, "取消"):
                agent.annotate(self.cues(), LanguagePair("en", "en"), cancelled=cancelled.is_set)
        self.assertEqual(calls, 1)
        self.assertFalse(agent.response_metadata[0]["validation_passed"])
        self.assertEqual(agent.batch_metadata[0]["status"], "cancelled")

    def test_resumed_batches_use_complete_timeline_raw_neighbors(self):
        agent = self.agent()
        agent.chunk_size = 6  # a default-sized batch must split at completed gaps
        full = self.cues(8)
        for cue in full:
            cue.text = "edited-" + cue.id
        pending = [full[2], full[5]]
        payloads = {}

        def request(worker, path, body):
            payload = json.loads(body["messages"][-1]["content"])
            payloads[payload["cues"][0]["id"]] = payload
            return api_response(body)

        with patch.object(LearningAgent, "_request", request):
            out = agent.annotate(pending, LanguagePair("en", "en"), context_cues=full)
        self.assertEqual([cue.id for cue in out], ["2", "5"])
        self.assertEqual(payloads["2"]["context_before"], ["word0", "word1"])
        self.assertEqual(payloads["2"]["context_after"], ["word3", "word4"])
        self.assertEqual(payloads["5"]["context_before"], ["word3", "word4"])
        self.assertEqual(payloads["5"]["context_after"], ["word6", "word7"])
        self.assertEqual(payloads["2"]["cues"], [{"id": "2", "text": "edited-2"}])

    def test_invalid_full_timeline_context_rejects_before_requests(self):
        agent = self.agent()
        full = self.cues(3)
        for pending, context in (
            ([full[0]], [full[1]]),
            ([full[0]], [full[0], full[0]]),
            ([full[2], full[0]], full),
        ):
            with patch.object(LearningAgent, "_request") as request:
                with self.assertRaises(AgentError):
                    agent.annotate(pending, LanguagePair(), context_cues=context)
                request.assert_not_called()

    def test_single_worker_diagnostic_mode_and_invalid_concurrency(self):
        agent = self.agent()
        agent.max_workers = 1
        calls = []

        def request(worker, path, body):
            calls.append(json.loads(body["messages"][-1]["content"])["cues"][0]["id"])
            return api_response(body)

        with patch.object(LearningAgent, "_request", request):
            result = agent.annotate(self.cues(3), LanguagePair("en", "en"))
        self.assertEqual(calls, ["0", "1", "2"])
        self.assertEqual(len(result), 3)
        agent.max_workers = 3
        with patch.object(LearningAgent, "_request") as request:
            with self.assertRaises(AgentError):
                agent.annotate(self.cues(1), LanguagePair())
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
