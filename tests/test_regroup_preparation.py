"""Run the production regrouping, persistence and lesson stages against in-process text services."""

from dataclasses import replace
import json

import pytest

from test_agent_preparation import film as film, requests as requests, write_subtitles
from tingmu import pipeline, regroup as engine
from tingmu.agent import request_ids
from tingmu.lesson_material import lesson_counts
from tingmu.models import Cue
from tingmu.notebooks import NotebookManager
from tingmu.providers import ProviderError


@pytest.fixture
def regroup_calls(monkeypatch):
    calls = []

    def client(provider, model, *, profile):
        def complete(_system, payload, _schema):
            units = payload["units"]
            calls.append((provider, model, [u["text"] for u in units]))
            cuts = [0, *payload["mandatory_starts"], len(units)]
            return {
                "groups": [
                    {
                        "start_index": a,
                        "end_index": b - 1,
                        "start_char": None,
                        "end_char": None,
                        "text": " ".join(u["text"] for u in units[a:b]),
                        "uncertain": False,
                        "reason": "",
                    }
                    for a, b in zip(cuts, cuts[1:])
                ]
            }

        return complete

    monkeypatch.setattr(engine, "LlmClient", client)
    # The learning/verification fixture exercises validation and checkpointing
    # against mocked completions, so no user credentials are needed.
    monkeypatch.setattr("tingmu.agent._config", lambda _: ("", "http://example.invalid", "fixture"))
    return calls


@pytest.mark.parametrize("workflow", ["classic", "subtitle_agent"])
def test_disabled_text_services_never_receive_smart_input(film, requests, regroup_calls, workflow):
    media, store, settings = film
    lines = ["We can begin.", "Can you hear me?", "The train is late."]
    write_subtitles(media.with_suffix(".en.srt"), lines)
    config = replace(
        settings, segmentation_mode="llm_regroup", preparation_workflow=workflow, text_models_enabled=False
    )
    if workflow == "subtitle_agent":
        with pytest.raises(ProviderError):
            pipeline.prepare_media(media, config, store)
    else:
        pipeline.prepare_media(media, config, store)
    saved = store.load_document(str(media), settings.language)
    assert [c.text for c in saved.cues] == lines
    assert saved.metadata["lesson_status"] == (
        "needs_teaching" if workflow == "subtitle_agent" else "local_only"
    )
    assert not requests and not regroup_calls
    assert "regroup_evidence" not in saved.metadata


@pytest.mark.parametrize("workflow", ["classic", "subtitle_agent"])
def test_mixed_pairs_keep_source_identity_and_verified_gaps(film, requests, regroup_calls, workflow):
    media, store, settings = film
    paired, translation = "I know you.", "我认识你。"
    write_subtitles(media.with_suffix(".en.srt"), [paired + "\n" + translation, "Can you hear", "me?"])
    config = replace(settings, segmentation_mode="llm_regroup", preparation_workflow=workflow)
    doc = pipeline.prepare_media(media, config, store)
    first, merged = doc.cues
    assert (first.start, first.end, first.text, first.translation) == (0, 2, paired, translation)
    assert doc.metadata["provided_translations"] == {first.id: translation}
    assert (merged.start, merged.end, merged.text, merged.translation) == (2, 6, "Can you hear me?", "终稿")
    assert lesson_counts(doc) == {"total": 2, "taught": 2, "provided": 1, "verified": 1}
    assert regroup_calls == [(config.provider, config.model, ["Can you hear", "me?"])]
    for stage, payload in requests:
        expected = first if stage == "explain_only" else merged
        assert stage in {"explain_only", "teach", "verify"}
        assert [r["id"] for r in payload["cues"]] == (["1"] if stage == "verify" else request_ids([expected]))
        assert [r["text"] for r in payload["cues"]] == [expected.text]
    assert {stage for stage, _ in requests} == {"explain_only", "teach", "verify"}
    evidence = json.loads((store.data_dir / doc.metadata["regroup_evidence"]).read_text("utf8"))
    assert evidence["outputs"][first.id]["timing_basis"] == "protected_source"
    assert evidence["outputs"][merged.id]["source_indices"] == [1, 2]
    cached = pipeline.prepare_media(media, config, store)
    assert cached.cues == doc.cues and len(regroup_calls) == 1 and len(requests) == 3


