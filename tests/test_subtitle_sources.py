"""Conservative subtitle planning without ASR, OCR or a network dependency."""

import json
import subprocess

import pysubs2
import pytest

from tingmu import subtitle_sources as sources
from tingmu.models import Cue, LanguagePair


PAIR = LanguagePair(source="zh-CN", target="en")
EN = ["Where are you?", "I am here.", "Let's go home."]
ZH = ["你在哪里？", "我在这里。", "我们回家吧。"]
TIMES = [(1, 3), (5, 7), (9, 11)]


def captions(rows, fmt="srt", *, times=TIMES, styles=None):
    subs = pysubs2.SSAFile()
    for i, (text, (start, end)) in enumerate(zip(rows, times)):
        style = styles[i] if styles else "Default"
        subs.styles[style] = pysubs2.SSAStyle()
        subs.events.append(pysubs2.SSAEvent(start=start*1000, end=end*1000,
                                             text=text.replace("\n", r"\N"), style=style))
    return subs.to_string(fmt).encode()


def stream(index, codec, lang="eng", *, forced=0):
    return {"index": index, "codec_type": "subtitle", "codec_name": codec, "tags": {"language": lang},
            "disposition": {"forced": forced}}


@pytest.fixture
def media(tmp_path, monkeypatch):
    video = tmp_path / "Example S01E02 Long Title (1080p).mkv"
    video.touch()
    info = {"format": {"duration": "12"}, "streams": [
        {"index": 1, "codec_type": "audio", "tags": {"language": "eng"}}]}
    monkeypatch.setattr(sources, "_probe", lambda path, stop: info)
    return video, info


@pytest.mark.parametrize("fmt", ["srt", "vtt", "ass", "ssa"])
def test_mixed_file_preserves_language_roles_and_timing(media, fmt):
    video, _ = media
    side = video.parent / f"Example.S01E02.zh.{fmt}"
    side.write_bytes(captions([z+"\n"+e for z, e in zip(ZH, EN)], fmt))
    result = sources.plan_subtitle_sources(video, PAIR)
    assert result.metadata["decision"] == "bilingual"
    assert [c.text for c in result.source_cues] == EN
    assert [c.original_text for c in result.source_cues] == EN
    assert [c.translation for c in result.source_cues] == ZH
    assert [(c.start, c.end) for c in result.source_cues] == TIMES
    assert result.metadata == sources.plan_subtitle_sources(video, PAIR).metadata
    assert EN[0] not in json.dumps(result.metadata)


def test_separate_ass_styles_same_times_and_signs(media):
    video, _ = media
    side = video.with_suffix(".ass")
    # Reversed position and language-agnostic style names.
    rows = EN + ZH + ["This is a sign"]
    side.write_bytes(captions(rows, "ass", times=TIMES+TIMES+[(0, 12)],
                              styles=["Small"]*3+["Large"]*3+["Signs"]))
    result = sources.plan_subtitle_sources(video, PAIR)
    assert result.metadata["decision"] == "bilingual"
    assert [c.text for c in result.source_cues] == EN
    assert [c.translation for c in result.source_cues] == ZH


@pytest.mark.parametrize("fmt", ["srt", "vtt", "ass", "ssa"])
def test_learning_import_filters_nondialogue_and_keeps_raw_playback(media, fmt):
    from tingmu.media import load_subtitles

    video, _ = media
    side = video.with_suffix("." + fmt)
    lines = ["[door slams] I know you.\n我认识你。", "♪ Song lyrics ♪", "SIGN: OPEN", "Please press [Enter]."]
    side.write_bytes(captions(lines, fmt, times=[(1, 3), (4, 5), (6, 8), (9, 11)]))
    raw = load_subtitles(side)
    assert len(raw) == 4 and "Song lyrics" in raw[1].text
    evidence = {}
    cues = sources.load_learning_subtitles(side, PAIR, metadata_out=evidence)
    assert [c.text for c in cues] == ["I know you.", "Please press [Enter]."]
    assert [(c.id, c.start, c.end) for c in cues] == [(r.id, r.start, r.end) for r in (raw[0], raw[-1])]
    assert "[door slams]" in cues[0].original_text and "我认识你。" in cues[0].original_text
    assert evidence["provided_translations"] == {cues[0].id: "我认识你。"}
    assert evidence["subtitle_import"]["events"][cues[-1].id]["event_index"] == 3
    assert evidence["subtitle_import"]["filtered_event_count"] == 2


