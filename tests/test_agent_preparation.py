from dataclasses import replace
import json

import pytest

from tingmu import pipeline, subtitle_sources
from tingmu.agent import LearningAgent, request_ids
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


@pytest.mark.parametrize("local_only", [True, False])
def test_classic_study_extracts_raw_ass_and_protects_its_provided_translations(film, requests, local_only):
    import pysubs2

    media, store, settings = film
    path = media.with_suffix(".ass")
    subs = pysubs2.SSAFile()
    raw = r"{\i1}" + SOURCE[0] + r"{\i0}\N" + KNOWN[0]
    subs.events.extend([
        pysubs2.SSAEvent(start=0, end=2000, text=raw),
        pysubs2.SSAEvent(start=2100, end=3300, text=r"{\pos(20,20)}Station Square"),
        pysubs2.SSAEvent(start=3400, end=5900, text=SOURCE[1]),
    ])
    subs.save(path)
    config = replace(settings, preparation_workflow="classic", text_models_enabled=not local_only)
    doc = pipeline.prepare_media(media, config, store)
    first, second = doc.cues
    assert (first.text, first.original_text, first.translation) == (SOURCE[0], raw, KNOWN[0])
    assert doc.metadata["raw_display_text"] == {first.id: first.text, second.id: second.text}
    assert doc.metadata["provided_translations"] == {first.id: KNOWN[0]}
    assert doc.metadata["subtitle_import"]["events"][second.id]["event_index"] == 2
    if local_only:
        assert not requests and not first.explanation
    else:
        assert all(item["text"] == SOURCE[1] for kind, payload in requests if kind in {"review", "teach", "verify"}
                   for item in payload["cues"])
        explained = [r for kind, payload in requests if kind == "explain_only" for r in payload["cues"]]
        assert [r["known_caption"] for r in explained] == [KNOWN[0]]


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



@pytest.mark.parametrize(("backend", "model", "source"), [
    ("local", "qwen3-asr-1.7b", "local_qwen"),
    ("local", "large-v3-turbo", "local_whisper"),
    ("local", "local:custom", "local_whisper"),
    ("local_api", "any", "local_api"),
    ("external", "any", "external_api"),
])
def test_acquisition_quality_gate_precedes_all_text_roles(film, requests, monkeypatch, backend, model, source):
    media, store, settings = film
    settings = replace(settings, asr_model=model, asr_backend=backend, target_language="yue")
    monkeypatch.setattr("tingmu.local_whisper.resolve_local_model", lambda _: media.parent / "custom")
    rows = [Cue("left", 1.1, 2.3, "入咗成個"), Cue("end", 2.34, 2.42, "鐘。"),
            Cue("noise", 3.0, 3.1, "♪"), Cue("next", 6.5, 8.0, "聽日再見。")]
    def acquire(*args, **kwargs):
        return rows

    monkeypatch.setattr(pipeline, "transcribe", acquire)
    monkeypatch.setattr("tingmu.external_asr.transcribe_external", acquire)
    result = pipeline.prepare_media(media, settings, store, force_asr=True)
    audit = result.metadata["pre_llm_audit"]
    assert audit["input_cue_count"] == 4 and audit["output_cue_count"] == 2
    assert audit["coalesced_count"] == 1
    assert {"split_runon_count", "repaired_timing_count", "sub_500ms_count", "short_fragment_count"} <= audit.keys()
    assert [c.original_text for c in result.cues] == ["入咗成個鐘。", "聽日再見。"]
    assert [role for role, _ in requests] == ["review", "teach", "verify"]
    ids = [c.id for c in result.cues]
    for role, payload in requests:
        wire_ids = [str(i) for i in range(1, len(ids) + 1)] if role == "verify" else request_ids(result.cues)
        assert [r["id"] for r in payload["cues"]] == wire_ids
        assert [r["text"] for r in payload["cues"]] == [c.text for c in result.cues]
    assert set(result.metadata["translation_review"]["verified"]) == set(ids)
    lineage = result.metadata["pre_llm_lineage"]
    assert [r["text"] for r in lineage["sources"]] == [c.text for c in rows]
    assert lineage["output_sources"][ids[0]] == ["left", "end"]
    assert "noise" in lineage["dropped_ids"]
    events = [json.loads(line) for line in (store.data_dir / "logs/events.jsonl").read_text("utf8").splitlines()]
    fields, = [e["fields"] for e in events if e["event"] == "pre_llm_audit"]
    assert fields["source_kind"] == source and fields["output_cue_count"] == 2
    assert all(type(v) is int for k, v in fields.items() if k not in ("source_kind", "media_id"))
    assert store.load_document(str(media), settings.language).metadata["pre_llm_lineage"] == lineage


