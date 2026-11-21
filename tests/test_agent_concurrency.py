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

    def test_selected_worker_count_applies_to_review_and_teaching(self):
        from tingmu.review import TranscriptReviewer

        for cls in (LearningAgent, TranscriptReviewer):
            with self.subTest(cls=cls.__name__):
                with patch("tingmu.agent._config", return_value=("key", "https://example.invalid", "model")):
                    agent = cls("openai")
                agent.chunk_size = 1
                agent.max_workers = 4
                wave = threading.Barrier(4)

                def batch(worker, cues, language, cursor, end, stopped, context, positions):
                    if cursor < 4:
                        wave.wait(timeout=5)
                    return cues[cursor:end]

                action = agent.review if cls is TranscriptReviewer else agent.annotate
                cues = self.cues(9)
                with patch.object(cls, "_annotate_batch", batch):
                    self.assertEqual(action(cues, LanguagePair("en", "en")), cues)
                self.assertEqual([row["batch_index"] for row in agent.batch_metadata], list(range(9)))

    def test_teaching_splits_invalid_then_truncated_batches_and_checkpoints_leaves(self):
        from tingmu.agent import AnnotationValidationError

        agent = self.agent()
        agent.chunk_size = 6
        agent.max_workers = 2
        agent.split_invalid_batches = True
        cues = self.cues(8)
        checkpoints = []

        def batch(worker, cues, language, start, end, stopped, context, positions):
            if (start, end) == (0, 6):
                raise AnnotationValidationError("invalid tokens")
            if (start, end) == (0, 3):
                raise AgentError("truncated", category="length")
            return cues[start:end]

        with patch.object(LearningAgent, "_annotate_batch", batch):
            result = agent.annotate(cues, LanguagePair("en", "en"),
                                    checkpoint=lambda rows: checkpoints.append([int(c.id) for c in rows]))
        self.assertEqual(result, cues)
        self.assertTrue(all(row == sorted(set(row)) for row in checkpoints))
        self.assertEqual(checkpoints[-1], list(range(8)))
        self.assertEqual([r["status"] for r in agent.batch_metadata].count("split"), 2)
        self.assertEqual([r["status"] for r in agent.batch_metadata].count("completed"), 4)
        self.assertEqual(len([r for r in agent.batch_metadata if "parent_batch_index" in r]), 4)

    def test_teaching_split_rejects_invalid_singleton_and_never_retries_http_errors(self):
        from tingmu.agent import AnnotationValidationError

        for error, expected in ((AnnotationValidationError("bad tokens"), [(0, 3), (0, 1), (1, 3), (1, 2)]),
                                (AgentError("rate limit", status=429), [(0, 3)]),
                                (AgentError("unauthorized", status=401), [(0, 3)])):
            with self.subTest(error=error):
                agent = self.agent()
                agent.chunk_size = 3
                agent.max_workers = 1
                agent.split_invalid_batches = True
                calls = []

                def batch(worker, cues, language, start, end, stopped, context, positions):
                    calls.append((start, end))
                    if start == 0 and end == 1:
                        return cues[start:end]
                    raise error

                with patch.object(LearningAgent, "_annotate_batch", batch), self.assertRaises(AgentError):
                    agent.annotate(self.cues(6), LanguagePair("en", "en"))
                self.assertEqual(calls, expected)
                self.assertEqual([c.id for c in agent.completed_cues], ["0"] if len(expected) > 1 else [])

    def test_partial_rows_checkpoint_before_targeted_requests_and_group_sparse_failures(self):
        agent = self.agent()
        agent.max_workers = 1
        agent.chunk_size = 6
        agent.split_invalid_batches = True
        agent.preserve_text = True
        calls, snapshots = [], []
        cues = self.cues(6)

        def request(worker, path, body):
            payload = json.loads(body["messages"][-1]["content"])
            ids = [row["id"] for row in payload["cues"]]
            calls.append(ids)
            response = api_response(body)
            rows = json.loads(response["choices"][0]["message"]["content"])["cues"]
            if len(calls) == 1:
                rows[1]["translation"] = ""
                rows[3]["tokens"][0]["learn"] = "sensitive original value"
            elif len(calls) == 2:
                self.assertEqual([c.id for c in agent.completed_cues], ["0", "2", "4", "5"])
                self.assertEqual(snapshots[-1], (["0", "2", "4", "5"], {"1", "3"}))
                self.assertIn("translation", payload["required_repairs_in_cue_order"][0])
                self.assertIn("token.learn", payload["required_repairs_in_cue_order"][1])
                self.assertIn("string", payload["required_repairs_in_cue_order"][1])
                self.assertNotIn("sensitive original value", str(payload))
                self.assertEqual(payload["read_only_neighbors_in_cue_order"][1],
                                 {"before": ["word2"], "after": ["word4"]})
                rows[1]["text"] = "altered private content"
            else:
                self.assertEqual(snapshots[-1], (["0", "1", "2", "4", "5"], {"3"}))
                self.assertIn("text", payload["required_repairs_in_cue_order"][0])
                self.assertNotIn("altered private content", str(payload))
            response["choices"][0]["message"]["content"] = json.dumps({"cues": rows})
            return response

        def checkpoint(rows):
            snapshots.append(([c.id for c in rows], set(agent.pending_repair_errors)))

        with patch.object(LearningAgent, "_request", request):
            result = agent.annotate(cues, LanguagePair("en", "en"), checkpoint=checkpoint)
        self.assertEqual(calls, [["0", "1", "2", "3", "4", "5"], ["1", "3"], ["3"]])
        self.assertEqual([c.id for c in result], [c.id for c in cues])
        self.assertEqual([(c.text, c.start, c.end) for c in result], [(c.text, c.start, c.end) for c in cues])
        self.assertEqual(snapshots[-1], (["0", "1", "2", "3", "4", "5"], set()))
        self.assertEqual(agent.batch_metadata[1]["cue_indices"], [1, 3])

    def test_strict_identity_allows_only_ordered_explicit_subsets(self):
        from tingmu.agent import AnnotationValidationError, validate_identified_rows

        cues = self.cues(4)
        checked = []

        def validate(source, row):
            checked.append(row["id"])
            return source

        for ids in (["a", "unknown"], ["a", "a"], ["b", "a"], ["a", None]):
            with self.subTest(ids=ids), self.assertRaises(AnnotationValidationError):
                validate_identified_rows(json.dumps({"cues": [{"id": i} for i in ids]}),
                                         cues, validate, expected_ids=["a", "b", "c", "d"])
            self.assertEqual(checked, [])
        good, errors = validate_identified_rows('{"cues":[{"id":"a"},{"id":"d"}]}', cues, validate,
                                                  expected_ids=["a", "b", "c", "d"])
        self.assertEqual(checked, ["0", "3"])
        self.assertEqual(good, [cues[0], cues[3]])
        self.assertEqual(set(errors), {"1", "2"})

    def test_long_hashes_use_short_ids_on_first_request_and_on_sparse_repairs(self):
        from tingmu.agent import request_ids

        ids = ["a" * 20, "b" * 20, "c" * 20, "d" * 20]
        cues = [Cue(ident, i, i + 1, f"line{i}") for i, ident in enumerate(ids)]
        self.assertEqual(request_ids(cues), ["1", "2", "3", "4"])
        self.assertEqual(request_ids([cues[0], Cue("1", 4, 5, "short")]), ["r1", "r2"])
        self.assertEqual(request_ids(self.cues(3)), ["0", "1", "2"])
        agent = self.agent()
        agent.chunk_size = 6
        agent.max_workers = 1
        agent.split_invalid_batches = True
        calls = []

        def request(worker, path, body):
            content = body["messages"][-1]["content"]
            self.assertFalse(any(ident in content for ident in ids))
            calls.append([r["id"] for r in json.loads(content)["cues"]])
            response = api_response(body)
            rows = json.loads(response["choices"][0]["message"]["content"])["cues"]
            if len(calls) == 1:
                rows = [rows[0], rows[2]]
            response["choices"][0]["message"]["content"] = json.dumps({"cues": rows})
            return response

        with patch.object(LearningAgent, "_request", request):
            result = agent.annotate(cues, LanguagePair("en", "en"))
        self.assertEqual([c.id for c in result], ids)
        self.assertEqual(calls, [["1", "2", "3", "4"], ["1", "2"]])
        self.assertTrue(all(r["identity_alias_initial"] and r["identity_mapping_validated"]
                            and not r["identity_alias_retry"] for r in agent.response_metadata))

    def test_local_token_repairs_need_no_new_model_call_and_keep_only_anchored_glosses(self):
        agent = self.agent()
        agent.chunk_size = 2
        agent.max_workers = 1
        agent.preserve_text = True
        agent.split_invalid_batches = True
        cues = [Cue("a", 0, 1, "one"), Cue("b", 1, 2, "two, please")]
        calls = []

        def request(worker, path, body):
            calls.append(body)
            response = api_response(body)
            rows = json.loads(response["choices"][0]["message"]["content"])["cues"]
            rows[0]["tokens"][0]["meaning"] = "anchored"
            rows[0]["tokens"].append({"text": "", "meaning": "unanchored", "reading": "", "learn": False})
            rows[1]["tokens"] = [{"text": "two", "meaning": "two things", "reading": "", "learn": True}]
            response["choices"][0]["message"]["content"] = json.dumps({"cues": rows})
            return response

        with patch.object(LearningAgent, "_request", request):
            result = agent.annotate(cues, LanguagePair("en", "en"))
        self.assertEqual(len(calls), 1)
        self.assertEqual([t.meaning for t in result[0].tokens], ["anchored"])
        self.assertEqual([t.text for t in result[1].tokens], ["two", ",", "please"])
        self.assertEqual([t.meaning for t in result[1].tokens], ["two things", "", ""])
        self.assertEqual({r["kind"] for r in agent.last_response_metadata["token_repairs"]},
                         {"empty_tokens_removed", "token_reconstruction"})

    def test_empty_token_repair_rejects_invalid_fields_and_reviewed_text_edits(self):
        from tingmu.agent import AnnotationValidationError, _validate_teaching_row

        source = Cue("x", 0, 1, "word")
        token = {"text": "word", "meaning": "", "reading": "", "learn": False}
        base = {"id": "x", "text": "word", "translation": "meaning", "explanation": "usage",
                "tokens": [token], "uncertain": False}
        for extra in ({"text": "", "meaning": [], "reading": "", "learn": False},
                      {"text": "", "meaning": "", "reading": "", "learn": 0},
                      {"text": "", "meaning": "", "reading": "", "learn": False, "extra": None}):
            with self.subTest(extra=extra), self.assertRaises(AnnotationValidationError):
                _validate_teaching_row(source, {**base, "tokens": [token, extra]}, LanguagePair("en", "en"), True, 0)
        with self.assertRaises(AnnotationValidationError):
            _validate_teaching_row(source, {**base, "text": "changed"}, LanguagePair("en", "en"), True, 0)
        with self.assertRaises(AnnotationValidationError):
            _validate_teaching_row(source, {**base, "tokens": [{**token, "text": ""}]},
                                   LanguagePair("en", "en"), True, 0)

    def test_only_unambiguous_json_envelopes_are_accepted(self):
        from tingmu.agent import AnnotationValidationError, validate_identified_rows

        cue = Cue("x", 0, 1, "word")
        row = json.dumps({"id": "x", "field": 1})
        frame = chr(96) * 3

        def validate(source, item):
            if set(item) != {"id", "field"}:
                raise AnnotationValidationError("field missing")
            return source

        for text in (row, f"{frame}json\n{row}\n{frame}", f'{frame}json\n{{"cues":[{row}]}}\n{frame}'):
            self.assertEqual(validate_identified_rows(text, [cue], validate), ([cue], {}))
        for text in ("Here is the result: " + row, row + "some text", "[{}]", '{"id":"other"}'):
            with self.subTest(text=text), self.assertRaises(AnnotationValidationError):
                validate_identified_rows(text, [cue], validate)
        self.assertEqual(validate_identified_rows('{"id":"x"}', [cue], validate), ([], {"x": "field missing"}))

    def test_partial_cancel_and_rate_limit_keep_completed_and_restore_the_error(self):
        agent = self.agent()
        agent.max_workers = 1
        agent.chunk_size = 6
        agent.split_invalid_batches = True
        cues = self.cues(3)
        pause = threading.Event()
        calls = []

        def request(worker, path, body):
            payload = json.loads(body["messages"][-1]["content"])
            calls.append([r["id"] for r in payload["cues"]])
            if len(calls) == 2:
                self.assertIn("缺少", payload["required_repairs_in_cue_order"][0])
                raise AgentError("rate limit", status=429)
            response = api_response(body)
            rows = json.loads(response["choices"][0]["message"]["content"])["cues"]
            response["choices"][0]["message"]["content"] = json.dumps({"cues": [rows[0], rows[2]]})
            return response

        with patch.object(LearningAgent, "_request", request):
            with self.assertRaises(AgentError):
                agent.annotate(cues, LanguagePair("en", "en"), cancelled=pause.is_set,
                               checkpoint=lambda rows: pause.set())
            self.assertEqual([c.id for c in agent.completed_cues], ["0", "2"])
            self.assertEqual(calls, [["0", "1", "2"]])
            agent.repair_feedback = dict(agent.pending_repair_errors)
            with self.assertRaises(AgentError) as failure:
                agent.annotate([cues[1]], LanguagePair("en", "en"), context_cues=cues)
            self.assertEqual(failure.exception.status, 429)
            self.assertEqual(set(agent.pending_repair_errors), {"1"})
        self.assertEqual(calls, [["0", "1", "2"], ["1"]])

    def test_terminal_content_error_reports_true_cue_location_and_bounded_cost(self):
        agent = self.agent()
        agent.max_workers = 1
        agent.chunk_size = 6
        agent.split_invalid_batches = True
        cues = self.cues(10)
        calls = []

        def request(worker, path, body):
            response = api_response(body)
            rows = json.loads(response["choices"][0]["message"]["content"])["cues"]
            calls.append([r["id"] for r in rows])
            for row in rows:
                if row["id"] == "8":
                    row["uncertain"] = "an untrusted value"
            response["choices"][0]["message"]["content"] = json.dumps({"cues": rows})
            return response

        with patch.object(LearningAgent, "_request", request), self.assertRaises(AgentError) as err:
            agent.annotate(cues[7:10], LanguagePair("en", "en"), context_cues=cues)
        self.assertEqual(calls, [["7", "8", "9"], ["8"]])
        self.assertEqual((err.exception.cue_number, err.exception.cue_start), (9, 8))
        self.assertEqual([c.id for c in agent.completed_cues], ["7", "9"])
        self.assertIn("uncertain", str(err.exception))
        self.assertIn("string", str(err.exception))
        self.assertNotIn("an untrusted value", str(err.exception))

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
        agent.max_workers = 17
        with patch.object(LearningAgent, "_request") as request:
            with self.assertRaises(AgentError):
                agent.annotate(self.cues(1), LanguagePair())
            request.assert_not_called()

