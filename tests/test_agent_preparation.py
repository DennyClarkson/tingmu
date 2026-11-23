from dataclasses import replace
import json

import pytest

from tingmu import pipeline, subtitle_sources
from tingmu.agent import LearningAgent
from tingmu.lesson_material import lesson_counts, translated_subtitles
from tingmu.models import AppSettings, Cue, preparation_identity_settings
from tingmu.providers import ProviderError
from tingmu.storage import Store


@pytest.fixture
def film(tmp_path, monkeypatch):
    media = tmp_path / "Example.S02E03.mkv"
    media.write_bytes(b"test film")
    store = Store(tmp_path / "workspace")
    settings = AppSettings(preparation_workflow="subtitle_agent", target_language="en",
                           source_language="zh-CN", llm_high_concurrency=False)
    monkeypatch.setattr(subtitle_sources, "_probe", lambda *_: {
        "format": {"duration": "6"}, "streams": [{"codec_type": "audio", "index": 0, "tags": {"language": "eng"}}],
    })
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **k: pytest.fail("unexpected ASR"))
    return media, store, settings


def write_subtitles(path, lines):
    path.write_text("\n".join(f"{i+1}\n00:00:0{i*2},000 --> 00:00:0{i*2+2},000\n{text}\n"
                              for i, text in enumerate(lines)), encoding="utf-8")


SOURCE = ["I know you.", "Where are you?", "We can go now."]
KNOWN = ["我认识你。", "你在哪里？", "我们现在可以走了。"]


@pytest.fixture
def requests(monkeypatch):
    calls = []

    def complete(agent, prompt, payload, schema):
        fields = schema["properties"]["cues"]["items"]["required"]
        inputs = payload["cues"]
        if "segmented_texts" in fields:
            stage = "review"
            rows = [{**r, "segmented_texts": [r["text"]], "reason": "", "uncertain": False} for r in inputs]
        elif "segments" in fields:
            stage = "verify"
            rows = [{"id": r["id"], "translation": "终稿", "segments": []} for r in inputs]
        else:
            stage = "teach" if "translation" in fields else "explain_only"
            rows = [{"id": r["id"], "text": r["text"], "explanation": "此处使用人称代词。",
                     "uncertain": False, "tokens": [{"text": r["text"], "meaning": "", "reading": "",
                                                    "learn": False}]} for r in inputs]
            if stage == "teach":
                for r in rows:
                    r["translation"] = "草稿"
            else:
                assert all("known_caption" in r for r in inputs)
                assert "Never generate, revise, or return a translation" in prompt
        calls.append((stage, payload))
        agent.last_response_metadata = {"returned_model": "offline-test"}
        return json.dumps({"cues": rows}, ensure_ascii=False)

    monkeypatch.setattr(LearningAgent, "_complete", complete)
    return calls


def test_bilingual_calls_only_explanations_and_preserves_captions(film, requests):
    media, store, settings = film
    path = media.with_suffix(".srt")
    write_subtitles(path, [f"{s}\n{k}" for s, k in zip(SOURCE, KNOWN)])
    events = []
    doc = pipeline.prepare_media(media, settings, store, progress=events.append)
    assert [r[0] for r in requests] == ["explain_only"]
    assert any(getattr(e, "details", {}).get("decision") for e in events)
    assert doc.metadata["subtitle_plan"]["decision"] == "bilingual"
    assert doc.metadata["lesson_status"] == "provided"
    assert [c.original_text for c in doc.cues] == SOURCE
    assert [(c.start, c.end) for c in doc.cues] == [(0, 2), (2, 4), (4, 6)]
    assert [c.text for c in translated_subtitles(doc)] == KNOWN
    assert lesson_counts(doc) == {"total": 3, "taught": 3, "verified": 0, "provided": 3}
    assert all(c.explanation for c in doc.cues)
    exports = store.material_paths(str(media), settings.language)[1]
    assert all(t in exports.with_suffix(".translation.srt").read_text(encoding="utf-8-sig") for t in KNOWN)
    pipeline.prepare_media(media, settings, store)
    pipeline.prepare_media(media, replace(settings, asr_model="unused"), store)
    assert len(requests) == 1
    # A teaching-only reset keeps the documentary evidence even if the external file was moved.
    store.reset_material(str(media), settings.language, "teaching")
    path.unlink()
    resumed = pipeline.prepare_media(media, settings, store)
    assert [r[0] for r in requests] == ["explain_only", "explain_only"]
    assert [c.translation for c in resumed.cues] == KNOWN