def archive_citation(store, doc, cue):
    store.save_note(doc.media_path, cue, "A linked sentence", doc.language)
    entry = store.entries(doc.media_path)[0]
    notebook = NotebookManager(store)
    notebook.append_note(doc.language.target, "First lesson", "Keep this example.", [entry["id"]])
    store.delete_entry(entry["id"])
    assert not store.entries(doc.media_path)
    assert store.referenced_cue_ids(doc.media_path, doc.language) == {cue.id}
    return notebook, entry["id"]


@pytest.mark.parametrize("reuse", [False, True], ids=["queue", "supplement"])
def test_mode_change_retains_legacy_derived_notebook_anchor(film, requests, regroup_calls, reuse):
    media, store, settings = film
    media.with_suffix(".en.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nI want\n\n"
        "2\n00:00:00,300 --> 00:00:02,000\nI want to go.\n\n"
        "3\n00:00:04,000 --> 00:00:06,000\nWe can go now.\n",
        encoding="utf8",
    )
    classic = replace(settings, preparation_workflow="classic", text_models_enabled=False)
    old = pipeline.prepare_media(media, classic, store)
    linked = old.cues[0]
    assert linked.id not in {r["id"] for r in old.metadata["pre_llm_lineage"]["sources"]}
    notebook, entry_id = archive_citation(store, old, linked)
    smart = replace(classic, text_models_enabled=True, segmentation_mode="llm_regroup")
    new = pipeline.prepare_media(media, smart, store, reuse_existing_transcript=reuse)
    assert [(c.id, c.start, c.end, c.text) for c in new.cues if c.id == linked.id] == [
        (linked.id, linked.start, linked.end, linked.text)
    ]
    assert [c.text for c in new.cues] == ["I want to go.", "We can go now."]
    assert notebook.source_entry(entry_id)["cue_id"] == linked.id
    assert all(linked.text not in texts for _, _, texts in regroup_calls)
    assert new.metadata["pre_llm_lineage"]["outputs"][linked.id]["timing_basis"] == "protected_source"


def test_local_supplement_does_not_relabel_smart_ledger_or_clear_notebook(film, requests, regroup_calls):
    media, store, settings = film
    write_subtitles(media.with_suffix(".en.srt"), ["Can you hear", "me?"])
    smart = replace(
        settings, preparation_workflow="classic", segmentation_mode="llm_regroup", review_enabled=False
    )
    first = pipeline.prepare_media(media, smart, store)
    (linked,) = first.cues
    notebook, entry_id = archive_citation(store, first, linked)
    saved = pipeline.prepare_media(
        media, replace(smart, segmentation_mode="local"), store, reuse_existing_transcript=True
    )
    assert saved.cues == first.cues
    assert notebook.source_entry(entry_id)["cue_id"] == linked.id
    assert len(regroup_calls) == 1
    assert saved.metadata["segmentation_mode"] == "local"
    assert "regroup_stage" not in saved.metadata
    assert saved.metadata.get("review_spec") != first.metadata["regroup_spec"]


def test_supplement_keeps_verified_translations_distinct_from_imported_pairs(film, requests, regroup_calls):
    media, store, settings = film
    write_subtitles(media.with_suffix(".en.srt"), ["I know you.\n我认识你。", "Can you hear", "me?"])
    smart = replace(settings, segmentation_mode="llm_regroup")
    first = pipeline.prepare_media(media, smart, store)
    assert lesson_counts(first) == {"total": 2, "taught": 2, "provided": 1, "verified": 1}
    saved = pipeline.prepare_media(
        media, replace(smart, segmentation_mode="local"), store, reuse_existing_transcript=True
    )
    assert saved.metadata["provided_translations"] == first.metadata["provided_translations"]
    assert lesson_counts(saved) == lesson_counts(first)
    assert [(c.id, c.start, c.end, c.translation) for c in saved.cues] == [
        (c.id, c.start, c.end, c.translation) for c in first.cues
    ]


