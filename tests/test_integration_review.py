"""Offline integration review regression cases; no real provider requests."""

import json
import threading
from unittest.mock import patch

import pytest

from tingmu.agent import LearningAgent
from tingmu.models import Cue, LanguagePair


def _response(body):
    payload = json.loads(body["messages"][-1]["content"])
    rows = [{"id": c["id"], "text": c["text"], "translation": "meaning",
             "explanation": "usage", "uncertain": False,
             "tokens": [{"text": c["text"], "reading": "", "meaning": "", "learn": False}]}
            for c in payload["cues"]]
    return {"model": "fake", "usage": {"total_tokens": 10},
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps({"cues": rows})}}]}


def _agent():
    with patch("tingmu.agent._config", return_value=("fake", "https://example.invalid", "fake")):
        return LearningAgent("openai")


def test_resumed_default_batch_keeps_intervening_completed_context():
    agent = _agent()
    full = [Cue(str(i), i, i + 1, f"word{i}") for i in range(8)]
    requests = []

    def request(worker, path, body):
        requests.append(json.loads(body["messages"][-1]["content"]))
        return _response(body)

    with patch.object(LearningAgent, "_request", request):
        agent.annotate([full[2], full[5]], LanguagePair("en", "en"), context_cues=full)
    serialized = json.dumps(requests)
    assert "word3" in serialized and "word4" in serialized, requests


def test_checkpoint_failure_still_accounts_for_paid_inflight_response():
    agent = _agent()
    agent.chunk_size = 1
    both_started = threading.Barrier(2)
    release_second = threading.Event()
    responses_returned = []

    def request(worker, path, body):
        index = json.loads(body["messages"][-1]["content"])["cues"][0]["id"]
        both_started.wait(timeout=3)
        if index == "1":
            assert release_second.wait(3)
        responses_returned.append(index)
        return _response(body)

    def checkpoint(rows):
        release_second.set()
        raise OSError("transient checkpoint write failure")

    with patch.object(LearningAgent, "_request", request):
        with pytest.raises(OSError, match="checkpoint"):
            agent.annotate([Cue(str(i), i, i + 1, f"word{i}") for i in range(2)],
                           LanguagePair("en", "en"), checkpoint=checkpoint)
    assert sorted(responses_returned) == ["0", "1"]
    assert len(agent.response_metadata) == 2
    assert len(agent.batch_metadata) == 2
