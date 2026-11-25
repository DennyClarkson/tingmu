"""Composed workspace text-consent service; human decisions stay in the UI."""

import uuid


class TextSetupService:
    def __init__(self, controller):
        self.controller = controller
        self.proposal = {}
        self.bindings = ()

    def pending(self):
        return self.proposal.get("status") == "pending"

    def propose(self, workspace_id, context):
        from tingmu import workspace_text as text
        controller = self.controller
        bridge = controller.bridge
        destination = controller.store_for(workspace_id)
        source = bridge.settings_snapshot()
        target = controller.settings_for(workspace_id)
        rows = text.assignments(bridge.store.data_dir, source, include_disabled=True)
        if target.text_models_enabled and target.cloud_text_enabled:
            return {
                "applied": True,
                "roles": text.public_roles(text.assignments(destination.data_dir, target)),
            }
        public = text.public_roles(rows)
        if not any(not row["local"] for row in rows.values()):
            applied = text.enable_global_text(bridge.store.data_dir, source, target)
            if applied:
                controller.save_settings(workspace_id, target)
            return {"applied": bool(applied), "roles": applied, "requires_human_confirmation": False}
        self.bindings = (text.roles_signature(rows), text.target_text_signature(destination, target))
        self.proposal = {
            "id": uuid.uuid4().hex,
            "status": "pending",
            "workspace_id": workspace_id,
            "workspace_name": controller.workspace_public(workspace_id)["name"],
            "origin_session_id": context.session_id,
            "roles": public,
        }
        controller.changed.emit()
        return {"applied": False, "requires_human_confirmation": True, "roles": public}

    def decide(self, ident, approved):
        from tingmu import workspace_text as text
        controller = self.controller
        bridge = controller.bridge
        proposal = self.proposal
        if proposal.get("id") != ident or not self.pending():
            return False
        try:
            if not approved:
                proposal["status"] = "declined"
                return True
            if bridge.chat.active_session_id != proposal["origin_session_id"]:
                raise ValueError("请在提出方案的原会话重新确认。")
            destination = controller.store_for(proposal["workspace_id"])
            settings = controller.settings_for(proposal["workspace_id"])
            expected, previous = self.bindings
            if previous != text.target_text_signature(destination, settings):
                raise ValueError("目标工作区的文本连接已变更，请重新选择。")
            text.enable_global_text(
                bridge.store.data_dir,
                bridge.settings_snapshot(),
                settings,
                allow_remote=True,
                expected_source=expected,
            )
            controller.save_settings(proposal["workspace_id"], settings)
            proposal["status"] = "ready"
            return True
        except (ValueError, RuntimeError, OSError) as exc:
            proposal.update(status="failed", error=str(exc))
            return False
        finally:
            controller.changed.emit()
