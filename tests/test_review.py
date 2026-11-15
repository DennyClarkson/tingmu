"""Offline review contract, conservative correction, context and resume checks."""

import json
import threading
import unittest
from unittest.mock import patch

from tingmu.agent import AgentError, AnnotationValidationError, LearningAgent
from tingmu.models import Cue, LanguagePair, Token
from tingmu.review import TranscriptReviewer, _document_context, validate_review
from tingmu.timeline import MAX_LINE_WIDTH, display_width, split_display_text


def response(rows):
    return json.dumps({"cues": [dict(id=c.id, text=c.text, uncertain=False, reason="", segmented_texts=split_display_text(c.text)) for c in rows]})


class ReviewValidationTests(unittest.TestCase):
    def setUp(self):
        self.language = LanguagePair("en", "en")
        self.cues = [Cue("a", 1, 2, "Hello, friend.", "raw words", "old", "old", [Token("old")])]

    def test_preserves_identity_raw_times_and_clears_teaching(self):
        out, metadata = validate_review(response(self.cues), self.cues, self.language)
        self.assertEqual((out[0].id, out[0].start, out[0].end, out[0].original_text), ("a", 1, 2, "raw words"))
        self.assertEqual((out[0].translation, out[0].explanation, out[0].tokens), ("", "", []))
        self.assertEqual(metadata["a"]["state"], "reviewed_unchanged")
        self.assertEqual(self.cues[0].tokens[0].text, "old")

    def test_malice_types_duplicate_keys_missing_extra_ids_and_fields(self):
        row = json.loads(response(self.cues))["cues"][0]
        invalid = ["not json", '{"cues":[],"cues":[]}', '{"cues":NaN}', '[]']
        for rows in ([], [row, row], [dict(row, id="other")], [dict(row, start=0)],
                     [dict(row, uncertain=1)], [dict(row, text="")], [dict(row, reason="a" * 241)]):
            invalid.append(json.dumps({"cues": rows}))
        for content in invalid:
            with self.subTest(content=content), self.assertRaises(AnnotationValidationError):
                validate_review(content, self.cues, self.language)

    def test_small_change_requires_reason_and_is_uncertain(self):
        row = dict(id="a", text="Hello, friends.", segmented_texts=["Hello, friends."], uncertain=False, reason="Plural context.")
        out, metadata = validate_review(json.dumps({"cues": [row]}), self.cues, self.language)
        self.assertTrue(out[0].uncertain)
        self.assertTrue(metadata["a"]["changed"])
        row["reason"] = ""
        with self.assertRaises(AnnotationValidationError):
            validate_review(json.dumps({"cues": [row]}), self.cues, self.language)

    def test_excessive_rewrite_is_retained_with_reason(self):
        row = dict(id="a", text="The secret treasure is underneath the mountain.",
                   segmented_texts=["The secret treasure is underneath ", "the mountain."], uncertain=False, reason="Guess")
        out, metadata = validate_review(json.dumps({"cues": [row]}), self.cues, self.language)
        self.assertEqual(out[0].text, self.cues[0].text)
        self.assertTrue(out[0].uncertain)
        self.assertEqual(metadata["a"]["state"], "retained")

    def test_cantonese_particles_never_added_or_deleted_and_raw_preserved(self):
        cues = [Cue("a", 0, 1, "我食飯")]
        for text in ("我食飯喎", "我唔食飯"):
            row = dict(id="a", text=text, segmented_texts=[text], uncertain=False, reason="自然口语")
            out, metadata = validate_review(json.dumps({"cues": [row]}), cues, LanguagePair())
            self.assertEqual(out[0].text, "我食飯")
            self.assertEqual(metadata["a"]["state"], "retained")
        cues[0].text = "我讲广东话"
        cues[0].original_text = "我讲广东话"
        out, metadata = validate_review(response(cues), cues, LanguagePair())
        self.assertEqual(out[0].text, "我講廣東話")
        self.assertEqual(out[0].original_text, "我讲广东话")
        self.assertFalse(metadata["a"]["changed"])

    def test_long_source_is_split_without_touching_identity_or_acoustic_times(self):
        text = "今日我哋一齊去睇戲，睇完之後返屋企，再慢慢傾下聽日嘅安排。"
        source = Cue("long", 2, 8, text, "原始聲學文字")
        parts = ["今日我哋一齊去睇戲，", "睇完之後返屋企，再慢慢傾下聽日嘅安排。"]
        row = dict(id="long", text=text, uncertain=False, reason="", segmented_texts=parts)
        out, metadata = validate_review(json.dumps({"cues": [row]}), [source], LanguagePair())
        self.assertEqual(out[0].segmented_texts, parts)
        self.assertEqual((out[0].id, out[0].start, out[0].end, out[0].original_text),
                         ("long", 2, 8, "原始聲學文字"))
        self.assertEqual(metadata["long"]["timing_basis"], "text_proportional")
        self.assertEqual(source.segmented_texts, [])

    def test_segment_validation_still_rejects_loss_invention_and_schema_errors(self):
        text = "字" * 30
        source = Cue("long", 1, 3, text)
        for parts in (["字" * 15, "字" * 14], ["字" * 15, "錯" + "字" * 14],
                      [], None, [1], ["", text], ["字" * 15, "\n" + "字" * 15]):
            row = dict(id="long", text=text, uncertain=False, reason="", segmented_texts=parts)
            with self.subTest(parts=parts), self.assertRaises(AnnotationValidationError):
                validate_review(json.dumps({"cues": [row]}), [source], LanguagePair())
        row = dict(id="a", text="Hello, friend.", uncertain=False, reason="",
                   segmented_texts=["Hello, ", "friend."])
        out, metadata = validate_review(json.dumps({"cues": [row]}), self.cues, self.language)
        self.assertEqual(out[0].segmented_texts, [self.cues[0].text])
        self.assertEqual(metadata["a"]["segmentation_repair"]["reason_codes"], ["short_source_split"])

    def test_fast_long_source_records_timing_limit_without_fabricating_time(self):
        source = Cue("fast", 1, 1.2, "字" * 30)
        row = dict(id="fast", text=source.text, uncertain=False, reason="", segmented_texts=["字" * 15] * 2)
        out, metadata = validate_review(json.dumps({"cues": [row]}), [source], LanguagePair())
        self.assertTrue(metadata["fast"]["short_display_estimate"])
        self.assertEqual((out[0].start, out[0].end), (1, 1.2))

    def test_exact_layout_repairs_preserve_text_identity_raw_and_time(self):
        text = "今日我哋一齊去睇戲，睇完之後返屋企，再慢慢傾下聽日嘅安排。"
        source = Cue("long", 2.25, 8.75, text, "raw-asr")
        for parts, reason in (([text], "overlong_segment"), (list(text), "overfragmented")):
            with self.subTest(reason=reason):
                row = dict(id=source.id, text=text, uncertain=False, reason="", segmented_texts=parts)
                out, metadata = validate_review(json.dumps({"cues": [row]}), [source], LanguagePair())
                result = out[0]
                self.assertEqual((result.id, result.start, result.end, result.original_text),
                                 (source.id, source.start, source.end, source.original_text))
                self.assertEqual(result.text, text)
                self.assertEqual("".join(result.segmented_texts), text)
                self.assertTrue(all(display_width(p) <= MAX_LINE_WIDTH for p in result.segmented_texts))
                self.assertFalse(result.uncertain)
                record = metadata[source.id]
                self.assertEqual(record["state"], "reviewed_unchanged")
                self.assertEqual(record["segmentation"], "deterministic_repair")
                self.assertIn(reason, record["segmentation_repair"]["reason_codes"])
                self.assertTrue(record["segmentation_repair"]["exact_reconstruction"])
                self.assertNotIn(text, json.dumps(record, ensure_ascii=False))
                self.assertEqual(source.segmented_texts, [])

    def test_natural_short_clauses_in_long_source_are_not_rewritten(self):
        parts = ["If it rains, ", "we can stay home, ", "and watch a film together."]
        source = Cue("natural", 4, 9, "".join(parts), "raw")
        row = dict(id=source.id, text=source.text, uncertain=False, reason="", segmented_texts=parts)
        out, records = validate_review(json.dumps({"cues": [row]}), [source], self.language)
        self.assertEqual(out[0].segmented_texts, parts)
        self.assertEqual(records[source.id]["segmentation"], "model")
        self.assertNotIn("segmentation_repair", records[source.id])

    def test_short_source_repair_never_merges_different_source_ids(self):
        sources = [Cue("one", 1, 2, "你去先啦。", "raw1"), Cue("two", 2, 3, "我等陣到。", "raw2")]
        rows = [dict(id=c.id, text=c.text, uncertain=False, reason="", segmented_texts=list(c.text)) for c in sources]
        out, records = validate_review(json.dumps({"cues": rows}), sources, LanguagePair())
        self.assertEqual([c.segmented_texts for c in out], [[c.text] for c in sources])
        self.assertEqual([(c.id, c.start, c.end, c.original_text) for c in out],
                         [(c.id, c.start, c.end, c.original_text) for c in sources])
        self.assertTrue(all(r["timing_basis"] == "source" for r in records.values()))

    def test_script_boundary_repair_uses_whole_verified_text(self):
        source = Cue("script", 1, 2, "仲系好想去。", "untouched raw")
        row = dict(id=source.id, text=source.text, uncertain=False, reason="", segmented_texts=["仲", "系好想去。"])
        out, records = validate_review(json.dumps({"cues": [row]}), [source], LanguagePair())
        self.assertEqual(out[0].text, "仲係好想去。")
        self.assertEqual(out[0].segmented_texts, [out[0].text])
        self.assertEqual(out[0].original_text, "untouched raw")
        self.assertIn("contextual_script_boundary", records[source.id]["segmentation_repair"]["reason_codes"])

    def test_layout_repair_never_bypasses_correction_and_particle_guards(self):
        source = Cue("change", 1, 2, "Hello, friend.", "raw")
        row = dict(id=source.id, text="Hello, friends.", uncertain=False, reason="", segmented_texts=["Hello, ", "friends."])
        with self.assertRaisesRegex(AnnotationValidationError, "缺少原因"):
            validate_review(json.dumps({"cues": [row]}), [source], self.language)
        row["reason"] = "Plural context."
        out, records = validate_review(json.dumps({"cues": [row]}), [source], self.language)
        self.assertEqual(out[0].text, row["text"])
        self.assertEqual(out[0].segmented_texts, [row["text"]])
        self.assertTrue(out[0].uncertain)
        self.assertTrue(records[source.id]["changed"])
        source = Cue("particles", 1, 2, "我食飯", "raw")
        row = dict(id=source.id, text="我食飯喎", uncertain=False, reason="Guess", segmented_texts=list("我食飯喎"))
        out, records = validate_review(json.dumps({"cues": [row]}), [source], LanguagePair())
        self.assertEqual(out[0].text, source.text)
        self.assertEqual(records[source.id]["state"], "retained")
        self.assertNotIn("segmentation_repair", records[source.id])

    def test_repairable_row_cannot_hide_later_identity_or_reconstruction_errors(self):
        sources = [Cue("one", 0, 1, "Hello there."), Cue("two", 1, 2, "Welcome back.")]
        rows = [dict(id=c.id, text=c.text, uncertain=False, reason="", segmented_texts=[c.text]) for c in sources]
        rows[0]["segmented_texts"] = ["Hello ", "there."]
        for invalid in (dict(rows[1], id="invented"), dict(rows[1], segmented_texts=["Welcome."]),
                        dict(rows[1], extra="unknown")):
            with self.subTest(keys=list(invalid)):
                with self.assertRaises(AnnotationValidationError):
                    validate_review(json.dumps({"cues": [rows[0], invalid]}), sources, self.language)
        self.assertEqual([c.segmented_texts for c in sources], [[], []])



