"""Exercise the preparation queue, stage bindings and studio reader over loopback HTTP."""

import json
from time import monotonic, sleep

import pytest

from test_preparation_streams import origin as origin, structured_answer
from tingmu.batch import PreparationQueue
from tingmu.diagnostics import AppLog
from tingmu.storage import Store
from tingmu.translation_review import translation_verified


@pytest.mark.parametrize("mode,first_stage", [("local", "review"), ("llm_regroup", "regroup")])
def test_studio_reads_live_pipeline_before_completion_and_keeps_rejected_text_ephemeral(
    tmp_path, origin, mode, first_stage, record_property
):
    media = tmp_path / "Sample.mkv"
    media.touch()
    source = "Studio sample dialogue."
    media.with_suffix(".en.srt").write_text(f"1\n00:00:00,000 --> 00:00:02,000\n{source}\n", encoding="utf-8")
    store = Store(tmp_path / "workspace")
    registry = store.model_registry
    profile = registry.save_profile({**origin.profile, "id": "loopback", "label": "Synthetic loopback"})
    registry.assign(["annotation", "review"], profile["id"])
    settings = store.load_settings()
    settings.target_language = "en"
    settings.source_language = "zh-CN"
    settings.segmentation_mode = mode
    settings.text_models_enabled = True
    settings.cloud_text_enabled = False
    store.save_settings(settings)
    queue = PreparationQueue(store, stream_preview=True)
    job_id = queue.enqueue([media], settings)[0]["id"]
    log = AppLog(store.data_dir)
    private = "NEVER_SAVE_REASONING_FROM_SSE"
    rejected = "UNVALIDATED_STREAM_ONLY"
    first_verification = True
    samples, seen_stages, errors = [], [], []

    def wait_for_tail(stage_name, needle):
        deadline = monotonic() + 2
        while monotonic() < deadline:
            for lane in queue.streams.snapshot(job_id, stage_name).values():
                if needle in lane["stream_tail"]:
                    return lane
            sleep(0.005)
        return {}

    def respond(handler, body):
        nonlocal first_verification
        payload = json.loads(body["messages"][-1]["content"])
        stage = ("regroup" if "units" in payload else "verification" if "known_language" in payload
                 else "review" if "document_context" in payload else "teaching")
        seen_stages.append(stage)
        stage_name = queue.snapshot()[0]["stage_name"]
        # Leave an incomplete JSON document for the parser while the provider
        # waits. One verification stream deliberately fails final validation.
        answer = json.dumps(structured_answer(stage, payload))
        needle = source
        if stage == "verification" and first_verification:
            first_verification = False
            answer = '{"cues":[{"id":"1","translation":"' + rejected
            needle = rejected
        pos = answer.index(needle)
        mid, end = pos + len(needle) // 2, pos + len(needle)
        handler.begin_stream()
        handler.event({"choices": [{"delta": {"reasoning_content": private}}]})
        handler.event({"choices": [{"delta": {"content": answer[:mid]}}]})
        first = wait_for_tail(stage_name, answer[pos:mid])
        handler.event({"choices": [{"delta": {"content": answer[mid:end]}}]})
        second = wait_for_tail(stage_name, needle)
        samples.append((stage, stage_name, first, second))
        handler.event({"choices": [{"delta": {"content": answer[end:]}, "finish_reason": "stop"}]})
        handler.event("[DONE]")

    origin.answer = respond
    try:
        jobs = queue.run(log_event=log.event, on_error=errors.append)
    finally:
        log.close()
    assert not errors, errors
    assert jobs[0]["status"] == "completed"
    assert seen_stages == [first_stage, "teaching", "verification", "verification"]
    assert all(body.get("stream") is True for body in origin.requests)
    counts = []
    for stage, stage_name, first, second in samples:
        assert first and second, (stage, stage_name, first, second)
        assert first["stream_epoch"] == second["stream_epoch"]
        assert 0 < first["stream_seq"] < second["stream_seq"]
        assert not any(value in second["stream_tail"] for value in (private, '"cues"', '"groups"', '"id"'))
        assert queue.streams.snapshot(job_id, stage_name) == {}
        counts.append((stage, first["stream_seq"], second["stream_seq"]))
    doc = store.load_document(str(media), settings.language)
    assert len(doc.cues) == 1
    assert (doc.cues[0].text, doc.cues[0].translation) == (source, "Checked " + source)
    assert translation_verified(doc.cues[0], doc.metadata["translation_review"])
    assert log.path.stat().st_size > 0
    files = [path for path in store.data_dir.rglob("*") if path.is_file()]
    for path in files:
        data = path.read_bytes()
        assert private.encode() not in data and rejected.encode() not in data, path
    assert source.encode() not in queue.path.read_bytes() and source.encode() not in log.path.read_bytes()
    record_property("live_sequences", json.dumps(counts))
    record_property("checked_files", len(files))
