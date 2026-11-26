"""Structured preparation through a real loopback SSE service."""

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from unittest.mock import Mock

import pytest

from tingmu.agent import LearningAgent
from tingmu.llm_client import LlmClient
from tingmu.models import Cue, LanguagePair, StudyDocument
from tingmu.providers import ProviderError, RequestCancelled
from tingmu.regroup import regroup
from tingmu.review import TranscriptReviewer
from tingmu.translation_review import TRANSLATION_REVIEW_VERSION
from tingmu.translation_verifier import verify_document


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.requests.append(body)
        self.server.answer(self, body)

    def begin_stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()

    def event(self, event):
        data = json.dumps(event) if isinstance(event, dict) else event
        self.wfile.write(f"data: {data}\n\n".encode())
        self.wfile.flush()

    def json_reply(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())


@pytest.fixture
def origin():
    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.requests = []
        server.profile = dict(provider="openai", protocol="openai", base_url=f"http://127.0.0.1:{server.server_port}",
                              model="loopback-model", key_source="none")
        thread = threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(2)


def structured_answer(stage, payload):
    if stage == "regroup":
        return {"groups": [dict(start_index=row["index"], end_index=row["index"], start_char=None, end_char=None,
                                text=row["text"], reason="", uncertain=False) for row in payload["units"]]}
    rows = []
    for cue in payload["cues"]:
        ident, text = cue["id"], cue["text"]
        if stage == "verification":
            row = dict(id=ident, translation=f"Checked {text}", segments=[])
        elif stage == "review":
            row = dict(id=ident, text=text, uncertain=False, reason="", segmented_texts=[text])
        else:
            row = dict(id=ident, text=text, translation=f"Draft {text}", explanation="Synthetic lesson.",
                       uncertain=False, tokens=[dict(text=text, reading="", meaning="line", learn=False)])
        rows.append(row)
    return {"cues": rows}


@pytest.mark.parametrize("stage", ["regroup", "review", "teaching", "verification"])
def test_stage_streams_concurrent_batches_to_their_own_lane_before_completion(origin, stage):
    deltas = defaultdict(list)
    both_visible = threading.Event()
    lock = threading.Lock()
    coordinator = threading.get_ident()
    callbacks = set()
    first_id = 1 if stage == "verification" else 0

    def observe(batch_id, content):
        with lock:
            callbacks.add(threading.get_ident())
            deltas[batch_id].append(content)
            if len(deltas) == 2:
                both_visible.set()

    def respond(handler, body):
        payload = json.loads(body["messages"][-1]["content"])
        content = json.dumps(structured_answer(stage, payload))
        handler.begin_stream()
        handler.event({"choices": [{"delta": {"reasoning_content": "PRIVATE_REASONING"}}]})
        cut = len(content) // 2
        handler.event({"choices": [{"delta": {"content": content[:cut]}}]})
        if both_visible.wait(5):
            handler.event({"choices": [{"delta": {"content": content[cut:]}, "finish_reason": "stop"}]})
            handler.event("[DONE]")

    origin.answer = respond
    language = LanguagePair("en", "en")
    cue_count = 25 if stage == "verification" else 2
    cues = [Cue(f"c{i}", i * 100, i * 100 + 2, f"Line {i}.", translation="Draft.", explanation="Lesson.")
            for i in range(cue_count)]
    progress = []
    if stage == "regroup":
        units = [dict(text=cue.text, source_id=cue.id, start=cue.start, end=cue.end) for cue in cues]
        output, audit, _ = regroup(units, language, provider="openai", model="loopback-model", profile=origin.profile,
                                   progress=progress.append, on_delta=observe)
        assert audit["fallback_chunk_count"] == 0
    elif stage == "verification":
        review = dict(version=TRANSLATION_REVIEW_VERSION, verified={}, responses=[], segments={})
        document = StudyDocument("synthetic.mkv", language, cues, metadata={"translation_review": review})
        verify_document(document, LearningAgent(profile=origin.profile), Mock(), progress.append, lambda: None,
                        on_delta=observe)
        assert review["status"] == "ready"
        output = document.cues
    else:
        worker = TranscriptReviewer(profile=origin.profile) if stage == "review" else LearningAgent(profile=origin.profile)
        worker.chunk_size = 1
        process = worker.review if stage == "review" else worker.annotate
        output = process(cues, language, progress=progress.append, on_delta=observe)
        assert "PRIVATE_REASONING" not in repr(worker.response_metadata)
    assert [cue.text for cue in output] == [cue.text for cue in cues]
    assert len(origin.requests) == 2
    assert all(body["stream"] is True for body in origin.requests)
    assert coordinator not in callbacks and len(callbacks) == 2
    batches = [event.details["batch"] for event in progress if "batch" in getattr(event, "details", {})]
    assert {row["id"] for row in batches if row["status"] == "running"} == set(deltas)
    for batch_id, first_cue in ((first_id, cues[0]), (first_id + 1, cues[-1])):
        first, second = deltas[batch_id]
        reply = json.loads(first + second)
        row = reply["groups" if stage == "regroup" else "cues"][0]
        assert (row.get("text") or row["translation"]) in (first_cue.text, "Checked " + first_cue.text)