@pytest.mark.parametrize("embedded", [False, True])
def test_provided_pairs_reach_explanation_with_source_ids(film, requests, monkeypatch, embedded):
    media, store, settings = film
    path = media.with_suffix(".srt")
    write_subtitles(path, [f"{s}\n{k}" for s, k in zip(SOURCE, KNOWN)])
    if embedded:
        payload = path.read_bytes()
        path.unlink()
        monkeypatch.setattr(subtitle_sources, "_probe", lambda *_: {
            "format": {"duration": "6"}, "streams": [
                {"index": 0, "codec_type": "audio", "tags": {"language": "eng"}},
                {"index": 2, "codec_type": "subtitle", "codec_name": "subrip", "tags": {"language": "eng"}},
            ]})
        monkeypatch.setattr(subtitle_sources, "_extract", lambda *_: (payload, "srt"))
    log = []
    result = pipeline.prepare_media(media, settings, store, log_event=lambda *a, **kw: log.append(kw))
    fields, = log
    assert fields["source_kind"] == ("embedded" if embedded else "sidecar")
    assert fields["input_cue_count"] == fields["output_cue_count"] == 3
    ids = [c.id for c in result.cues]
    assert result.metadata["provided_translations"] == dict(zip(ids, KNOWN))
    assert [role for role, _ in requests] == ["explain_only"]
    assert [r["id"] for r in requests[0][1]["cues"]] == request_ids(result.cues)
    assert result.metadata["pre_llm_lineage"]["output_sources"] == {i: [i] for i in ids}



def test_quality_gate_keeps_linked_ids_on_resume(film):
    from tingmu.models import StudyDocument
    from tingmu.preparation import apply_quality_gate

    media, store, settings = film
    rows = [Cue("old-left", 0, 1.0, "入咗成個"), Cue("old-note", 1.02, 1.1, "鐘。")]
    doc = StudyDocument(str(media), settings.language, rows)
    store.save_note(str(media), rows[1], "笔记", settings.language)
    apply_quality_gate(doc, store, source_kind="local_whisper")
    assert [c.id for c in doc.cues] == ["old-left", "old-note"]
    assert doc.cues[1] == rows[1]
    first = json.dumps(doc.metadata, sort_keys=True)
    apply_quality_gate(doc, store, source_kind="local_whisper",
                       log_event=lambda *_a, **_kw: pytest.fail("repeat normalization"))
    assert json.dumps(doc.metadata, sort_keys=True) == first


def test_only_noise_is_audited_without_sending_a_text_request(film, requests, monkeypatch):
    media, store, settings = film
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **k: [Cue("x", 0, .1, "♪")])
    with pytest.raises(ValueError, match="有效对白"):
        pipeline.prepare_media(media, settings, store, force_asr=True)
    assert not requests
    records = [json.loads(line) for line in (store.data_dir / "logs/events.jsonl").read_text("utf8").splitlines()]
    assert records[0]["fields"]["input_cue_count"] == 1
    assert records[0]["fields"]["output_cue_count"] == 0



def test_quality_gate_accepts_catalog_and_custom_language_tags_in_both_roles():
    from tingmu.locale_catalog import language_choices
    from tingmu.models import LanguagePair
    from tingmu.pre_llm import normalize

    codes = [item["code"] for item in language_choices()] + ["x-test"]
    for known, studied in zip(codes, codes[1:] + codes[:1]):
        pair = LanguagePair(known, studied)
        # A supplied bilingual proper name may be shared verbatim in any language.
        rows = [Cue("paired", 0, 1.2, "Alma", translation="Alma"), Cue("noise", 2, 2.1, "♪")]
        out, audit, evidence = normalize(rows, source_kind="embedded", language=pair)
        assert out == rows[:1], (known, studied)
        assert audit["protected_count"] == audit["dropped_count"] == 1
        assert evidence["output_sources"] == {"paired": ["paired"]}



