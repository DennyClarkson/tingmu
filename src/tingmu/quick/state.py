"""UI state projections; the legacy facade remains a separate compatibility view."""
from copy import deepcopy
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


class DomainStates:
    def __init__(self, initial):
        self._values = {domain: {"error": "", "status": ""} for domain in DOMAIN_KEYS}
        self.update(initial)

    def snapshot(self, domain):
        return deepcopy(self._values[domain])

    def update(self, values, domain="notification"):
        changed = set()
        for key, value in values.items():
            targets = {domain, "notification"} if key in {"error", "status"} else {KEY_DOMAIN.get(key, "application")}
            for target in targets:
                if self._values[target].get(key) != value:
                    self._values[target][key] = deepcopy(value)
                    changed.add(target)
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
