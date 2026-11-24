"""UI state projections; the legacy facade remains a separate compatibility view."""
from copy import deepcopy

from PySide6.QtCore import Property
from .commands import command_domain as command_domain


DOMAIN_KEYS = {
    "media": {"mediaPath", "mediaName", "subtitleSource", "reviewModel", "reviewSummary", "lessonCounts"},
    "queue": {"queueRunning", "totalCount", "jobCount", "hasPending", "failedCount", "runningMediaPaths"},
    "chat": {"chatBusy", "chatStopping", "streaming", "chatToolStatus", "chatToolPhase", "activePhase", "activeStatus"},
    "download": {"modelDownloading", "downloadProgress", "downloadMessage"},
    "learning": {"learningBusy"},
    "models": {"modelBusy"},
    "workspace": {"speechModelChoices", "mediaDir"},
    "application": {"busy", "dataDir", "version", "cloudEnabled"},
    "notification": set(),
}
KEY_DOMAIN = {key: domain for domain, keys in DOMAIN_KEYS.items() for key in keys}


def isolated(value):
    if type(value) in (str, int, float, bool, type(None)):
        return value
    return deepcopy(value)


class DomainProperty(Property):
    """Qt converts a cached map into its own QVariant. Python gets an isolated map."""

    def __init__(self, domain, notify):
        self.domain = domain
        super().__init__("QVariantMap", lambda bridge: bridge._domain_states.qt_view(domain), notify=notify)

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return instance._domain_states.snapshot(self.domain)


class DomainStates:
    def __init__(self, initial):
        self._values = {domain: {"error": "", "status": ""} for domain in DOMAIN_KEYS}
        self._nested = {domain: set() for domain in DOMAIN_KEYS}
        self.update(initial)

    def qt_view(self, domain):
        """Borrow this revision until Qt converts it. Writers replace the entire map."""
        return self._values[domain]

    def snapshot(self, domain):
        row = self._values[domain].copy()
        for key in self._nested[domain]:
            row[key] = deepcopy(row[key])
        return row

    def update(self, values, domain="notification"):
        changed = set()
        for key, value in values.items():
            targets = (domain, "notification") if key in {"error", "status"} else (KEY_DOMAIN.get(key, "application"),)
            for target in targets:
                if self._values[target].get(key) == value:
                    continue
                if target not in changed:
                    self._values[target] = self._values[target].copy()
                    changed.add(target)
                self._values[target][key] = isolated(value)
                if type(value) in (str, int, float, bool, type(None)):
                    self._nested[target].discard(key)
                else:
                    self._nested[target].add(key)
        return changed


def task_domain(key):
    return {"chat": "chat", "summary": "chat", "notebook": "learning", "models": "models",
            "queue": "queue", "model-download": "download", "speech-models": "workspace",
            "media-copy": "media", "display-import": "media", "import": "media"}.get(key, "notification")


def setting_domain(name):
    if name == "companion_reasoning_effort":
        return "chat"
    if name == "teaching_preferences":
        return "learning"
    if name.endswith("_profile_id") or name in {"provider", "model", "review_provider", "review_model",
                                                "companion_provider", "companion_model"}:
        return "models"
    if name.startswith(("subtitle_", "overlay_")) or name in {"audio_track", "audio_output_id"}:
        return "media"
    return "workspace"
