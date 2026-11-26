"""Bounded, local display of selected fields from an incomplete model response."""
import codecs
from collections import deque
from dataclasses import dataclass
from threading import Lock

TAIL = 160
_TEXT = {"text", "translation", "explanation", "reading", "reason", "meaning", "segments", "segmented_texts"}
_CONTAINERS = {"groups", "cues", "tokens", "segments", "segmented_texts"}
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": " ", "f": " ", "n": " ", "r": " ", "t": " "}


@dataclass(slots=True)
class _Frame:
    kind: str
    expect: str
    name: str
    visible: bool
    key: str = ""
    start: int = -1
    end: int = -1
    has_text: bool = False


class ReadableStream:
    """Scan once, including across UTF-8, JSON escape and surrogate boundaries.

    The parser retains only the current key/short scalar, nesting state and the
    display tail. Unknown subtrees stay opaque, even if they contain a 'text' key.
    """

    def __init__(self):
        self.tail = deque(maxlen=TAIL)
        self.seq = 0
        self.stack = []
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.string = ""
        self.key = ""
        self.escape = False
        self.hex_digits = None
        self.high = 0
        self.scalar = ""
        self.first = False
        self.finished = False
        self.preamble = ""

    def _emit(self, text):
        if self.first and self.seq:
            self.tail.extend(" · ")
            self.seq += 3
        self.first = False
        self.tail.extend(text)
        self.seq += len(text)

    def _character(self, char):
        if self.string == "key":
            if len(self.key) < 48:
                self.key += char
        elif self.string == "visible":
            self._emit(char if char.isprintable() or char == "\u200d" else " ")
            if self.stack[-1].key == "text":
                self.stack[-1].has_text = True

    def _quoted(self, char):
        if self.hex_digits is not None:
            if char not in "0123456789abcdefABCDEF":
                self.finished = True
                return
            self.hex_digits += char
            if len(self.hex_digits) == 4:
                point = int(self.hex_digits, 16)
                self.hex_digits = None
                if self.high and 0xdc00 <= point <= 0xdfff:
                    point = 0x10000 + ((self.high - 0xd800) << 10) + point - 0xdc00
                    self.high = 0
                elif self.high:
                    self._character("\ufffd")
                    self.high = 0
                if 0xd800 <= point <= 0xdbff:
                    self.high = point
                else:
                    self._character(chr(point) if not 0xdc00 <= point <= 0xdfff else "\ufffd")
            return
        if self.escape:
            self.escape = False
            if char == "u":
                self.hex_digits = ""
            elif char in _ESCAPES:
                self._flush_high()
                self._character(_ESCAPES[char])
            else:
                self.finished = True
        elif char == "\\":
            self.escape = True
        else:
            self._flush_high()
            if char == '"':
                if self.string == "key":
                    self.stack[-1].key = self.key
                    self.stack[-1].expect = "colon"
                self.string = ""
            elif ord(char) < 32:
                self.finished = True
            else:
                self._character(char)

    def _flush_high(self):
        if self.high:
            self._character("\ufffd")
            self.high = 0

    def _close(self):
        frame = self.stack.pop()
        if frame.visible and frame.name == "groups" and frame.kind == "{" and not frame.has_text:
            if 0 <= frame.start <= frame.end:
                self.first = True
                self._emit(f"#{frame.start + 1}–#{frame.end + 1}")
        self.finished = not self.stack

    def feed(self, chunk):
        if self.finished:
            return
        if isinstance(chunk, bytes):
            try:
                chunk = self.decoder.decode(chunk)
            except UnicodeDecodeError:
                self.finished = True
                return
        for char in chunk:
            if self.finished:
                return
            if self.string:
                self._quoted(char)
                continue
            if not self.stack:
                if char in "{[" and self.preamble.strip() in ("", "\x60\x60\x60", "\x60\x60\x60json"):
                    self.stack.append(_Frame(char, "key" if char == "{" else "value", "", True))
                elif char.isspace() and not self.preamble:
                    continue
                elif len(self.preamble) < 16:
                    self.preamble += char
                else:
                    self.finished = True
                continue
            frame = self.stack[-1]
            if self.scalar:
                if char not in ",}]" and not char.isspace():
                    if len(self.scalar) < 16:
                        self.scalar += char
                    continue
                if frame.visible and frame.name == "groups" and frame.key in ("start_index", "end_index"):
                    if self.scalar.isascii() and self.scalar.isdecimal() and len(self.scalar) <= 9:
                        if frame.key == "start_index":
                            frame.start = int(self.scalar)
                        else:
                            frame.end = int(self.scalar)
                self.scalar = ""
            if char.isspace():
                continue
            if char == ("}" if frame.kind == "{" else "]") and frame.expect in ("key", "value", "comma"):
                self._close()
            elif char == "," and frame.expect == "comma":
                frame.expect = "key" if frame.kind == "{" else "value"
            elif frame.expect == "key" and char == '"':
                self.key, self.string = "", "key"
            elif frame.expect == "colon" and char == ":":
                frame.expect = "value"
            elif frame.expect == "value":
                name = frame.key if frame.kind == "{" else frame.name
                frame.expect = "comma"
                if char == '"':
                    self.string = "visible" if frame.visible and name in _TEXT else "hidden"
                    self.first = True
                elif char in "{[" and len(self.stack) < 32:
                    visible = frame.visible and (frame.kind == "[" or name in _CONTAINERS)
                    self.stack.append(_Frame(char, "key" if char == "{" else "value", name, visible))
                elif char in "-0123456789tfn":
                    self.scalar = char
                else:
                    self.finished = True
            else:
                self.finished = True


