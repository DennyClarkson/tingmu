"""Bounded content-only projection from provider output to the live studio."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier

import pytest

from tingmu.studio_stream import ReadableStream, StudioStreams, TAIL


@pytest.mark.parametrize(("payload", "expected"), [
    ({"cues": [{"id": "ignored", "text": "粵🙂", "reason": "checked",
                "segmented_texts": ["法語", 'A "quote"']} ]}, '粵🙂 · checked · 法語 · A "quote"'),
    ({"cues": [{"explanation": "Une phrase", "tokens": [
        {"text": "cœur", "reading": "kœʁ", "meaning": "heart", "learn": True, "pos": "omit"}]}]},
     "Une phrase · cœur · kœʁ · heart"),
    ({"cues": [{"id": "external", "translation": "hello\nworld", "segments": ["one", "two"]}]},
     "hello world · one · two"),
    ({"groups": [{"start_index": 0, "end_index": 7}, {"start_index": 8, "end_index": 9, "text": "次です"}]},
     "#1–#8 · 次です"),
])
@pytest.mark.parametrize("escaped", [False, True])
def test_reader_handles_utf8_surrogates_and_json_syntax_across_one_byte_deltas(payload, expected, escaped):
    reader = ReadableStream()
    raw = json.dumps(payload, ensure_ascii=escaped).encode()
    for byte in raw:
        reader.feed(bytes([byte]))
    assert "".join(reader.tail) == expected and reader.seq == len(expected)


def test_opaque_subtrees_and_id_strings_never_escape_into_public_text():
    private = '{"text":"not public"}'
    raw = json.dumps({"reasoning_content": {"groups": [{"text": private, "start_index": 0, "end_index": 2}]},
                      "cues": [{"id": private, "pos": private, "reasoning_content": [{"reading": private}],
                                "metadata": {"explanation": private}, "text": "公開"}]})
    reader = ReadableStream()
    reader.feed("\x60\x60\x60json\n" + raw[:-4])
    assert "not public" not in "".join(reader.tail)
    reader.feed(raw[-4:] + "\n\x60\x60\x60")
    assert "".join(reader.tail) == "公開"


def test_live_tail_is_bounded_and_visible_before_json_finishes():
    reader = ReadableStream()
    reader.feed('{"cues":[{"text":"')
    text = "泰語 é あ🙂" * 7000
    reader.feed(text)
    assert "".join(reader.tail) == text[-TAIL:] and reader.seq == len(text)
    reader.feed('","translation":"é')
    assert "".join(reader.tail).endswith("🙂 · é")
    assert reader.seq == len(text) + 4


def test_sixteen_lanes_keep_content_isolated_through_retry_completion_and_reassignment():
    hub = StudioStreams()
    send = hub.bind("job", "逐句讲解")
    slots = [dict(id=i, status="running") for i in range(16)]
    hub.sync("job", "逐句讲解", slots)
    gate = Barrier(16)
    def worker(index):
        gate.wait(timeout=10)
        send(index, '{"translation":"')
        for _ in range(35):
            send(index, chr(0x4e00 + index) * 8)
    with ThreadPoolExecutor(16) as pool:
        list(pool.map(worker, range(16)))
    rows = hub.snapshot("job", "逐句讲解")
    assert all(row["stream_tail"] == chr(0x4e00 + int(key)) * TAIL and row["stream_seq"] == 280
               for key, row in rows.items())
    assert len({r["stream_epoch"] for r in rows.values()}) == 16
    send(3, None)
    send(3, '{"text":"new')
    assert hub.snapshot("job", "逐句讲解")["3"]["stream_tail"] == "new"
    slots[3]["status"] = "completed"
    hub.sync("job", "逐句讲解", slots)
    send(3, "late")
    assert hub.snapshot("job", "逐句讲解")["3"]["stream_tail"] == "new"
    slots[3] = dict(id=18, status="running")
    hub.sync("job", "逐句讲解", slots)
    assert "3" not in hub.snapshot("job", "逐句讲解")
    next_stage = hub.bind("job", "译稿核验")
    hub.sync("job", "译稿核验", [slots[0]])
    send(0, '{"text":"old callback')
    next_stage(0, '{"translation":"final')
    assert hub.snapshot("job", "译稿核验")["0"]["stream_tail"] == "final"
    assert hub.snapshot("job", "逐句讲解") == hub.snapshot("different", "译稿核验") == {}
    hub.clear()
    next_stage(0, "late")
    assert not hub.snapshot("job", "译稿核验")