@pytest.mark.parametrize("status", [400, 422])
def test_stream_format_rejection_retries_same_schema_then_existing_format_fallback(origin, status):
    def respond(handler, body):
        if "response_format" in body:
            handler.json_reply(status, {"error": {"message": "Unsupported response_format."}})
        else:
            handler.json_reply(200, {"choices": [{"finish_reason": "stop", "message": {"content": '{"ok":true}'}}]})

    origin.answer = respond
    client = LlmClient(profile=origin.profile)
    deltas = []
    assert client.complete("fixture", {}, {"type": "object"}, on_delta=deltas.append) == '{"ok":true}'
    assert [(r.get("stream", False), r.get("response_format", {}).get("type")) for r in origin.requests] == [
        (True, "json_schema"), (False, "json_schema"), (False, "json_object"), (False, None)]
    assert not deltas
    assert len(client.response_metadata) == 1
    assert client.last_response_metadata["response_format"] == "prompt"


@pytest.mark.parametrize("blocking_fallback", [False, True])
def test_cancelling_interrupts_a_blocked_structured_request(origin, blocking_fallback):
    ready, release, cancelled = threading.Event(), threading.Event(), threading.Event()

    def respond(handler, body):
        if blocking_fallback and body.get("stream"):
            return handler.json_reply(422, {"error": {"message": "Streaming unavailable with json_schema."}})
        if body.get("stream"):
            handler.begin_stream()
            handler.event({"choices": [{"delta": {"content": '{"cues": ['}}]})
        ready.set()
        release.wait(5)

    origin.answer = respond
    client = LlmClient(profile=origin.profile)
    with ThreadPoolExecutor() as pool:
        response = pool.submit(client.complete, "fixture", {}, {"type": "object"},
                               on_delta=lambda _: None, cancelled=cancelled.is_set)
        try:
            assert ready.wait(4)
            cancelled.set()
            with pytest.raises(RequestCancelled):
                response.result(2)
        finally:
            release.set()
    assert not client.response_metadata


def test_reasoning_parameter_error_never_retries_the_stream(origin):
    origin.answer = lambda handler, body: handler.json_reply(400, {"error": {"message": "Invalid reasoning_effort"}})
    with pytest.raises(ProviderError) as error:
        LlmClient(profile=origin.profile).complete("fixture", {}, {"type": "object"}, on_delta=lambda _: None)
    assert error.value.category == "reasoning_parameter"
    assert len(origin.requests) == 1


def test_endpoint_returning_complete_json_to_stream_is_validated_without_replay_or_simulated_deltas(origin):
    def respond(handler, body):
        handler.json_reply(200, {"choices": [{"finish_reason": "stop",
                          "message": {"content": '{"ok":true}', "reasoning_content": "PRIVATE_REASONING"}}]})

    origin.answer = respond
    deltas = []
    client = LlmClient(profile=origin.profile)
    assert client.complete("fixture", {}, {"type": "object"}, on_delta=deltas.append) == '{"ok":true}'
    assert len(origin.requests) == 1 and origin.requests[0]["stream"] is True
    assert not deltas and "PRIVATE_REASONING" not in repr(client.response_metadata)


def test_regroup_starts_a_new_preview_for_a_same_batch_repair(origin):
    def respond(handler, body):
        payload = json.loads(body["messages"][-1]["content"])
        content = json.dumps(structured_answer("regroup", payload)) if "retry_feedback" in payload else '{"groups":[]}'
        handler.begin_stream()
        handler.event({"choices": [{"delta": {"content": content}, "finish_reason": "stop"}]})
        handler.event("[DONE]")

    origin.answer = respond
    preview = []
    units = [dict(text="Line.", source_id="c0", start=0, end=2)]
    cues, audit, _ = regroup(units, LanguagePair("en", "en"), provider="openai", model="loopback-model",
                             profile=origin.profile, on_delta=lambda ident, value: preview.append((ident, value)))
    assert len(origin.requests) == 2 and audit["fallback_chunk_count"] == 0
    assert preview[:2] == [(0, '{"groups":[]}'), (0, None)]
    assert json.loads(preview[2][1])["groups"][0]["text"] == cues[0].text


