"""Coordinate embedded captions independently of the Qt video and audio decoder."""

from bisect import bisect_right
from concurrent.futures import CancelledError, ThreadPoolExecutor
import threading

from PySide6.QtCore import QObject, QTimer, Qt, Signal

from tingmu.embedded_subtitles import BitmapTrack, Events, PAGE_MS, SubtitleError, load_page, load_track


class EmbeddedCaptions(QObject):
    changed = Signal()
    _result = Signal(int, object, object, str)

    def __init__(self, parent):
        super().__init__(parent)
        self.track = -1
        self.status = self.text = self.image = ""
        self.bounds = {}
        self._media = ""
        self._position = 0
        self._source = None
        self._pages = {}
        self._failed = {}
        self._serial = 0
        self._pending = None
        self._future = None
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embedded")
        self._result.connect(self._receive, Qt.ConnectionType.QueuedConnection)
        self._debounce = QTimer(self, singleShot=True, interval=50)
        self._debounce.timeout.connect(self._start)

    def _cancel(self):
        self._serial += 1
        self._debounce.stop()
        self._stop.set()
        if self._future is not None:
            self._future.cancel()
        self._future = None
        self._pending = None

    def select(self, media, track, position):
        if self._media == media and self.track == track and not self._failed:
            return
        self._cancel()
        self.track = track
        self._media = media
        self._position = position
        self._source = None
        self._pages.clear()
        self._failed.clear()
        self.status = self.text = self.image = ""
        self.bounds = {}
        if track >= 0:
            self.status = "正在读取内嵌字幕…"
            self._schedule("track")
        self.changed.emit()

    def position(self, value):
        self._position = max(0, value)
        if self._source is not None:
            self._sync()

    def _schedule(self, key):
        if self._pending != key:
            self._cancel()
            self._pending = key
            self._debounce.start()

    def _start(self):
        serial, key = self._serial, self._pending
        self._stop = threading.Event()
        if key == "track":
            future = self._executor.submit(load_track, self._media, self.track, self._stop)
        else:
            future = self._executor.submit(load_page, self._media, self.track, self._source, key, self._stop)
        self._future = future

        def done(f):
            try:
                data = f.result()
                error = ""
            except (InterruptedError, CancelledError):
                return
            except SubtitleError as exc:
                data, error = None, str(exc)
            except Exception:
                data, error = None, "内嵌字幕读取失败，可重新选择字幕轨。"
            self._result.emit(serial, key, data, error)

        future.add_done_callback(done)

    def _receive(self, serial, key, result, error):
        if serial != self._serial:
            return
        self._pending = self._future = None
        if error:
            self._failed[key] = error
        elif key == "track":
            self._source = result
        else:
            self._pages[key] = result
            while len(self._pages) > 2:
                oldest = next(page for page in self._pages if page != self._position // PAGE_MS and page != key)
                del self._pages[oldest]
        self._sync()

    def _sync(self):
        status = self._failed.get("track", "")
        text = image = ""
        bounds = {}
        if isinstance(self._source, Events):
            text = self._source.at(self._position)
        elif isinstance(self._source, BitmapTrack):
            page, offset = divmod(self._position, PAGE_MS)
            if page not in self._pages:
                status = self._failed.get(page, "")
                if not status:
                    status = "正在读取图形字幕…"
                    self._schedule(page)
            else:
                frame = self._pages[page].at(self._position)
                if frame:
                    image, bounds = frame.url, frame.bounds
                more_packets = bisect_right(self._source.packets, self._position) < len(self._source.packets)
                if offset > PAGE_MS - 6000 and (image or more_packets):
                    if page + 1 not in self._pages and page + 1 not in self._failed:
                        self._schedule(page + 1)
        if (self.status, self.text, self.image) != (status, text, image):
            self.status, self.text, self.image = status, text, image
            self.bounds = bounds
            self.changed.emit()

    def close(self):
        self.track = -1
        self._source = None
        self._pages.clear()
        self._cancel()
        self._executor.shutdown(wait=True, cancel_futures=True)