def test_mixed_caption_coverage_only_generates_for_the_gap(film, requests):
    media, store, settings = film
    write_subtitles(media.with_suffix(".srt"), [f"{SOURCE[0]}\n{KNOWN[0]}", SOURCE[1], f"{SOURCE[2]}\n{KNOWN[2]}"])
    doc = pipeline.prepare_media(media, settings, store)
    assert doc.metadata["lesson_status"] == "mixed"
    assert lesson_counts(doc) == {"total": 3, "taught": 3, "verified": 1, "provided": 2}
    assert [c.text for c in translated_subtitles(doc)] == [KNOWN[0], "终稿", KNOWN[2]]
    for stage in ("teach", "verify"):
        assert [r["text"] for k, p in requests if k == stage for r in p["cues"]] == [SOURCE[1]]


@pytest.mark.parametrize("layout", ["reference", "wrong_episode"])
def test_asr_receives_selected_track_and_only_safe_references_reach_text_service(film, requests, monkeypatch, layout):
    media, store, settings = film
    path = media.with_suffix(".srt") if layout == "reference" else media.with_name("Example.S02E04.srt")
    write_subtitles(path, KNOWN)
    raw = [Cue(f"asr-{i}", i * 2, i * 2 + 2, text) for i, text in enumerate(SOURCE)]
    runs = []
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **kw: runs.append(kw["audio_track"]) or raw)
    doc = pipeline.prepare_media(media, settings, store, subtitle_path=path)
    assert runs == [settings.audio_track]
    assert [stage for stage, _ in requests] == ["review", "teach", "verify"]
    assert [c.original_text for c in doc.cues] == SOURCE
    assert [c.translation for c in doc.cues] == ["终稿"] * 3
    assert not doc.metadata["provided_translations"]
    for stage, payload in requests[:2]:
        if layout == "reference":
            assert payload["known_language_reference_in_cue_order"] == KNOWN
        else:
            assert "known_language_reference_in_cue_order" not in payload
    assert doc.metadata["subtitle_plan"]["decision"] == ("reference" if layout == "reference" else "asr")
    assert [r["text"] for r in doc.metadata["subtitle_reference_cues"]] == (KNOWN if layout == "reference" else [])


def test_source_only_waits_for_teaching_then_resumes_without_asr(film, requests):
    media, store, settings = film
    write_subtitles(media.with_suffix(".srt"), SOURCE)
    with pytest.raises(ProviderError, match="启用逐句讲解模型"):
        pipeline.prepare_media(media, replace(settings, text_models_enabled=False), store)
    partial = store.load_document(str(media), settings.language)
    assert partial.metadata["lesson_status"] == "needs_teaching" and not requests
    assert [c.original_text for c in partial.cues] == SOURCE and not any(c.explanation for c in partial.cues)
    doc = pipeline.prepare_media(media, settings, store)
    assert [r[0] for r in requests] == ["teach", "verify"]
    assert lesson_counts(doc) == {"total": 3, "taught": 3, "verified": 3}


def test_agent_never_reuses_legacy_generic_translated_subtitle_as_original(film, requests, monkeypatch):
    media, store, settings = film
    write_subtitles(media.with_suffix(".srt"), KNOWN)
    classic = replace(settings, preparation_workflow="classic", text_models_enabled=False)
    old = pipeline.prepare_media(media, classic, store)
    assert [c.text for c in old.cues] == KNOWN
    runs = []
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **kw: runs.append(True)
                        or [Cue("a", 0, 2, SOURCE[0])])
    with pytest.raises(ProviderError, match="旧字幕"):
        pipeline.prepare_media(media, settings, store, reuse_existing_transcript=True)
    assert not runs
    current = pipeline.prepare_media(media, settings, store)
    assert runs == [True] and [c.original_text for c in current.cues] == SOURCE[:1]
    assert current.metadata["subtitle_plan"]["decision"] == "reference"
    assert preparation_identity_settings({"preparation_workflow": "classic"}) == {}


