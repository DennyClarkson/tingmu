"""Explicit composition root and finite legacy aliases for the QML Bridge.

Only this binding layer knows historical Bridge member names. Domain services
receive typed callback ports and owned state; no service forwards unknown attrs.
"""

from types import SimpleNamespace

from .chat_controller import ChatController, ChatPorts
from .learning_controller import LearningController, LearningPorts
from .model_controller import ModelController, ModelPorts
from .ui_actions import UiActionController, UiActionPorts
from .feature_state import FeatureState
from .application_controller import ApplicationController, ApplicationPorts

_PORT_SPECS = {
    "models": {
        "settings": ["getter", "_settings"],
        "store": ["getter", "store"],
        "settingsChanged": ["signal", "settingsChanged"],
        "set_state": ["method", "_set_state"],
        "model_registry": ["getter", "model_registry"],
        "task_workers": ["getter", "_workers"],
        "modelsChanged": ["signal", "modelsChanged"],
        "start_job": ["method", "_start_job"],
        "queue": ["getter", "queue"],
        "sync_chat_stopping": ["method", "_sync_chat_stopping"],
    },
    "chat": {
        "library_rows": ["getter", "_library"],
        "conversations": ["getter", "conversations"],
        "settings": ["getter", "_settings"],
        "media_path": ["getter", "media_path"],
        "task_workers": ["getter", "_workers"],
        "set_state": ["method", "_set_state"],
        "chat_model_snapshot": ["method", "_chat_model_snapshot"],
        "chatSessionsChanged": ["signal", "chatSessionsChanged"],
        "messagesChanged": ["signal", "messagesChanged"],
        "is_closed": ["getter", "_closed"],
        "workspaces": ["getter", "workspaces"],
        "thread": ["method", "thread"],
        "store": ["getter", "store"],
        "notebook_manager": ["getter", "notebook_manager"],
        "request_app_action": ["method", "_request_app_action"],
        "learningUpdated": ["signal", "_learningUpdated"],
        "active_cue": ["getter", "_active_cue"],
        "frozen_subtitle": ["method", "_frozen_subtitle"],
        "tutor_transport": ["method", "_tutor_transport"],
        "persist_chat": ["method", "_persist_chat"],
        "player": ["getter", "_player"],
        "playback": ["getter", "playback"],
        "chatPresentation": ["signal", "_chatPresentation"],
        "chatDelta": ["signal", "_chatDelta"],
        "chatActionProgress": ["signal", "_chatActionProgress"],
        "log": ["getter", "log"],
        "refresh_notebooks": ["method", "_refresh_notebooks"],
        "start_job": ["method", "_start_job"],
        "send_chat": ["method", "_send_chat"],
        "activate_context_session": ["method", "_activate_context_session"],
        "select_chat": ["method", "_select_chat"],
    },
    "learning": {
        "command": ["method", "_command"],
        "settings": ["getter", "_settings"],
        "notebook_manager": ["getter", "notebook_manager"],
        "set_state": ["method", "_set_state"],
        "export_destination": ["method", "_export_destination"],
        "notebooksChanged": ["signal", "notebooksChanged"],
        "setting": ["method", "setting"],
        "v4_send_chat": ["method", "_v4_send_chat"],
        "is_closed": ["getter", "_closed"],
        "error": ["method", "_error"],
        "refresh_entries": ["method", "_refresh_entries"],
        "model_registry": ["getter", "model_registry"],
        "task_workers": ["getter", "_workers"],
        "tutor_transport": ["method", "_tutor_transport"],
        "start_job": ["method", "_start_job"],
        "collect_word": ["method", "_collect_word"],
        "collect_sentence": ["method", "_collect_sentence"],
        "find_cue": ["method", "_find_cue"],
        "store": ["getter", "store"],
        "media_path": ["getter", "media_path"],
        "settingsChanged": ["signal", "settingsChanged"],
        "open_media": ["method", "open_media"],
        "load_document": ["method", "_load_document"],
        "load_personal": ["method", "_load_personal"],
        "seek": ["method", "_seek"],
    },
    "actions": {
        "perform_app_action": ["method", "_perform_app_action"],
        "start_action_jobs": ["method", "_start_action_jobs"],
        "load_document": ["method", "_load_document"],
        "settings": ["getter", "_settings"],
        "store": ["getter", "store"],
        "task_workers": ["getter", "_workers"],
        "queue": ["getter", "queue"],
        "library_cache": ["getter", "_library_cache"],
        "set_document": ["method", "_set_document"],
        "refresh_library": ["method", "_refresh_library"],
        "set_state": ["method", "_set_state"],
        "workspaces": ["getter", "workspaces"],
        "model_registry": ["getter", "model_registry"],
        "media_path": ["getter", "media_path"],
        "player": ["getter", "_player"],
        "playback": ["getter", "playback"],
        "save_position": ["method", "save_position"],
        "seek": ["method", "_seek"],
        "command": ["method", "_command"],
        "update_playback": ["method", "_update_playback"],
        "refresh_jobs": ["method", "_refresh_jobs"],
        "resume_inbox": ["method", "_resume_inbox"],
        "feature_command": ["method", "_feature_command"],
        "appAction": ["signal", "appAction"],
        "is_closed": ["getter", "_closed"],
        "thread": ["method", "thread"],
        "appActionRequested": ["signal", "_appActionRequested"],
        "start_job": ["method", "_start_job"],
        "log": ["getter", "log"],
        "setting": ["method", "setting"],
    },
    "application_controller": {
        "store": ["getter", "store"],
        "log": ["getter", "log"],
        "refresh_diagnostics": ["method", "refresh_diagnostics"],
        "export_diagnostics": ["method", "export_diagnostics"],
        "set_state": ["method", "_set_state"],
    },
}

