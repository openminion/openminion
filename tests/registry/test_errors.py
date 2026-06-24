from __future__ import annotations

from openminion.modules.registry.errors import AgentRegError


def test_agent_reg_error_to_dict_uses_details_plural() -> None:
    payload = AgentRegError(
        "NOT_FOUND",
        "agent missing",
        {"agent_id": "demo"},
    ).to_dict()
    assert payload == {
        "code": "NOT_FOUND",
        "message": "agent missing",
        "details": {"agent_id": "demo"},
    }