def test_supplement_matches_raw_rolling_events_after_classic_gate(film, requests):
    media, store, settings = film
    path = media.with_suffix(".en.srt")
    path.write_text("1\n00:00:00,000 --> 00:00:01,000\nI want\n\n"
                    "2\n00:00:00,300 --> 00:00:02,000\nI want to go.\n\n"
                    "3\n00:00:04,000 --> 00:00:06,000\nWe can go now.\n", encoding="utf8")
    classic = pipeline.prepare_media(media, replace(settings, preparation_workflow="classic",
                                                     text_models_enabled=False), store)
    assert [c.text for c in classic.cues] == ["I want to go.", "We can go now."]
    ledger = classic.metadata["pre_llm_lineage"]
    supplemented = pipeline.prepare_media(media, settings, store, reuse_existing_transcript=True)
    assert [c.id for c in supplemented.cues] == [c.id for c in classic.cues]
    assert supplemented.metadata["pre_llm_lineage"] == ledger
    events = supplemented.metadata["subtitle_import"]["events"]
    assert [events[c["id"]]["event_index"] for c in ledger["sources"]] == [0, 1, 2]
    assert [stage for stage, _ in requests] == ["teach", "verify"]



@pytest.mark.parametrize("has_review_role", [False, True])
def test_smart_mode_replaces_review_and_freezes_its_output_ids(film, requests, monkeypatch, has_review_role):
    from tingmu import regroup as engine

    media, store, settings = film
    settings = replace(settings, segmentation_mode="llm_regroup", preparation_workflow="classic",
                       review_profile_id="separate-review" if has_review_role else "",
                       review_enabled=False, review_model="unused", model="teacher")
    profiles = {"annotation": None,
                "review": {"id": "separate-review", "provider": "openai", "model": "reviewer"}}
    monkeypatch.setattr(store.model_registry, "text_role", lambda _, role: (True, profiles[role]))
    raw = [Cue("a", 1, 2.0, "Can you hear"), Cue("b", 2.1, 3.2, "me?")]
    asr = []
    monkeypatch.setattr(pipeline, "transcribe", lambda *_a, **_kw: asr.append(True) or raw)
    monkeypatch.setattr(pipeline, "TranscriptReviewer", lambda **_: pytest.fail("duplicate review"))
    routed = []
    def assemble(units, language, **options):
        routed.append(options["model"])
        assert [u["text"] for u in units] == [c.text for c in raw]
        out = Cue("smart-1", units[0]["start"], units[-1]["end"], "Can you hear me?")
        return [out], {"input_unit_count": 2, "output_cue_count": 1, "fallback_chunk_count": 0}, {
            "output_sources": {out.id: ["a", "b"]}, "outputs": {out.id: {
                "source_indices": [0, 1], "raw_text": out.text, "timing_basis": "source_union",
                "reason": "", "operations": ["regroup"]}}}
    monkeypatch.setattr(engine, "regroup", assemble)
    doc = pipeline.prepare_media(media, settings, store)
    assert routed == ["reviewer" if has_review_role else "teacher"]
    assert asr == [True]
    assert [k for k, _ in requests] == ["teach", "verify"]
    assert doc.metadata["segmentation_mode"] == doc.metadata["transcript_spec"]["segmentation_mode"] == "llm_regroup"
    assert doc.metadata["reviewed_text"] == {"smart-1": "Can you hear me?"}
    assert doc.metadata["pre_llm_lineage"]["output_sources"] == {"smart-1": ["a", "b"]}
    assert doc.metadata["regroup_stage"] == "ready"
    assert (store.data_dir / doc.metadata["regroup_evidence"]).is_file()
    pipeline.prepare_media(media, settings, store)
    assert len(routed) == len(asr) == 1 and len(requests) == 2
    # Changing the mode reuses the original acoustic stream, not the smart sentence.
    offline = replace(settings, segmentation_mode="local", text_models_enabled=False)
    monkeypatch.setattr(store.model_registry, "text_role", lambda *_: (False, None))
    local = pipeline.prepare_media(media, offline, store)
    assert asr == [True] and local.metadata["pre_llm_audit"]["input_cue_count"] == 2
    assert local.metadata["segmentation_mode"] == "local" and "regroup_stage" not in local.metadata
