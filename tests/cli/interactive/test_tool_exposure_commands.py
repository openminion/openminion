from __future__ import annotations

from openminion.cli.interactive.commands import SlashCommandMixin


class _Runtime:
    def __init__(self) -> None:
        self.active = False

    def list_tools(self):
        return [("ops.target.list", True), ("ops.job.cancel", self.active)]

    def tool_exposure_status(self):
        return {
            "profiles": [
                {"profile_id": "ops_minimal", "tier": "read", "active": True},
                {
                    "profile_id": "ops_job_control",
                    "tier": "apply",
                    "active": self.active,
                },
            ]
        }

    def activate_tool_profile(self, profile_id: str, **kwargs):
        assert profile_id == "ops_job_control"
        assert kwargs["approved"] is True
        self.active = True
        return {"profile_id": profile_id, "audit_id": "audit-1"}

    def deactivate_tool_profile(self, profile_id: str, **kwargs):
        assert profile_id == "ops_job_control"
        self.active = False
        return True


def _commands(runtime: _Runtime) -> SlashCommandMixin:
    commands = object.__new__(SlashCommandMixin)
    commands._runtime = runtime
    return commands


def test_tools_command_supports_status_activation_and_deactivation() -> None:
    runtime = _Runtime()
    tab = _commands(runtime)

    assert "hidden  ops_job_control  (apply)" in tab._tools_command_body("/tools")
    activated = tab._tools_command_body(
        "/tools activate ops_job_control approved=yes"
    )
    assert activated == "Activated: ops_job_control (audit-1)"
    assert "active  ops_job_control  (apply)" in tab._tools_command_body("/tools status")
    assert tab._tools_command_body(
        "/tools deactivate ops_job_control"
    ) == "Deactivated: ops_job_control"


def test_tools_command_rejects_unstructured_options() -> None:
    tab = _commands(_Runtime())
    assert tab._tools_command_body(
        "/tools activate ops_job_control invalid"
    ) == "Tool profile options must use key=value syntax."