class StudioStreams:
    """One active job, at most 16 bounded lanes. Polled by the visible studio only."""

    def __init__(self):
        self._lock = Lock()
        self._scope = None
        self._key = None
        self._lanes = {}
        self._epoch = 0

    def clear(self):
        with self._lock:
            self._scope = self._key = None
            self._lanes.clear()

    def bind(self, job_id, stage):
        scope = object()
        with self._lock:
            self._scope = scope
            self._key = job_id, stage
            self._lanes.clear()

        def delta(batch_id, content):
            with self._lock:
                lane = self._lanes.get(batch_id)
                if scope is not self._scope or lane is None or not lane.active:
                    return
                if content is None:
                    self._lanes[batch_id] = self._new_lane()
                    return
            with lane.lock:
                lane.reader.feed(content)
        return delta

    def _new_lane(self):
        self._epoch += 1
        return _Lane(self._epoch)

    def sync(self, job_id, stage, slots):
        with self._lock:
            if self._key != (job_id, stage):
                return
            retained = {}
            for slot in slots[:16]:
                ident = slot["id"]
                lane = self._lanes.get(ident)
                if slot["status"] == "running" and (lane is None or not lane.active):
                    lane = self._new_lane()
                if lane is not None:
                    lane.active = slot["status"] == "running"
                    retained[ident] = lane
            self._lanes = retained

    def snapshot(self, job_id, stage):
        with self._lock:
            if self._key != (job_id, stage):
                return {}
            lanes = list(self._lanes.items())
        return {str(ident): lane.sample() for ident, lane in lanes}


class _Lane:
    def __init__(self, epoch):
        self.epoch = epoch
        self.lock = Lock()
        self.reader = ReadableStream()
        self.active = True
        self.published = (0, "")

    def sample(self):
        # An unusually large SSE event can take time to parse. A GUI frame
        # may reuse the last immutable pair instead of waiting for a worker.
        if self.lock.acquire(blocking=False):
            try:
                if self.reader.seq != self.published[0]:
                    self.published = self.reader.seq, "".join(self.reader.tail)
            finally:
                self.lock.release()
        seq, tail = self.published
        return {"stream_epoch": self.epoch, "stream_seq": seq, "stream_tail": tail}
