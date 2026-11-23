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


def test_actual_embedded_subtitle_decode(tmp_path):
    try:
        ffmpeg = sources.decoder._binary("ffmpeg")
        sources.decoder._binary("ffprobe")
    except ValueError:
        pytest.skip("FFmpeg unavailable")
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