def test_explicit_language_spans_for_custom_pair_and_embedded_source_evidence(media, monkeypatch):
    video, info = media
    side = video.with_suffix(".vtt")
    side.write_text("WEBVTT\n\n00:00:00.000 --> 00:00:03.000\n<lang nl>Vlinders</lang> | <lang sv>Fjärilar</lang>\n", "utf-8")
    row, = sources.load_learning_subtitles(side, LanguagePair(source="sv", target="nl"))
    assert (row.text, row.translation) == ("Vlinders", "Fjärilar")
    side.unlink()
    info["streams"].append(stream(9, "ass"))
    raw = captions(EN + ZH + ["Do not teach this sign"], "ass", times=TIMES+TIMES+[(0, 11)],
                   styles=["Main"]*3 + ["Other"]*3 + ["Signs"])
    monkeypatch.setattr(sources, "_extract", lambda *_: (raw, "ass"))
    plan = sources.plan_subtitle_sources(video, PAIR)
    assert [c.translation for c in plan.source_cues] == ZH
    assert plan.import_source["stream_index"] == 9 and plan.import_source["path"] == str(video)
    event = plan.import_source["events"][plan.source_cues[1].id]
    assert event["event_index"] == 1 and event["translation_event"]["event_index"] == 4


@pytest.mark.parametrize("changed", [False, True])
def test_legacy_import_recovers_only_original_text_and_preserves_linked_id(media, changed):
    from tingmu.media import load_subtitles
    from tingmu.models import StudyDocument

    video, _ = media
    path = video.with_suffix(".ass")
    path.write_bytes(captions([r"{\i1}I know you.{\i0}"+"\n我认识你。", "♪ Credits"], "ass", times=TIMES[:2]))
    original = load_subtitles(path)
    doc = StudyDocument(str(video), PAIR, original, metadata={"subtitle_path": str(path)})
    if changed:
        path.write_bytes(captions(["This is replacement text."], "ass", times=TIMES[:1]))
    sources.project_legacy_import(doc, protected_ids=[original[0].id])
    row, = doc.cues
    assert (row.id, row.start, row.end, row.original_text) == (original[0].id, 1, 3, original[0].original_text)
    assert (row.text, row.translation) == ("I know you.", "我认识你。")
    assert doc.metadata["raw_display_text"] == {row.id: row.text}
    assert doc.metadata["subtitle_import"]["evidence"] == ("saved_raw_events" if changed else "matched_file")


def test_ambiguous_legacy_multiline_requests_reimport_atomically(media):
    from tingmu.models import StudyDocument

    video, _ = media
    before = [Cue("a", 0, 1, "unknown\nqxxx")]
    doc = StudyDocument(str(video), PAIR, before, metadata={"subtitle_path": str(video.with_suffix(".srt"))})
    with pytest.raises(ValueError, match="重新导入"):
        sources.project_legacy_import(doc)
    assert doc.cues == before and "subtitle_import" not in doc.metadata


def test_positioned_dialogue_and_unmarked_moving_signs(media):
    video, _ = media
    path = video.with_suffix(".ass")
    rows = [r"{\pos(120,40)}Central Station", r"{\move(50,80,500,80)}大埔墟",
            r"{\pos(20,200)}Where are you?", r"{\k15}Singing along"]
    path.write_bytes(captions(rows, "ass", times=[(0, 3), (3, 6), (6, 9), (9, 11)]))
    result = sources.load_learning_subtitles(path, PAIR)
    assert [r.text for r in result] == ["Where are you?"]
    assert r"\pos" in result[0].original_text