def test_known_asr_cache_is_reusable_when_reference_track_arrives(film, requests, monkeypatch):
    media, store, settings = film
    original = [Cue("first", 0, 2, SOURCE[0]), Cue("outside", 4, 7, SOURCE[2])]
    runs = []

    def transcribe(*args, **kw):
        runs.append(True)
        kw["metadata_out"].update(asr_backend="faster-whisper", alignment_warning_count=1)
        return original

    monkeypatch.setattr(pipeline, "transcribe", transcribe)
    classic = replace(settings, preparation_workflow="classic", text_models_enabled=False)
    pipeline.prepare_media(media, classic, store)
    write_subtitles(media.with_suffix(".srt"), KNOWN)
    doc = pipeline.prepare_media(media, settings, store)
    assert runs == [True] and doc.metadata["alignment_warning_count"] == 1
    assert doc.metadata["subtitle_reference_hints"] == {"first": KNOWN[0]}
    assert [c.end for c in doc.cues] == [2, 7]
    assert requests[0][1]["known_language_reference_in_cue_order"] == [KNOWN[0], ""]


def test_supplement_adopts_identical_confirmed_legacy_source_and_preserves_taught_rows(film, requests):
    media, store, settings = film
    write_subtitles(media.with_suffix(".srt"), SOURCE)
    classic = replace(settings, preparation_workflow="classic", text_models_enabled=False)
    old = pipeline.prepare_media(media, classic, store)
    old.cues[0].translation = "先前译文"
    old.cues[0].explanation = "先前讲解"
    store.save_document(old)
    doc = pipeline.prepare_media(media, settings, store, reuse_existing_transcript=True)
    assert [c.id for c in doc.cues] == [c.id for c in old.cues]
    assert doc.cues[0].explanation == "先前讲解" and doc.metadata["subtitle_plan"]["decision"] == "source"
    assert [r["text"] for stage, p in requests if stage == "teach" for r in p["cues"]] == SOURCE[1:]


@pytest.mark.parametrize("missing", ["credentials", "profile"])
def test_missing_model_configuration_keeps_bilingual_evidence_for_retry(film, monkeypatch, missing):
    media, store, settings = film
    write_subtitles(media.with_suffix(".srt"), [f"{s}\n{k}" for s, k in zip(SOURCE, KNOWN)])
    monkeypatch.setattr("tingmu.agent._environment", lambda *_: "")
    monkeypatch.setattr("tingmu.providers._open", lambda *args: pytest.fail("Unexpected network request"))
    if missing == "profile":
        settings.annotation_profile_id = "deleted"
    with pytest.raises(ProviderError):
        pipeline.prepare_media(media, settings, store)
    partial = store.load_document(str(media), settings.language)
    assert partial.metadata["stage"] == "partial" and partial.metadata["lesson_status"] == "needs_teaching"
    assert [c.translation for c in partial.cues] == KNOWN
    assert not any(c.explanation for c in partial.cues)
    assert not translated_subtitles(partial)


def test_explanation_rejects_unsolicited_translation_and_preserves_valid_rows(film, monkeypatch):
    media, store, settings = film
    write_subtitles(media.with_suffix(".srt"), [f"{s}\n{k}" for s, k in zip(SOURCE, KNOWN)])
    calls = []

    def complete(agent, prompt, payload, schema):
        assert "translation" not in schema["properties"]["cues"]["items"]["properties"]
        rows = [{"id": r["id"], "text": r["text"], "explanation": "讲解", "uncertain": False,
                 "tokens": [{"text": r["text"], "meaning": "", "reading": "", "learn": False}]}
                for r in payload["cues"]]
        calls.append([r["text"] for r in rows])
        if len(calls) == 1:
            rows[1]["translation"] = "模型擅自生成的译文"
        return json.dumps({"cues": rows})

    monkeypatch.setattr(LearningAgent, "_complete", complete)
    doc = pipeline.prepare_media(media, settings, store)
    assert calls == [SOURCE, [SOURCE[1]]]
    assert [c.translation for c in doc.cues] == KNOWN
