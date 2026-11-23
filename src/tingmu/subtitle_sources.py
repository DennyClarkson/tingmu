"""Local subtitle evidence for preparation. Subtitle text is data, never a prompt."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import hashlib
import html
import json
import math
from pathlib import Path
import re
import time
import unicodedata

import pysubs2

from . import embedded_subtitles as decoder
from .media import SUBTITLE_EXTENSIONS, TranscriptionCancelled, _cue_id
from .models import Cue, LanguagePair
from .storage import media_fingerprint


VERSION = 1
MAX_BYTES = 4 * 1024 * 1024
MAX_EVENTS = 18000
MAX_INPUTS = 12
MAX_STREAMS = 128
TEXT_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text", "text", "microdvd"}
EPISODE = re.compile(r"(?<![\da-z])(?:s(\d{1,3})[ ._-]*e(\d{1,3})|(\d{1,2})x(\d{1,3}))(?!\d)", re.I)
AUXILIARY = re.compile(r"\b(?:forced|signs?|songs?|commentary|lyrics|karaoke|partial|trailer)\b|强制|強制|注释|註釋|仅外语|僅外語", re.I)
NON_DIALOGUE = re.compile(r"\b(?:signs?|credits?|title|typeset|karaoke|op|ed|song|lyrics)\b|特效|片头|片尾|片頭|字幕组|字幕組", re.I)
CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
KANA = re.compile(r"[ぁ-ヿ]")
HANGUL = re.compile(r"[가-힣]")
YUE = re.compile(r"[嘅咗啲喺唔冇佢哋咩嘢嚟嗰喎噉咁畀乜]|係咪|我係|就係|邊度|點解|而家|點樣")
TOKENS = re.compile(r"[^\W\d_]+", re.UNICODE)
# Positive lexical evidence. A Latin alphabet, filename suffix or track language alone
# cannot establish the language, especially for pairs that share a script.
WORDS = {lang: set(words.split()) for lang, words in {
    "en": "the a an is are was were be been being to of with for and have has had that this these those i i'm we you he she they my your his her their it it's don't didn't can't can could would should will what who when where why how not yes no hello thanks thank please hey good going come here there let's just know need want very from in on me do did go all right again never home wait stop help now time back really ready look morning today tomorrow",
    "fr": "je tu il elle nous vous ils elles le la les un une des est êtes sont ai avec dans pour pas que qui pourquoi oui non merci bonjour salut mon ton suis très c'est qu'il mais bien allons",
    "de": "ich du er sie wir ihr der die das den ist sind bin mit und nicht auch wie was wer wo bitte danke hallo guten morgen nein ja ein eine mein dein geht von",
    "es": "yo tú usted nosotros ustedes ellos ellas el la los las un una soy es está son que qué con por para pero sí no hola gracias tengo estás vamos dónde cómo buenos días hasta",
    "it": "io tu noi voi loro il lo gli una sono sei che per non grazie ciao buongiorno perché come questa questo",
    "pt": "eu você nós vocês ele ela os as um uma é são não sim obrigado obrigada olá bom dia com por para meu minha",
    "ru": "я ты вы он она мы они это что как для нет да спасибо привет здесь почему где",
}.items()}
LANG_ALIASES = {"eng": "en", "zho": "zh", "chi": "zh", "cmn": "zh", "jpn": "ja", "kor": "ko",
                "spa": "es", "fra": "fr", "fre": "fr", "deu": "de", "ger": "de", "ita": "it",
                "por": "pt", "rus": "ru", "cantonese": "yue", "zh-hk": "yue"}


@dataclass
class SubtitlePlan:
    source_cues: list[Cue]
    reference_cues: list[Cue]
    metadata: dict


@dataclass
class _Track:
    record: dict
    source: list[Cue] = field(default_factory=list)
    reference: list[Cue] = field(default_factory=list)


def _lang(code):
    value = str(code or "").casefold().replace("_", "-")
    return LANG_ALIASES.get(value, LANG_ALIASES.get(value.split("-")[0], value.split("-")[0]))


def _title(stem):
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", stem).casefold()))


def _episode(stem):
    match = EPISODE.search(stem)
    if match:
        return (_title(stem[:match.start()]), int(match[1] or match[3]), int(match[2] or match[4]))
    return None


def _same_title(media, sub):
    video, caption = _title(media.stem), _title(sub.stem)
    a, b = _episode(media.stem), _episode(sub.stem)
    if a and b and (a != b or a[0]):
        return a == b
    if caption == video:
        return True
    if not caption.startswith(video + " "):
        return False
    suffix = caption[len(video):].strip()
    return bool(re.fullmatch(r"(?:(?:en|eng|english|zh|chi|zho|cn|tw|hk|hans|hant|chs|cht|"
                             r"yue|ja|jpn|ko|kor|fr|fra|de|es|it|pt|ru|us|gb|sdh|cc|"
                             r"forced|signs|简英|繁英|中英|简体|繁体|英文|中文|双语|雙語|简|繁)\s*)+", suffix))


def discover_sidecars(media: Path) -> list[Path]:
    """Inspect names only; never use an unrelated episode or all subtitles in the folder."""
    media = Path(media)
    try:
        if any(p.is_symlink() or p.is_junction() for p in (media, *media.parents)):
            return []
        candidates = [p for p in media.parent.iterdir() if p.suffix.lower() in SUBTITLE_EXTENSIONS
                      and _same_title(media, p) and not p.is_symlink() and not p.is_junction() and p.is_file()]
    except OSError:
        return []
    return sorted(candidates, key=lambda p: (p.stem.casefold() != media.stem.casefold(), p.name.casefold()))[:MAX_INPUTS]


class _Stop:
    def __init__(self, cancelled):
        self.cancelled = cancelled

    def is_set(self):
        if self.cancelled and self.cancelled():
            raise TranscriptionCancelled("已取消检查字幕来源")
        return False

    def wait(self, delay):
        time.sleep(delay)
        return self.is_set()


def _probe(media, stop):
    with decoder._scratch() as folder:
        result = decoder._run([decoder._binary("ffprobe"), "-v", "error", "-protocol_whitelist", "file,pipe",
                               "-show_entries", "format=duration:stream=index,codec_type,codec_name:"
                               "stream_tags=language,title:stream_disposition=forced,comment",
                               "-of", "json", str(media)], Path(folder), stop)
    return json.loads(result)


def _extract(media, stream, stop):
    fmt = "ass" if stream.get("codec_name") in {"ass", "ssa"} else "srt"
    with decoder._scratch() as folder:
        data = decoder._run([decoder._binary("ffmpeg"), "-nostdin", "-v", "error", "-protocol_whitelist",
                             "file,pipe", "-i", str(media), "-map", f"0:{int(stream['index'])}",
                             "-c:s", fmt, "-f", fmt, "pipe:1"], Path(folder), stop)
    return data, fmt


def _decode(data):
    encodings = ("utf-16",) if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig", "gb18030", "big5")
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError("字幕编码无法识别")


def _identify(line, languages):
    if CJK.search(line) and len(re.findall(r"[A-Za-z]{2,}", line)) >= 2:
        return ""
    if KANA.search(line):
        return "ja"
    if HANGUL.search(line):
        return "ko"
    if CJK.search(line):
        if YUE.search(line):
            return "yue"
        # Han-only lines can also be Japanese. Avoid attributing them to Chinese
        # without independent evidence when the two are being compared.
        if "ja" in languages and not re.search(r"[你们們这這吗嗎的了个個说說么麼]", line):
            return ""
        return "zh"
    words = set(TOKENS.findall(line.replace("’", "'").casefold()))
    scores = {lang: len(words & lexicon) for lang, lexicon in WORDS.items()}
    best = max(scores.values())
    winners = [lang for lang, score in scores.items() if score == best]
    return winners[0] if best and len(winners) == 1 else ""


def _parts(event):
    if (event.is_comment or event.is_drawing or event.end <= event.start or event.start < 0
            or NON_DIALOGUE.search(event.style.replace("_", " ")) or re.search(r"\\(?:pos|move|clip)\(", event.text)):
        return []
    lines = []
    for raw in event.plaintext.splitlines():
        line = html.unescape(raw).strip()
        if not line or re.fullmatch(r"(?:\[[^\]]*\]|【[^】]*】|\([^)]*\)|[♪♫\s])+[.!?]?", line):
            continue
        if not any(c.isalnum() for c in line):
            continue
        # Preserve punctuation in ordinary dialogue. Only split a separator if
        # both sides carry independent evidence for different languages.
        chunks = re.split(r"\s*[|｜]\s*|\s+/\s+", line)
        if len(chunks) > 1:
            codes = [_identify(chunk, set()) for chunk in chunks]
            if any(not code for code in codes) or len(set(codes)) < 2:
                chunks = [line]
        lines.extend(part.strip() for part in chunks if part.strip())
    return lines


def _sufficient(cues, duration):
    if not duration:
        return False
    if len(cues) < max(1 if duration <= 12 else 2, round(duration / 40)):
        return False
    if cues[0].start > max(4, duration * .24) or max(c.end for c in cues) < duration * .76:
        return False
    frontier = 0.0
    for cue in cues:
        if cue.start - frontier > max(25, duration * .24):
            return False
        frontier = max(frontier, cue.end)
    return True


def align_references(cues: list[Cue], references: list[Cue]) -> dict[str, str]:
    """Attach only unambiguous reciprocal one-to-one subtitle timing matches."""
    refs = sorted(references, key=lambda cue: cue.start)
    starts = [r.start for r in refs]
    possible = {}
    uses = Counter()
    for cue in cues:
        # Ignore unusually long events (titles/credits); bounded search even on a malicious track.
        if not 0 < cue.end - cue.start <= 35:
            continue
        lo, hi = bisect_left(starts, cue.start - 35), bisect_right(starts, cue.end)
        if hi - lo > 150:
            continue
        matches = []
        for idx in range(lo, hi):
            ref = refs[idx]
            overlap = min(cue.end, ref.end) - max(cue.start, ref.start)
            if overlap > 0 and overlap >= .68 * max(cue.end - cue.start, ref.end - ref.start):
                matches.append(idx)
                uses[idx] += 1
        if len(matches) == 1:
            possible[cue.id] = matches[0]
    return {key: refs[idx].text for key, idx in possible.items() if uses[idx] == 1}


def align_reference_hints(cues: list[Cue], references: list[Cue]) -> dict[str, str]:
    """Context for a text model only. Acoustic and caption segmentation can differ."""
    refs = sorted(references, key=lambda cue: cue.start)
    starts = [r.start for r in refs]
    hints = {}
    for cue in cues:
        duration = cue.end - cue.start
        if not 0 < duration <= 35:
            continue
        lo, hi = bisect_left(starts, cue.start - 20), bisect_left(starts, cue.end)
        if hi - lo > 100:
            continue
        overlaps = []
        for ref in refs[lo:hi]:
            span = ref.end - ref.start
            overlap = min(cue.end, ref.end) - max(cue.start, ref.start)
            if 0 < span <= 20 and overlap >= .55 * min(duration, span):
                overlaps.append((ref, overlap))
        if not 1 <= len(overlaps) <= 3 or sum(n for _, n in overlaps) < .45 * duration:
            continue
        if any(previous.end > current.start + .15 for (previous, _), (current, _) in zip(overlaps, overlaps[1:])):
            continue  # Simultaneously visible alternatives or concurrent speakers.
        hint = "\n".join(ref.text for ref, _ in overlaps)
        if 0 < len(hint) <= 360:
            hints[cue.id] = hint
    return hints


def _analyze(data, fmt, track, language, duration, stop):
    if len(data) > MAX_BYTES:
        raise ValueError("字幕文件超过读取上限")
    decoded = _decode(data)
    events = pysubs2.SSAFile.from_string(decoded, format_=fmt)
    if len(events) > MAX_EVENTS:
        raise ValueError("字幕条目超过读取上限")
    if fmt == "vtt" and re.search(r"<v[.\s]", decoded, re.I):
        tagged = pysubs2.SSAFile.from_string(decoded, format_="vtt", keep_html_tags=True)
        for event, raw in zip(events, tagged):
            # A cue with multiple voice tags has no single speaker.
            if len(re.findall(r"<v[.\s]", raw.text, re.I)) == 1:
                voice = re.fullmatch(r"\s*<v(?:\.[^\s>]+)*\s+([^>]+)>[^<]*(?:</v>)?\s*", raw.text, re.I)
                if voice:
                    event.name = html.unescape(voice[1].strip())
    target, known = _lang(language.target), _lang(language.source)
    rows = []
    lanes = defaultdict(Counter)
    for index, event in enumerate(events):
        if index % 128 == 0:
            stop.is_set()
        lines = _parts(event)
        if not lines:
            continue
        if len(lines) > 24 or any(len(line) > 4096 for line in lines):
            raise ValueError("字幕行过长")
        parts = []
        for i, line in enumerate(lines):
            lane = (event.style, len(lines), i)
            identified = _identify(line, {target, known})
            lanes[lane][identified] += 1
            parts.append((line, lane, identified))
        rows.append((index, event, parts))
    record = track.record
    record.update(events=len(rows), sha256=hashlib.sha256(data).hexdigest())
    if not rows:
        record["reason"] = "未找到可读取的对白"
        return
    if not duration:
        record["reason"] = "无法核对媒体时长"
        return
    if max(event.end for _, event, _ in rows) / 1000 > duration + max(1.5, min(10, duration * .025)):
        record["reason"] = "字幕时间超出视频，可能为不同剪辑"
        return
    missing = 0
    for index, event, parts in rows:
        if index % 128 == 0:
            stop.is_set()
        text = defaultdict(list)
        unresolved = False
        for line, lane, code in parts:
            votes = lanes[lane]
            dominant, count = votes.most_common(1)[0]
            # A subtitle style/line position can establish the role of numbers,
            # names and ambiguous single words. Its text must independently
            # establish a consistent language first, and the script must fit.
            words = TOKENS.findall(line)
            has_latin = any("LATIN" in unicodedata.name(c, "") for c in line)
            consistent = (dominant and count >= 3 and count >= .67 * votes.total()
                          and count >= .97 * (votes.total() - votes[""]))
            ambiguous_word = count >= 20 and len(words) == 1 and code in WORDS and code not in {known, target}
            if consistent and (not code or ambiguous_word):
                if not words or (dominant in WORDS and dominant != "ru" and has_latin and line.isascii()
                                 and len(words) <= (12 if len(parts) == 2 else 3)):
                    code = dominant
            if code:
                text[code].append(line)
            else:
                unresolved = True
        if unresolved or any(code not in {target, known} for code in text):
            missing += 1
        start, end = event.start / 1000, event.end / 1000
        for code, output in ((target, track.source), (known, track.reference)):
            if code not in text or (output is track.reference and known == target):
                continue
            content = "\n".join(text[code])
            identity = f"{record['id']}:{record['sha256']}:{code}"
            cue = Cue(_cue_id(identity, index, start, end), start, end, content,
                      speaker=event.name.strip())
            if output is track.source and known != target and known in text:
                cue.translation = "\n".join(text[known])
            output.append(cue)
    track.source.sort(key=lambda c: (c.start, c.end))
    track.reference.sort(key=lambda c: (c.start, c.end))
    record.update(source_count=len(track.source), reference_count=len(track.reference), uncertain_events=missing)
    if track.source:
        attached = align_references(track.reference, track.source)
        paired = sum(bool(c.translation) for c in track.source)
        # Separate styled ASS translation events need not share an event index.
        missing_source = len(rows) - len(track.source) - max(0, len(attached) - paired)
        if missing or missing_source > 0 or not _sufficient(track.source, duration):
            track.source.clear()
            record["reason"] = "原文语种或对白覆盖不完整，需语音识别"
    if track.reference and not _sufficient(track.reference, duration):
        record["reference_partial"] = True
        record.setdefault("reason", "仅部分时间有译文参考")
    if not track.source and not track.reference:
        record.setdefault("reason", "文本语种证据不足")


def _mismatched_episode(media, sub):
    a, b = _episode(media.stem), _episode(sub.stem)
    if a and b and a[1:] != b[1:]:
        return f"集数不符（视频 S{a[1]:02}E{a[2]:02}，字幕 S{b[1]:02}E{b[2]:02}）"
    if a and b and a[0] and b[0] and a[0] != b[0]:
        return "剧名不符"
    return ""


def _read_sidecar(path):
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("字幕文件超过读取上限")
    return data


def _scan_track(track, read, language, duration, stop):
    try:
        data, fmt = read()
        _analyze(data, fmt, track, language, duration, stop)
    except InterruptedError:
        raise
    except (OSError, ValueError, pysubs2.exceptions.Pysubs2Error, OverflowError):
        track.source.clear()
        track.reference.clear()
        track.record["reason"] = "字幕解码失败或超过读取上限"


def _choose(tracks, attr, explicit_id):
    available = [t for t in tracks if getattr(t, attr)]
    if not available:
        return None, False

    def rank(track):
        return (track.record["id"] == explicit_id, bool(track.source and track.reference),
                not track.record.get("reference_partial", False) if attr == "reference" else True,
                track.record["kind"] == "embedded")

    priority = max(map(rank, available))
    winners = [t for t in available if rank(t) == priority]
    if len(winners) > 1:
        primary = [(c.start, c.end, c.text) for c in getattr(winners[0], attr)]
        if any([(c.start, c.end, c.text) for c in getattr(t, attr)] != primary for t in winners[1:]):
            return None, True
    return winners[0], False


def plan_subtitle_sources(media_path: Path, language: LanguagePair, subtitle_path: Path | None = None,
                          *, cancelled=None, audio_track: int = 0) -> SubtitlePlan:
    """Assess local subtitles against the requested audio, language pair and media timeline."""
    media = Path(media_path).resolve()
    explicit = Path(subtitle_path).resolve() if subtitle_path is not None else None
    stop = _Stop(cancelled)
    stop.is_set()
    tracks, notes = [], []
    duration, streams = 0.0, []
    try:
        info = _probe(media, stop)
        duration = float(info.get("format", {}).get("duration") or 0)
        if not math.isfinite(duration) or duration <= 0:
            duration = 0
        streams = info.get("streams", [])[:MAX_STREAMS]
    except InterruptedError:
        raise
    except (ValueError, OSError):
        notes.append("无法读取媒体轨道或时长")
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    selected_audio = audio[audio_track] if 0 <= audio_track < len(audio) else {}
    tags = selected_audio.get("tags", {})
    spoken = _lang(tags.get("language"))
    target = _lang(language.target)
    blocked = ""
    if not selected_audio:
        blocked = "无法核对所选音轨"
    elif AUXILIARY.search(tags.get("title", "")) or selected_audio.get("disposition", {}).get("comment"):
        blocked = "所选音轨为评论或辅助音轨"
    elif spoken not in {"", "und", "mul", target} and not (spoken == "zh" and target == "yue"):
        blocked = "所选音轨标注的语言与学习语言不同"
    if blocked:
        notes.append(blocked)
    paths = [explicit] if explicit else discover_sidecars(Path(media_path).absolute())
    for path in paths:
        stop.is_set()
        record = {"kind": "sidecar", "id": "file:" + path.name, "name": path.name,
                  "fingerprint": media_fingerprint(path)}
        track = _Track(record)
        tracks.append(track)
        mismatch = _mismatched_episode(media, path)
        descriptor = path.stem.removeprefix(media.stem).replace("_", " ")
        if mismatch:
            record["reason"] = mismatch
        elif path.suffix.lower() not in SUBTITLE_EXTENSIONS:
            record["reason"] = "此文件不是支持的文本字幕"
        elif AUXILIARY.search(descriptor):
            record["reason"] = "强制、片段或辅助字幕"
        else:
            _scan_track(track, lambda: (_read_sidecar(path), path.suffix[1:].lower()), language, duration, stop)
    # Each decode reads only the designated subtitle stream. Bound the number of
    # passes through the media container independently of the number of bitmap tracks.
    decoded = len(paths)
    for stream in streams:
        if stream.get("codec_type") != "subtitle":
            continue
        stop.is_set()
        index = int(stream["index"])
        codec = stream.get("codec_name", "")
        tags = stream.get("tags", {})
        record = {"kind": "embedded", "id": f"stream:{index}", "name": f"内嵌字幕轨 {index}",
                  "index": index, "codec": codec, "language": str(tags.get("language", ""))[:40]}
        track = _Track(record)
        tracks.append(track)
        if codec not in TEXT_CODECS:
            record["reason"] = "图像字幕，需要 OCR" if codec in decoder.BITMAP_CODECS else "未支持的字幕编码"
        elif (stream.get("disposition", {}).get("forced") or stream.get("disposition", {}).get("comment")
              or AUXILIARY.search(tags.get("title", "").replace("_", " "))):
            record["reason"] = "强制或辅助字幕"
        elif decoded >= MAX_INPUTS:
            record["reason"] = "超过本次检查的轨道上限"
        else:
            decoded += 1
            _scan_track(track, lambda: _extract(media, stream, stop), language, duration, stop)
    if explicit and tracks and tracks[0].record.get("reason"):
        notes.append(f"指定字幕：{tracks[0].record['reason']}")
    if tracks and all(t.record.get("codec") in decoder.BITMAP_CODECS for t in tracks):
        notes.append("内嵌轨均为图像字幕，无法提取文字")
    candidates = [] if blocked else tracks
    explicit_id = "file:" + explicit.name if explicit else None
    source, ambiguous_source = _choose(candidates, "source", explicit_id)
    ref, ambiguous_ref = _choose(candidates, "reference", explicit_id)
    if ambiguous_source or ambiguous_ref:
        notes.append("多份同语种字幕内容不同，无法唯一判断")
    sources = source.source if source else []
    if source and source.reference:
        ref = source
    refs = ref.reference if ref else []
    if sources and refs:
        pairs = align_references(sources, refs)
        if ref is not source and len(pairs) < .70 * len(refs):
            notes.append("两份字幕的时间未充分对应，已跳过译文")
            refs, ref = [], None
        else:
            for cue in sources:
                cue.translation = cue.translation or pairs.get(cue.id, "")
    translated_count = sum(bool(c.translation) for c in sources)
    decision = ("bilingual" if translated_count else "source") if sources else "reference" if refs else "asr"
    summary = {"bilingual": "采用学习语言原文和按时间对应的已有译文",
               "source": "采用学习语言字幕作为原文，缺少的译文由后续步骤处理",
               "reference": "已有字幕仅用作译文参考，原文需语音识别",
               "asr": "未找到可直接使用的对白字幕，需语音识别"}[decision]
    metadata = {"version": VERSION, "decision": decision, "summary": summary, "notes": notes,
                "media_fingerprint": media_fingerprint(media), "duration": duration, "audio_track": audio_track,
                "audio_language": spoken, "target": language.target, "known": language.source,
                "source_id": source.record["id"] if source else None, "reference_id": ref.record["id"] if ref else None,
                "source_count": len(sources), "reference_count": len(refs), "paired_count": translated_count,
                "candidates": [track.record for track in tracks]}
    metadata["identity"] = hashlib.sha256(json.dumps(metadata, sort_keys=True).encode()).hexdigest()
    return SubtitlePlan(sources, refs, metadata)