_STATE_ALIASES = {
    "_message_markdown": ("chat", "markdown"),
    "_messages": ("chat", "messages"),
    "_chat_sessions": ("chat", "sessions"),
    "_active_session_id": ("chat", "active_session_id"),
    "_chat_worker_session_id": ("chat", "worker_session_id"),
    "_agent_selected_media": ("chat", "selected_media"),
    "_model_profiles": ("models", "profiles"),
    "_model_presets": ("models", "presets"),
    "_discovered_models": ("models", "discovered"),
    "_model_status": ("models", "status"),
    "_notebook_language": ("learning", "language"),
    "_notebooks": ("learning", "notebooks"),
}

FEATURE_METHODS = {
    "_refresh_models": ("models", "_refresh_models"),
    "_profile": ("models", "_profile"),
    "_tutor_transport": ("models", "_tutor_transport"),
    "_chat_model_snapshot": ("models", "_chat_model_snapshot"),
    "_profile_fingerprint": ("models", "_profile_fingerprint"),
    "_stop_model_jobs": ("models", "_stop_model_jobs"),
    "_refresh_notebooks": ("learning", "_refresh_notebooks"),
    "_organize_notebook": ("learning", "_organize_notebook"),
    "_display_messages": ("chat", "_display_messages"),
    "_refresh_sessions": ("chat", "_refresh_sessions"),
    "_activate_context_session": ("chat", "_activate_context_session"),
    "_select_chat": ("chat", "_select_chat"),
    "_sync_chat_stopping": ("chat", "_sync_chat_stopping"),
    "_persist_chat": ("chat", "_persist_chat"),
    "_chat_delta": ("chat", "_chat_delta"),
    "_chat_presentation": ("chat", "_chat_presentation"),
    "_tool_executor": ("chat", "_tool_executor"),
    "_chat_action_progress": ("chat", "_chat_action_progress"),
    "_v4_send_chat": ("chat", "_v4_send_chat"),
    "_request_app_action": ("actions", "_request_app_action"),
    "_dispatch_app_action": ("actions", "_dispatch_app_action"),
    "_perform_app_action": ("actions", "_perform_app_action"),
    "_start_action_jobs": ("actions", "_start_action_jobs"),
}


class _SignalPort:
    def __init__(self, host, name):
        self.host, self.name = host, name

    def emit(self, *args):
        return getattr(self.host, self.name).emit(*args)