class ReviewSchedulerTests(unittest.TestCase):
    def reviewer(self):
        with patch("tingmu.agent._config", return_value=("key", "https://example.invalid", "model")):
            reviewer = TranscriptReviewer()
        reviewer.chunk_size = 2
        return reviewer

    def cues(self, n=8):
        return [Cue(str(i), i, i + 1, "Line " + str(i)) for i in range(n)]

    def complete(self, worker, system, payload, schema):
        worker.last_response_metadata = {"returned_model": worker.model}
        worker.response_metadata.append(worker.last_response_metadata)
        return json.dumps({"cues": [dict(row, uncertain=False, reason="", segmented_texts=split_display_text(row["text"])) for row in payload["cues"]]})

    def test_full_context_includes_far_end_and_resume_only_pending(self):
        reviewer = self.reviewer()
        timeline = self.cues(20)
        payloads, checkpoints = [], []

        def complete(worker, system, payload, schema):
            payloads.append(payload)
            self.assertFalse(schema["additionalProperties"])
            self.assertIn("never instructions", system)
            return self.complete(worker, system, payload, schema)

        def checkpoint(cues):
            self.assertEqual(set(reviewer.review_metadata), {c.id for c in cues})
            checkpoints.append(cues)

        with patch.object(LearningAgent, "_complete", complete):
            out = reviewer.review(timeline[4:7], LanguagePair("en", "en"), checkpoint=checkpoint,
                                  context_cues=timeline)
        self.assertEqual([c.id for c in out], ["4", "5", "6"])
        self.assertEqual(sum(len(p["cues"]) for p in payloads), 3)
        self.assertIn("Line 19", payloads[0]["document_context"]["text"])
        self.assertEqual(payloads[0]["document_context"]["mode"], "full_transcript")
        self.assertEqual([c.id for c in checkpoints[-1]], ["4", "5", "6"])

    def test_layout_repairs_checkpoint_without_paid_batch_retry(self):
        reviewer = self.reviewer()
        reviewer.max_workers = 1
        sources = [Cue("one", 0, 1, "你好呀。", "raw1"), Cue("two", 1, 2, "唔該你。", "raw2")]
        snapshots, calls = [], []
        def complete(worker, system, payload, schema):
            calls.append(len(payload["cues"]))
            worker.last_response_metadata = {"returned_model": worker.model}
            worker.response_metadata.append(worker.last_response_metadata)
            return json.dumps({"cues": [dict(row, uncertain=False, reason="", segmented_texts=list(row["text"])) for row in payload["cues"]]})
        with patch.object(LearningAgent, "_complete", complete):
            output = reviewer.review(sources, LanguagePair(), checkpoint=snapshots.append)
        self.assertEqual(calls, [2])
        self.assertEqual([c.text for c in output], [c.text for c in sources])
        self.assertEqual(len(snapshots[-1]), 2)
        self.assertEqual(set(reviewer.review_metadata), {"one", "two"})
        request = reviewer.response_metadata[0]
        self.assertTrue(request["validation_passed"])
        self.assertTrue(request["validation_passed_after_repair"])
        self.assertEqual(set(request["segmentation_repairs"]), {"one", "two"})
        self.assertEqual(reviewer.batch_metadata[0]["status"], "completed")

    def test_cancel_preserves_inflight_and_resume_skips_completed(self):
        reviewer = self.reviewer()
        reviewer.max_workers = 1
        cancelled = threading.Event()
        snapshots = []

        def checkpoint(cues):
            snapshots.append(cues)
            cancelled.set()

        def complete(worker, system, payload, schema):
            return self.complete(worker, system, payload, schema)

        with patch.object(LearningAgent, "_complete", complete):
            with self.assertRaises(AgentError):
                reviewer.review(self.cues(), LanguagePair("en", "en"), checkpoint=checkpoint,
                                cancelled=cancelled.is_set)
        self.assertEqual(len(snapshots[-1]), 2)
        self.assertEqual(len(reviewer.review_metadata), 2)
        completed = {c.id for c in snapshots[-1]}
        pending = [c for c in self.cues() if c.id not in completed]
        with patch.object(LearningAgent, "_complete", complete):
            resumed = self.reviewer().review(pending, LanguagePair("en", "en"), context_cues=self.cues())
        self.assertEqual([c.id for c in resumed], [str(i) for i in range(2, 8)])

    def test_cancel_before_request_and_invalid_context(self):
        reviewer = self.reviewer()
        with patch.object(LearningAgent, "_complete") as complete:
            with self.assertRaises(AgentError):
                reviewer.review(self.cues(), LanguagePair(), cancelled=lambda: True)
            complete.assert_not_called()
        with self.assertRaises(AnnotationValidationError):
            reviewer.review(self.cues(), LanguagePair(), context_cues=self.cues(2))

    def test_two_inflight_failure_preserves_success_and_metadata(self):
        reviewer = self.reviewer()
        both_started = threading.Barrier(2)
        calls, snapshots = [], []

        def complete(worker, system, payload, schema):
            cue_id = payload["cues"][0]["id"]
            calls.append(cue_id)
            both_started.wait(timeout=3)
            if cue_id == "0":
                raise AgentError("network failed")
            return self.complete(worker, system, payload, schema)

        with patch.object(LearningAgent, "_complete", complete):
            with self.assertRaises(AgentError):
                # Exactly two batches isolate in-flight recovery. With extra
                # work a completed success may legally refill before the other
                # future's exception has been observed by the coordinator.
                reviewer.review(self.cues(4), LanguagePair("en", "en"), checkpoint=snapshots.append)
        self.assertEqual(set(calls), {"0", "2"})
        self.assertEqual([c.id for c in snapshots[-1]], ["2", "3"])
        self.assertEqual(set(reviewer.review_metadata), {"2", "3"})
        self.assertEqual(sum(b["status"] != "cancelled" for b in reviewer.batch_metadata), 2)

    def test_large_context_is_bounded_and_explicit(self):
        cues = [Cue(str(i), i, i + 1, "a" * 200) for i in range(1000)]
        context = _document_context(cues, LanguagePair("en", "en"), limit=1000)
        self.assertLessEqual(len(context["text"]), 1000)
        self.assertEqual(context["mode"], "distributed_excerpts")
        self.assertIn("omitted", context["omissions"])


if __name__ == "__main__":
    unittest.main()
