"""Measured progress events that also work with the existing text callbacks."""

import time


class Progress(str):
    def __new__(cls, message, **details):
        event = super().__new__(cls, message)
        event.details = details
        return event


class TransferMeter:
    """Rate uses bytes newly transferred, independent of restored or deduplicated bytes."""

    def __init__(self, component, report):
        self.component = component
        self.report = report
        self.previous_time = time.monotonic()
        self.previous_bytes = 0
        self.emitted = False

    def update(self, completed, total, received, *, force=False):
        now = time.monotonic()
        interval = now - self.previous_time
        if self.emitted and interval < 0.25 and not force:
            return
        rate = max(0, received - self.previous_bytes) / interval if interval > 0 else 0
        self.report(Progress(f"下载 {self.component}", component=self.component, completed=float(completed),
                             total=float(total), received=float(received), rate=rate))
        self.previous_time, self.previous_bytes = now, received
        self.emitted = True


def batch_progress(index, start, end, count, *, status="running", done=0, repair=False):
    label = f"第 {start}–{end} 句" if start != end else f"第 {start} 句"
    return Progress(label, batch={"id": index, "label": label, "total": count, "done": done,
                                  "status": status, "repair": repair})