def _ports(host, group, port_type):
    values = {}
    for public, (kind, legacy) in _PORT_SPECS[group].items():
        if kind == "signal":
            values[public] = _SignalPort(host, legacy)
        elif kind == "getter":
            if legacy == "workspaces":
                values[public] = lambda: getattr(host, "workspaces", None)
            else:
                values[public] = lambda attr=legacy: getattr(host, attr)
        else:
            values[public] = lambda *args, attr=legacy, **kwargs: getattr(host, attr)(*args, **kwargs)
    return port_type(**values)


def initialize_feature_state(host):
    if "feature_state" not in host.__dict__:
        host.feature_state = FeatureState()


def _legacy_state(host, group):
    # Old unbound extension calls can supply a minimal duck-typed facade. These
    # finite properties translate only the documented legacy fields they use.
    aliases = {field: name for name, (owner, field) in _STATE_ALIASES.items() if owner == group}
    namespace = {
        field: property(
            lambda self, attr=name: getattr(host, attr),
            lambda self, value, attr=name: setattr(host, attr, value),
        )
        for field, name in aliases.items()
    }
    return type("Legacy" + group.title() + "State", (), namespace)()


def _build_services(host, state):
    return SimpleNamespace(
        application_controller=ApplicationController(
            None, _ports(host, "application_controller", ApplicationPorts)
        ),
        chat=ChatController(state.chat, _ports(host, "chat", ChatPorts)),
        models=ModelController(state.models, _ports(host, "models", ModelPorts)),
        learning=LearningController(state.learning, _ports(host, "learning", LearningPorts)),
        actions=UiActionController(None, _ports(host, "actions", UiActionPorts)),
    )


def services_for(host):
    existing = getattr(host, "feature_services", None)
    if existing is not None:
        return existing
    state = SimpleNamespace(**{group: _legacy_state(host, group) for group in ("chat", "models", "learning")})
    return _build_services(host, state)


def initialize_features(host):
    from tingmu.notebooks import NotebookManager
    from tingmu.conversations import ConversationStore

    initialize_feature_state(host)
    host.model_registry = host.store.model_registry
    host.notebook_manager = NotebookManager(host.store)
    host.conversations = ConversationStore(host.store)
    host.feature_state.models.presets = host.model_registry.presets()
    host.feature_state.learning.language = host._settings.target_language
    host.feature_services = _build_services(host, host.feature_state)
    host.chat, host.models = host.feature_services.chat, host.feature_services.models
    host.learning, host.actions = host.feature_services.learning, host.feature_services.actions
    host.application_controller = host.feature_services.application_controller
    host._appActionRequested.connect(host._dispatch_app_action)
    host._chatActionProgress.connect(host._chat_action_progress)
    host._chatDelta.connect(host._chat_delta)
    host._chatPresentation.connect(host._chat_presentation)
    host._learningUpdated.connect(host._refresh_notebooks)
    host._refresh_models()
    host._activate_context_session()
    host.notebook_manager.sync(host._notebook_language)
    host._refresh_notebooks()


def _delegate(name):
    group, method = FEATURE_METHODS[name]

    def forward(host, *args, **kwargs):
        return getattr(getattr(services_for(host), group), method)(*args, **kwargs)

    forward.__name__ = name
    forward.__doc__ = "Compatibility delegate to " + group + "." + method
    return forward


def _state_property(group, field):
    def read(host):
        initialize_feature_state(host)
        return getattr(getattr(host.feature_state, group), field)

    def write(host, value):
        initialize_feature_state(host)
        setattr(getattr(host.feature_state, group), field, value)

    return property(read, write)


def feature_command(host, name, payload):
    from .commands import try_feature

    return try_feature(host, name, payload)


def install_feature_facade(cls):
    """Install the closed compatibility list; never resolve arbitrary attributes."""
    for name in FEATURE_METHODS:
        setattr(cls, name, _delegate(name))
    for name, (group, field) in _STATE_ALIASES.items():
        setattr(cls, name, _state_property(group, field))
    cls._feature_init = initialize_features
    cls._feature_command = feature_command
    return cls


class FeatureMixin:
    """Import/unbound-call compatibility only. Bridge does not inherit this class."""

    _feature_command = feature_command


for _name in FEATURE_METHODS:
    setattr(FeatureMixin, _name, _delegate(_name))