def test_verification_drains_rate_limited_wave_and_resumes_in_serial(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    from tingmu.models import StudyDocument
    from tingmu.pipeline import _verify_document
    from tingmu.translation_review import TRANSLATION_REVIEW_VERSION

    coordinator = threading.get_ident()
    wave = threading.Barrier(4)
    stop = threading.Event()
    monkeypatch.setattr("tingmu.pipeline.Event", lambda: stop)
    calls = []
    snapshots = []
    cues = [Cue(str(i), i, i + 1, "source", translation="draft", explanation="lesson") for i in range(120)]
    review = {"version": TRANSLATION_REVIEW_VERSION, "verified": {}, "segments": {}, "responses": []}
    document = StudyDocument("test.mp4", LanguagePair("en", "en"), cues, metadata={"translation_review": review})

    class Verifier:
        max_workers = 4

        def verify_translations(self, pending, language, context):
            first = int(pending[0].id)
            calls.append(first)
            self.last_response_metadata["first"] = first
            if self.max_workers == 4:
                wave.wait(timeout=5)
                if first == 24:
                    raise AgentError("rate limit", status=429)
                assert stop.wait(5)
            return [(replace(c, translation="checked"), []) for c in pending]

    def save(doc):
        assert threading.get_ident() == coordinator
        snapshots.append(set(doc.metadata["translation_review"]["verified"]))

    agent = Verifier()
    store = SimpleNamespace(save_document=save)
    with unittest.TestCase().assertRaises(AgentError):
        _verify_document(document, agent, store, lambda _: None, lambda: None)
    assert set(calls) == {0, 24, 48, 72}
    assert snapshots[-1] == {str(i) for i in [*range(24), *range(48, 96)]}
    assert [r["first"] for r in review["responses"]] == [0, 48, 72]
    agent.max_workers = 1
    stop.clear()
    _verify_document(document, agent, store, lambda _: None, lambda: None)
    assert calls[4:] == [24, 96]
    assert all(c.translation == "checked" for c in document.cues)
    assert review["status"] == "ready"


def test_cancel_during_translation_persists_running_calls_only():
    from types import SimpleNamespace

    import pytest

    from tingmu.models import StudyDocument
    from tingmu.pipeline import PreparationCancelled, _verify_document
    from tingmu.translation_review import TRANSLATION_REVIEW_VERSION

    requested = threading.Event()
    observed = threading.Event()
    wave = threading.Barrier(2, action=requested.set)
    calls = []
    cues = [Cue(str(i), i, i + 1, "source", translation="draft", explanation="lesson") for i in range(72)]
    review = {"version": TRANSLATION_REVIEW_VERSION, "verified": {}, "segments": {}, "responses": []}
    doc = StudyDocument("test.mp4", LanguagePair("en", "en"), cues, metadata={"translation_review": review})

    class Verifier:
        max_workers = 2

        def verify_translations(self, pending, language, context):
            calls.append(pending[0].id)
            wave.wait(timeout=5)
            assert observed.wait(5)
            return [(c, []) for c in pending]

    def check_cancel():
        if requested.is_set():
            observed.set()
            raise PreparationCancelled()

    with pytest.raises(PreparationCancelled):
        _verify_document(doc, Verifier(), SimpleNamespace(save_document=lambda _: None), lambda _: None, check_cancel)
    assert set(calls) == {"0", "24"}
    assert set(review["verified"]) == {str(i) for i in range(48)}


if __name__ == "__main__":
    unittest.main()