def test_uncertain_bilingual_rows_never_mix_unknown_lanes_into_dialogue(media):
    video, _ = media
    path = video.with_suffix(".srt")
    path.write_bytes(captions(["I know you.\n我认识你。\nBonjour merci.", "We can\naccelerate.",
                               "Where are you? | Qxxzzy"], times=TIMES))
    assert [(r.text, r.translation) for r in sources.load_learning_subtitles(path, PAIR)] == [
        ("I know you.", ""), ("We can\naccelerate.", ""), ("Where are you?", "")]
    path.write_bytes(captions(["Qxxzzy\nFrrrgx"], times=TIMES[:1]))
    with pytest.raises(ValueError, match="重新导入"):
        sources.load_learning_subtitles(path, PAIR)


@pytest.mark.parametrize(("code", "line"), [("ar", "المكتبة"), ("uk", "Книгарня"), ("hi", "पुस्तकालय")])
def test_configured_distinct_scripts_preserve_non_latin_translation(media, code, line):
    path = media[0].with_suffix(".srt")
    path.write_bytes(captions(["I know you.\n" + line], times=TIMES[:1]))
    cue, = sources.load_learning_subtitles(path, LanguagePair(source=code, target="en"))
    assert (cue.text, cue.translation) == ("I know you.", line)


def test_filename_or_chinese_characters_alone_cannot_prove_spoken_cantonese(media):
    video, info = media
    info["streams"][0]["tags"]["language"] = "yue"
    audio = LanguagePair(source="zh-CN", target="yue")
    side = video.with_suffix(".yue.srt")
    side.write_bytes(captions(["我的關係很好。", "我在这里。", "我们回家吧。"]))
    result = sources.plan_subtitle_sources(video, audio)
    assert result.metadata["decision"] == "reference"
    assert not result.source_cues
    assert len(result.reference_cues) == 3
    side.write_bytes(captions(["你喺邊度？", "我唔知。", "佢哋返咗屋企。"]))
    result = sources.plan_subtitle_sources(video, audio)
    assert result.metadata["decision"] == "source"
    assert not result.reference_cues


def test_mislabeled_latin_and_same_script_pair(media):
    video, _ = media
    side = video.with_suffix(".en.srt")
    french = ["Bonjour, je suis là.", "Merci pour le livre.", "Nous allons dans la rue."]
    side.write_bytes(captions(french))
    assert sources.plan_subtitle_sources(video, PAIR).metadata["decision"] == "asr"
    pair = LanguagePair(source="fr", target="en")
    assert sources.plan_subtitle_sources(video, pair).metadata["decision"] == "reference"
    side.write_bytes(captions([a+"\n"+b for a,b in zip(EN,french)]))
    assert [c.translation for c in sources.plan_subtitle_sources(video, pair).source_cues] == french
    assert sources.plan_subtitle_sources(video, LanguagePair(source="en", target="en")).metadata["decision"] == "asr"


