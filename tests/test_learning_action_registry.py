"""Learning action dispatch remains an explicit, schema-gated capability list."""

from unittest.mock import Mock

import pytest

from tingmu.app_actions import (
    ACTION_NAMES,
    UI_ACTIONS,
    LEARNING_ACTION_HANDLERS,
    ActionContext,
    ActionError,
    AppActionExecutor,
    action_tools,
)
from tingmu.models import LanguagePair
from tingmu.providers import RequestCancelled


def executor(**kwargs):
    return AppActionExecutor(
        None, None, ActionContext("session", "", LanguagePair(target="en")), None, Mock(), **kwargs
    )


def test_learning_registry_covers_exactly_schema_actions_with_bound_immutable_handlers():
    actions = executor()
    names = {tool["function"]["name"] for tool in action_tools()}
    assert set(LEARNING_ACTION_HANDLERS) == names - UI_ACTIONS == ACTION_NAMES - UI_ACTIONS
    assert set(actions._learning_handlers) == set(LEARNING_ACTION_HANDLERS)
    for name, method in LEARNING_ACTION_HANDLERS.items():
        handler = actions._learning_handlers[name]
        assert handler.__self__ is actions
        assert handler.__func__ is getattr(AppActionExecutor, method)
    with pytest.raises(TypeError):
        actions._learning_handlers["run_shell"] = lambda *args: None
    with pytest.raises(TypeError):
        LEARNING_ACTION_HANDLERS["approve_download"] = "_handle_save_notebook"
    assert not {"run_shell", "read_file", "approve_download", "requestDialog", "openDialog"} & names


def test_helper_names_unknown_actions_and_invalid_arguments_never_reach_handlers(monkeypatch):
    calls = []

    def handler(self, name, args, target):
        calls.append((name, args, target))
        return {"saved": False}

    monkeypatch.setattr(AppActionExecutor, "_handle_save_notebook", handler)
    actions = executor()
    for name in ("_handle_save_notebook", "run_shell", "approve_download"):
        with pytest.raises(ActionError) as error:
            actions(name, {})
        assert error.value.code == "unknown_action"
    with pytest.raises(ActionError) as error:
        actions("save_notebook", {"markdown": "text", "revision": "seen", "path": "elsewhere.md"})
    assert error.value.code == "invalid_arguments"
    assert calls == []
    arguments = {"markdown": "text", "revision": "seen"}
    assert actions("save_notebook", arguments) == {"saved": False}
    assert calls == [("save_notebook", arguments, "en")]
    actions.dispatch.assert_not_called()


def test_cancelled_learning_action_stops_before_registry_dispatch(monkeypatch):
    handler = Mock()
    monkeypatch.setattr(AppActionExecutor, "_handle_append_note", handler)
    actions = executor(cancelled=lambda: True)
    with pytest.raises(RequestCancelled):
        actions("append_learning_note", {"text": "must not be saved"})
    handler.assert_not_called()
