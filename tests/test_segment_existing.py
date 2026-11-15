import copy
import importlib.util
import json
from pathlib import Path
import threading

import pytest

from tingmu.agent import AnnotationValidationError
from tingmu.timeline import split_display_text

spec = importlib.util.spec_from_file_location("segment_existing", Path(__file__).parents[1] / "scripts/segment_existing.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def document(count=2):
    return {"media_path": "example.mkv", "future_field": "keep", "metadata": {"review_spec": {"version": "v1"}},
            "cues": [{"id": str(i), "start": i, "end": i + 1, "text": "字" * (30 if i % 2 else 5),
                      "original_text": "raw", "translation": "translation", "explanation": "teaching",
                      "tokens": [{"text": "word", "reading": "reading", "meaning": "meaning", "learn": True}],
                      "unknown": {"future": i}} for i in range(count)]}


def fake_request(rows, context):
    return {r["id"]: split_display_text(r["text"]) for r in rows}, [{"returned_model": "fake-test"}]


def test_only_long_sources_sent_and_every_other_field_preserved(tmp_path):
    source = document(6)
    before = copy.deepcopy(source)
    calls = []

    def request(rows, context):
        calls.append(([r["id"] for r in rows], context))
        return fake_request(rows, context)

    result = module.segment_document(source, request, tmp_path / "cache.json")
    assert calls[0][0] == ["1", "3", "5"]
    assert {r["id"] for r in calls[0][1]} == {"0", "2", "4"}
    assert source == before
    assert result["metadata"]["review_spec"] == before["metadata"]["review_spec"]
    assert result["metadata"]["segment_review_metadata"]["model_source_count"] == 3
    for original, updated in zip(source["cues"], result["cues"]):
        assert "".join(updated["segmented_texts"]) == original["text"]
        assert {k: v for k, v in updated.items() if k != "segmented_texts"} == original
    cached = module.segment_document(source, lambda *a: pytest.fail("Cached long cues requested again"), tmp_path / "cache.json")
    assert [r["segmented_texts"] for r in cached["cues"]] == [r["segmented_texts"] for r in result["cues"]]
    assert module.segment_document(result, lambda *a: pytest.fail("Completed document requested again")) == result


def test_short_only_never_calls_cloud():
    source = document(1)
    result = module.segment_document(source, lambda *a: pytest.fail("Short source sent"))
    assert result["cues"][0]["segmented_texts"] == [source["cues"][0]["text"]]
    assert result["metadata"]["segment_review_metadata"]["responses"] == []


def test_invalid_model_boundaries_retry_once_without_exposing_teacher_annotations():
    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs == {"provider": "openai", "model": "gpt-6-astra"}
            self.response_metadata = []

        def _complete(self, prompt, payload, schema):
            calls.append(payload)
            self.last_response_metadata = {"returned_model": "fake-test"}
            self.response_metadata.append(self.last_response_metadata)
            parts = ["字" * 30] if len(calls) == 1 else ["字" * 15] * 2
            return json.dumps({"cues": [{"id": "1", "segmented_texts": parts}]})

    result, responses = module.request_batch([document()["cues"][1]], [], Client)
    assert len(calls) == 2
    assert result == {"1": ["字" * 15] * 2}
    assert all(set(payload["cues"][0]) == {"id", "text"} for payload in calls)
    assert [r["validation_passed"] for r in responses] == [False, True]


@pytest.mark.parametrize("rows", [[{"id": "1", "segmented_texts": ["字" * 30]}],
                                  [{"id": "1", "segmented_texts": ["字" * 15, "錯" + "字" * 14]}],
                                  [{"id": "other", "segmented_texts": ["字" * 15] * 2}],
                                  [{"id": "1", "segmented_texts": ["字" * 15] * 2, "text": "invented"}]])
def test_boundary_contract_rejects_rewrite_id_change_extra_fields_and_long_line(rows):
    with pytest.raises(AnnotationValidationError):
        module.validate_boundaries(json.dumps({"cues": rows}), [document()["cues"][1]])


def test_batch_limit_and_two_workers():
    source = document(122)
    active = 0
    peak = 0
    sizes = []
    lock = threading.Lock()
    first_pair = threading.Barrier(2)

    def request(rows, context):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            sizes.append(len(rows))
        if len(rows) == 30:
            first_pair.wait(timeout=3)
        result = fake_request(rows, context)
        with lock:
            active -= 1
        return result

    module.segment_document(source, request)
    assert sorted(sizes) == [1, 30, 30]
    assert peak == 2


def test_atomic_backup_and_concurrent_modification_guard(tmp_path):
    path = tmp_path / "documents/doc.json"
    path.parent.mkdir()
    before = b'{"original":"bytes remain exact"}'
    path.write_bytes(before)
    backup = module.commit_document(path, before, {"updated": True}, tmp_path / "backups")
    assert backup.read_bytes() == before
    assert json.loads(path.read_bytes()) == {"updated": True}
    with pytest.raises(RuntimeError):
        module.commit_document(path, before, {}, tmp_path / "second-backups")
    assert not (tmp_path / "second-backups").exists()


def test_protected_hashes_include_database_settings_and_exclude_docs_backups(tmp_path):
    for name in ("study.db", "settings.json", "documents/one.json", "document-import-backups/one.json"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"unchanged")
    before = module.protected_hashes(tmp_path)
    assert set(before) == {"study.db", "settings.json"}
    (tmp_path / "documents/one.json").write_bytes(b"new boundary")
    assert module.protected_hashes(tmp_path) == before
    (tmp_path / "study.db").write_bytes(b"changed")
    assert module.protected_hashes(tmp_path) != before


@pytest.mark.parametrize("parts", [["字"] * 30, ["字" * 30]])
def test_boundary_only_pass_rejects_layout_repair_instead_of_mislabeling_model_provenance(parts):
    source = document()["cues"][1]
    with pytest.raises(AnnotationValidationError, match="automatic layout repairs"):
        module.validate_boundaries(json.dumps({"cues": [{"id": source["id"], "segmented_texts": parts}]}), [source])


def test_completed_document_with_invalid_stored_boundaries_is_not_accepted():
    completed = module.segment_document(document(), fake_request)
    completed["cues"][1]["segmented_texts"] = [completed["cues"][1]["text"]]
    before = copy.deepcopy(completed)
    with pytest.raises(AnnotationValidationError, match="automatic layout repairs"):
        module.segment_document(completed, lambda *args: pytest.fail("A completed but corrupt document must not silently request new model work"))
    assert completed == before


def test_cached_invalid_boundaries_cannot_pass_validation_of_a_repaired_copy(tmp_path):
    source = document()
    cache = tmp_path / "cache.json"
    fingerprint = module.sha(json.dumps(source, ensure_ascii=False, sort_keys=True).encode())
    cache.write_text(json.dumps({"fingerprint": fingerprint, "segments": {"1": [source["cues"][1]["text"]]}, "responses": []}), encoding="utf-8")
    before = cache.read_bytes()
    with pytest.raises(AnnotationValidationError, match="automatic layout repairs"):
        module.segment_document(source, lambda *args: pytest.fail("Invalid cache must be reported"), cache)
    assert cache.read_bytes() == before


def test_boundary_only_short_source_rejects_extra_model_boundaries():
    source = document(1)["cues"][0]
    with pytest.raises(AnnotationValidationError, match="automatic layout repairs"):
        module.validate_boundaries(json.dumps({"cues": [{"id": source["id"], "segmented_texts": list(source["text"])}]}), [source])