def test_mode_change_keeps_reference_track_after_sidecar_moves(film, requests, regroup_calls, monkeypatch):
    media, store, settings = film
    sidecar = media.with_suffix(".srt")
    write_subtitles(sidecar, ["我认识你。", "你在哪里？", "我们现在可以走了。"])
    asr = [
        Cue(str(i), 2 * i, 2 * i + 2, text, speaker=str(i))
        for i, text in enumerate(["I know you.", "Where are you?", "We can go now."])
    ]
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **kw: asr)
    smart = replace(settings, segmentation_mode="llm_regroup")
    first = pipeline.prepare_media(media, smart, store, subtitle_path=sidecar)
    assert first.metadata["subtitle_plan"]["decision"] == "reference"
    assert len(first.metadata["subtitle_reference_hints"]) == 3
    sidecar.unlink()
    monkeypatch.setattr(pipeline, "transcribe", lambda *a, **kw: pytest.fail("repeat recognition"))
    saved = pipeline.prepare_media(media, settings, store, reuse_existing_transcript=True)
    assert saved.metadata["subtitle_reference_cues"] == first.metadata["subtitle_reference_cues"]
    assert saved.metadata["subtitle_reference_hints"] == first.metadata["subtitle_reference_hints"]
    assert saved.metadata["provided_translations"] == {}


def test_changed_media_fingerprint_requires_fresh_recognition_across_modes(
    film, requests, regroup_calls, monkeypatch
):
    media, store, settings = film
    recognized = []

    def transcribe(path, *args, **kwargs):
        recognized.append(path.read_bytes())
        return [Cue("one", 0, 1, "The first film." if len(recognized) == 1 else "Replacement speech.")]

    monkeypatch.setattr(pipeline, "transcribe", transcribe)
    local = replace(settings, preparation_workflow="classic", text_models_enabled=False)
    pipeline.prepare_media(media, local, store)
    media.write_bytes(b"replacement video of a different size")
    smart = replace(local, segmentation_mode="llm_regroup", text_models_enabled=True)
    doc = pipeline.prepare_media(media, smart, store)
    assert len(recognized) == 2
    assert [c.text for c in doc.cues] == ["Replacement speech."]
    assert [texts for _, _, texts in regroup_calls] == [["Replacement speech."]]


@pytest.mark.parametrize("new_profile", [False, True], ids=["same-profile", "changed-profile"])
def test_partial_regroup_resumes_only_matching_profile(
    film, requests, regroup_calls, monkeypatch, new_profile
):
    media, store, settings = film
    media.with_suffix(".en.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nFirst speaker.\n\n"
        "2\n00:02:04,000 --> 00:02:06,000\nSecond speaker.\n",
        encoding="utf8",
    )
    registry = store.model_registry
    profile = {
        "id": "regroup",
        "provider": "local_compatible",
        "model": "alpha",
        "base_url": "http://127.0.0.1:8000/v1",
        "key_source": "none",
    }
    registry.save_profile(profile)
    cfg = replace(
        settings,
        segmentation_mode="llm_regroup",
        preparation_workflow="classic",
        review_profile_id="regroup",
        review_enabled=False,
    )
    healthy = engine.LlmClient

    def interrupted(*args, **kwargs):
        complete = healthy(*args, **kwargs)

        def run(system, payload, schema):
            if payload["units"][0]["text"] == "Second speaker.":
                raise ProviderError("fixture pause", category="configuration")
            return complete(system, payload, schema)

        return run

    monkeypatch.setattr(engine, "LlmClient", interrupted)
    with pytest.raises(ProviderError):
        pipeline.prepare_media(media, cfg, store)
    partial = store.load_document(str(media), cfg.language)
    assert partial.metadata["regroup_stage"] == "partial"
    assert len(partial.metadata["regroup_checkpoint"]["completed"]) == 1
    assert not requests
    monkeypatch.setattr(engine, "LlmClient", healthy)
    if new_profile:
        registry.save_profile({**profile, "base_url": "http://127.0.0.1:8001/v1"})
    ready = pipeline.prepare_media(media, cfg, store)
    assert ready.metadata["regroup_stage"] == "ready" and "regroup_checkpoint" not in ready.metadata
    assert [c.text for c in ready.cues] == ["First speaker.", "Second speaker."]
    assert [v[2] for v in regroup_calls] == (
        [["First speaker."], ["First speaker."], ["Second speaker."]]
        if new_profile
        else [["First speaker."], ["Second speaker."]]
    )
    assert (
        partial.metadata["regroup_spec"]["profile"] != ready.metadata["regroup_spec"]["profile"]
    ) == new_profile
    assert ready.metadata["regroup_spec"]["model"] == "alpha"
    assert ready.metadata["annotation_spec"]["model"] == cfg.model