def test_review_keeps_already_issued_stream_after_another_batch_fails(origin):
    started, failed = threading.Event(), threading.Event()
    cues = [Cue(f"c{i}", 2*i, 2*i+1, f"Line {i}.") for i in range(3)]
    reviewer = TranscriptReviewer(profile=origin.profile)
    reviewer.chunk_size = 1

    def respond(handler, body):
        payload = json.loads(body["messages"][-1]["content"])
        if payload["cues"][0]["id"] == "c0":
            started.wait(4)
            return handler.json_reply(429, {"error": {"message": "rate_limited"}})
        content = json.dumps(structured_answer("review", payload))
        handler.begin_stream()
        handler.event({"choices": [{"delta": {"content": content[:8]}}]})
        started.set()
        if failed.wait(4):
            handler.event({"choices": [{"delta": {"content": content[8:]}, "finish_reason": "stop"}]})
            handler.event("[DONE]")

    def progress(event):
        if getattr(event, "details", {}).get("batch", {}).get("status") == "failed":
            failed.set()

    origin.answer = respond
    with pytest.raises(ProviderError) as failure:
        reviewer.review(cues, LanguagePair("en", "en"), progress=progress, on_delta=lambda *_: None,
                        cancelled=lambda: False)
    assert failure.value.status == 429 and len(origin.requests) == 2
    assert [cue.id for cue in reviewer.completed_cues] == ["c1"]


def test_preview_does_not_bypass_explicit_nonstream_request(monkeypatch):
    monkeypatch.setattr("tingmu.providers._open", lambda *_: pytest.fail("unexpected HTTP request"))
    client = LlmClient(profile=dict(provider="openai", model="fixture", base_url="http://127.0.0.1", key_source="none"))
    deltas = []
    request = Mock(return_value={"choices": [{"finish_reason": "stop", "message": {"content": "fixture reply"}}]})
    assert client.complete("fixture", {}, {"type": "object"}, request=request, on_delta=deltas.append) == "fixture reply"
    request.assert_called_once()
    assert "stream" not in request.call_args.args[1]
    assert not deltas


@pytest.mark.parametrize("hook", ["teacher_instance", "teacher_class", "reviewer_instance", "reviewer_class",
                                  "review_via_teacher", "client_private_instance", "client_public_instance",
                                  "client_private_class", "client_public_class", "client_subclass"])
def test_concurrent_worker_preserves_injected_ordinary_request_when_studio_is_open(monkeypatch, hook):
    monkeypatch.setattr("tingmu.providers._open", lambda *_: pytest.fail("unexpected HTTP request"))
    # Exercise the original positional-only fixtures through both worker forks.
    review = hook.startswith("review")
    profile = dict(provider="openai", model="fixture", base_url="http://127.0.0.1", key_source="none")
    agent = TranscriptReviewer(profile=profile) if review else LearningAgent(profile=profile)
    stage = "review" if review else "teaching"
    requests, deltas = [], []

    def request(path, body):
        assert path == "/chat/completions"
        requests.append(body)
        payload = json.loads(body["messages"][-1]["content"])
        content = json.dumps(structured_answer(stage, payload))
        return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}

    if hook == "client_subclass":
        class FixtureClient(LlmClient):
            def _request(self, path, body):
                return request(path, body)

        agent.client = FixtureClient(profile=profile)
    elif hook.startswith("client"):
        name = "_request" if "private" in hook else "request"
        if hook.endswith("instance"):
            monkeypatch.setattr(agent.client, name, request)
        else:
            monkeypatch.setattr(LlmClient, name, lambda self, path, body: request(path, body))
    elif hook.endswith("instance"):
        monkeypatch.setattr(agent, "_request", request)
    else:
        cls = TranscriptReviewer if hook == "reviewer_class" else LearningAgent
        monkeypatch.setattr(cls, "_request", lambda self, path, body: request(path, body))
    complete = agent.review if review else agent.annotate
    result = complete([Cue("c1", 0, 2, "Fixture line.")], LanguagePair("en", "en"),
                      on_delta=lambda *args: deltas.append(args), cancelled=lambda: False)
    assert result[0].text == "Fixture line."
    assert len(requests) == 1 and "stream" not in requests[0]
    assert deltas == []