def test_partial_forced_wrong_episode_duration_and_selected_audio(media):
    video, info = media
    wrong = video.parent / "Example S01E03.en.srt"
    wrong.write_bytes(captions(EN))
    assert sources.discover_sidecars(video) == []
    result = sources.plan_subtitle_sources(video, PAIR, wrong)
    assert "集数不符" in "".join(result.metadata["notes"])
    side = video.with_suffix(".srt")
    side.write_bytes(captions(EN, times=[(1, 3),(4, 5),(12, 22)]))
    assert "时间超出" in sources.plan_subtitle_sources(video, PAIR).metadata["candidates"][0]["reason"]
    side.write_bytes(captions(EN, times=[(1, 2),(2, 3),(3, 4)]))
    assert sources.plan_subtitle_sources(video, PAIR).metadata["decision"] == "asr"
    side.rename(video.with_suffix(".forced.srt"))
    assert "强制" in sources.plan_subtitle_sources(video, PAIR).metadata["candidates"][0]["reason"]
    side.write_bytes(captions(EN))
    info["streams"].append({"index": 2, "codec_type": "audio", "tags": {"language": "deu"}})
    result = sources.plan_subtitle_sources(video, PAIR, audio_track=1)
    assert not result.source_cues and "音轨" in "".join(result.metadata["notes"])
    info["streams"][1]["tags"] = {"language": "eng", "title": "Director Commentary"}
    assert sources.plan_subtitle_sources(video, PAIR, audio_track=1).metadata["decision"] == "asr"


def test_bitmap_and_forced_skipped_multiple_text_streams_verified_by_content(media, monkeypatch):
    video, info = media
    info["streams"].extend([stream(2, "dvd_subtitle"), stream(3, "ass", forced=1),
                            stream(4, "subrip", "chi"), stream(5, "mov_text", "eng")])
    def extract(path, selected, stop):
        assert selected["index"] in {4, 5}
        return captions(EN if selected["index"] == 4 else ZH), "srt"
    monkeypatch.setattr(sources, "_extract", extract)
    result = sources.plan_subtitle_sources(video, PAIR)
    assert result.metadata["source_id"] == "stream:4"
    assert result.metadata["reference_id"] == "stream:5"
    assert [c.translation for c in result.source_cues] == ZH
    assert result.metadata["candidates"][0]["codec"] == "dvd_subtitle"


def test_conflicting_candidates_and_mistranslation_are_not_imported(media):
    video, _ = media
    a, b = video.with_suffix(".srt"), video.with_suffix(".vtt")
    a.write_bytes(captions(EN))
    b.write_bytes(captions(list(reversed(EN)), "vtt"))
    assert sources.plan_subtitle_sources(video, PAIR).metadata["decision"] == "asr"
    assert sources.plan_subtitle_sources(video, PAIR, a).metadata["decision"] == "source"
    b.write_bytes(captions(ZH, "vtt", times=[(0, 1),(4, 5),(8, 10)]))
    result = sources.plan_subtitle_sources(video, PAIR)
    assert result.metadata["decision"] == "source" and not result.reference_cues
    assert all(not c.translation for c in result.source_cues)


def test_shared_references_or_interleaved_foreign_cues_never_become_spoken_text(media):
    video, _ = media
    refs = [Cue("r", 1, 3, "译文")]
    assert not sources.align_references([Cue("a", 1, 3, "one"), Cue("b", 1, 3, "two")], refs)
    side = video.with_suffix(".srt")
    side.write_bytes(captions([EN[0], ZH[1], EN[2]]))
    assert not sources.plan_subtitle_sources(video, PAIR).source_cues


def test_limits_cancellation_and_failed_probe(media, monkeypatch):
    video, _ = media
    side = video.with_suffix(".srt")
    side.write_bytes(b"A" * (sources.MAX_BYTES + 1))
    assert not sources.plan_subtitle_sources(video, PAIR).source_cues
    with pytest.raises(InterruptedError):
        sources.plan_subtitle_sources(video, PAIR, cancelled=lambda: True)
    side.write_bytes(captions(EN))
    def broken(*args):
        raise ValueError("bad probe")
    monkeypatch.setattr(sources, "_probe", broken)
    assert sources.plan_subtitle_sources(video, PAIR).metadata["decision"] == "asr"


