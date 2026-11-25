"""Measured progress events that also work with the existing text callbacks."""

import math
import time


class Progress(str):
    def __new__(cls, message, **details):
        event = super().__new__(cls, message)
        event.details = details
        return event


class TransferMeter:
    """Throttle UI events and smooth network throughput independently of file bytes."""

    smoothing_seconds = 2.5

    def __init__(self, component, report):
        self.component = component
        self.report = report
        self.previous_time = time.perf_counter()
        self.previous_bytes = 0
        self.rate = 0.0
        self.last_activity = self.previous_time
        self.emitted = False
        self.completed = self.total = self.received = 0
        self.context = {}

    def _report(self, stage):
        eta = None
        if stage == "download" and self.total > self.completed and self.rate > 0:
            eta = math.ceil((self.total - self.completed) / self.rate)
        self.report(Progress(f"下载 {self.component}" if stage == "download" else self._labels[stage],
                             component=self.component, stage=stage, completed=float(self.completed),
                             total=float(self.total), received=float(self.received), rate=self.rate,
                             eta=eta, **self.context))

    _labels = {"connecting": "正在连接下载源", "checking": "正在检查本地文件", "waiting": "正在等待其他安装",
               "verifying": "正在校验文件", "installing": "正在安装文件", "testing": "正在自检",
               "ready": "已就绪", "cancelling": "正在取消…"}

    def stage(self, stage, **context):
        self.context.update(context)
        self.rate = 0.0
        self.previous_time = self.last_activity = time.perf_counter()
        self.previous_bytes = self.received
        self._report(stage)

    def __call__(self, done, total):
        """Runtime byte callbacks predate structured events. Both forms share this meter."""
        self.update(done, total, done, force=done == total)

    def update(self, completed, total, received, *, force=False):
        now = time.perf_counter()
        interval = now - self.previous_time
        first_bytes = received > 0 and not self.previous_bytes
        if self.emitted and interval < 0.25 and not (force or first_bytes):
            return
        delta = max(0, received - self.previous_bytes)
        elapsed = now - self.last_activity
        if delta and elapsed > 0:
            # Empty polls should not shorten the sampling period for the next chunk.
            instant = delta / elapsed
            weight = 1 - math.exp(-elapsed / self.smoothing_seconds)
            self.rate = self.rate + weight * (instant - self.rate) if self.rate else instant
            self.last_activity = now
        elif not self.emitted:
            self.last_activity = now
        elif elapsed >= self.smoothing_seconds:
            self.rate = 0.0
        self.completed, self.total, self.received = completed, total, received
        self._report("download")
        self.previous_time, self.previous_bytes = now, received
        self.emitted = True


def batch_progress(index, start, end, count, *, status="running", done=0, repair=False):
    label = f"第 {start}–{end} 句" if start != end else f"第 {start} 句"
    return Progress(label, batch={"id": index, "label": label, "total": count, "done": done,
                                  "status": status, "repair": repair})
