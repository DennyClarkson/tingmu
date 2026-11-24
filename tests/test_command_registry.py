"""The trusted UI registry owns routing metadata without expanding Agent permissions."""

from dataclasses import replace
from pathlib import Path
import re

import pytest

from tingmu.app_actions import ACTION_NAMES, action_tools
from tingmu.quick import commands, state
from test_domain_events import bridge as bridge


def test_qml_command_names_resolve_to_composed_handlers(bridge):
    qml = Path(__file__).parents[1] / "src/tingmu/quick/qml"
    literal_commands = set()
    for path in qml.glob("*.qml"):
        literal_commands.update(
            re.findall(r'backend\.command\(\s*"([A-Za-z]+)"', path.read_text(encoding="utf-8"))
        )
    assert literal_commands
    assert literal_commands <= commands.COMMANDS.keys()
    for name, spec in commands.COMMANDS.items():
        owner, method = spec.handler
        assert callable(getattr(getattr(bridge, owner), method)), name
        assert spec.domain in {"notification", "media", "queue", "chat", "learning", "models", "workspace"}
    with pytest.raises(TypeError):
        commands.COMMANDS["shutdown"] = commands.COMMANDS["playPause"]


def test_domain_and_dispatch_consume_one_registry_entry(bridge, monkeypatch):
    calls = []

    def handler(name, payload):
        calls.append((name, payload))
        raise ValueError("synthetic local rejection")

    spec = replace(commands.COMMANDS["playPause"], domain="learning")
    monkeypatch.setattr(commands, "COMMANDS", {**commands.COMMANDS, "playPause": spec})
    monkeypatch.setattr(bridge.playback_controller, "command_playPause", handler)
    bridge._set_state(status="background progress remains visible")
    bridge.command("playPause", {"synthetic": True})
    assert calls == [("playPause", {"synthetic": True})]
    assert state.command_domain("playPause") == "learning"
    assert bridge.learningState["error"] == "synthetic local rejection"
    assert not bridge.mediaState["error"]


def test_unknown_ui_commands_and_human_routes_are_not_dynamic_dispatch(bridge):
    schemas = {tool["function"]["name"] for tool in action_tools()}
    assert schemas == set(ACTION_NAMES)
    for name in ("shutdown", "decideProposal", "decideTextReuse", "requestDialog", "openDialog", "openFiles"):
        assert name not in schemas
    for name in ("shutdown", "decideProposal", "decideTextReuse", "requestDialog", "openDialog"):
        with pytest.raises(ValueError, match="未知操作"):
            commands.dispatch(bridge, name, {})
    assert not bridge.is_closed