def test_actual_embedded_subtitle_decode(tmp_path, ffmpeg):
    tracks = []
    for name, lines in (("a", EN), ("b", ZH)):
        path = tmp_path / f"{name}.srt"
        path.write_bytes(captions(lines))
        tracks.extend(["-i", str(path)])
    media = tmp_path / "two-tracks.mkv"
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "anullsrc=d=12",
                    *tracks, "-map", "0:a", "-map", "1:s", "-map", "2:s", "-c:a", "aac", "-c:s", "srt",
                    "-metadata:s:a:0", "language=eng", str(media)], check=True)
    plan = sources.plan_subtitle_sources(media, PAIR)
    assert [c.translation for c in plan.source_cues] == ZH
    assert [c.text for c in plan.source_cues] == EN
    assert plan.metadata["paired_count"] == 3


def test_acoustic_hints_allow_split_and_merged_spans_without_publication():
    source = [Cue("a", 1, 3, "one"), Cue("b", 3, 5, "two"), Cue("c", 6, 9, "three"),
              Cue("d", 22, 24, "outside")]
    refs = [Cue("x", 1, 5, "译一"), Cue("y", 6, 7.4, "译二"), Cue("z", 7.5, 9, "译三")]
    assert sources.align_reference_hints(source, refs) == {"a": "译一", "b": "译一", "c": "译二\n译三"}
    assert not sources.align_references(source, refs)
    assert all(not c.translation for c in source)
    assert not sources.align_reference_hints(source[:1], [refs[0], Cue("alt", 1, 5, "另一个人")])
    assert not sources.align_reference_hints(source[:1], [Cue("long", 0, 2, "字"*361)])


def test_sparse_translations_accepted_only_with_local_timing_match(media):
    video, _ = media
    video.with_suffix(".en.srt").write_bytes(captions(EN))
    video.with_suffix(".zh.srt").write_bytes(captions([ZH[1]], times=[TIMES[1]]))
    result = sources.plan_subtitle_sources(video, PAIR)
    assert [c.translation for c in result.source_cues] == ["", ZH[1], ""]
    assert result.metadata["paired_count"] == 1
    assert any(r.get("reference_partial") for r in result.metadata["candidates"])


def test_moving_media_and_sidecars_keeps_identity_and_cue_links(media, tmp_path):
    import shutil
    video, _ = media
    side = video.with_suffix(".srt")
    side.write_bytes(captions(EN))
    before = sources.plan_subtitle_sources(video, PAIR)
    moved = tmp_path / "moved"
    moved.mkdir()
    for p in (video, side):
        shutil.copy2(p, moved / p.name)
    after = sources.plan_subtitle_sources(moved / video.name, PAIR)
    assert before.metadata == after.metadata
    assert [c.id for c in before.source_cues] == [c.id for c in after.source_cues]
    assert str(tmp_path) not in json.dumps(before.metadata)


def test_directory_links_are_not_automatically_discovered(media, tmp_path):
    video, _ = media
    video.with_suffix(".srt").write_bytes(captions(EN))
    import os
    alias = tmp_path / "linked"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(video.parent)],
                       check=True, capture_output=True)
    else:
        alias.symlink_to(video.parent, target_is_directory=True)
    try:
        assert not sources.discover_sidecars(alias / video.name)
        assert not sources.plan_subtitle_sources(alias / video.name, PAIR).source_cues
    finally:
        if os.name == "nt":
            alias.rmdir()
        else:
            alias.unlink()


def test_inline_language_separator_and_single_vtt_speaker(media):
    video, _ = media
    side = video.with_suffix(".vtt")
    payload = captions([e+"|"+z for e,z in zip(EN, ZH)], "vtt").decode()
    # Insert a WebVTT voice span without treating its contents as dialogue.
    payload = payload.replace(EN[0]+"|"+ZH[0], "<v Ann>"+EN[0]+"|"+ZH[0]+"</v>")
    side.write_text(payload, encoding="utf-8")
    plan = sources.plan_subtitle_sources(video, PAIR)
    assert [c.text for c in plan.source_cues] == EN
    assert [c.translation for c in plan.source_cues] == ZH
    assert plan.source_cues[0].speaker == "Ann"
