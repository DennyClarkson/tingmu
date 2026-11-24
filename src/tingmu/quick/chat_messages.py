"""Public message construction, independent of the Qt facade."""

from datetime import datetime, timezone
import uuid


def message(role, content, **metadata):
    return {
        "id": uuid.uuid4().hex,
        "role": role,
        "content": content,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "complete",
        **metadata,
    }


def learning_tools():
    from tingmu.app_actions import action_tools

    return action_tools()
