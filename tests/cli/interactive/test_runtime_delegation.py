from __future__ import annotations

from types import SimpleNamespace

from openminion.cli.interactive.runtime.delegation import RuntimeDelegationMixin


def test_runtime_delegation_forwards_readonly_child_permission(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _run_agent_delegate_request(**kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(
        "openminion.cli.commands.agent.delegation.run_agent_delegate_request",
        _run_agent_delegate_request,
    )

    runtime = SimpleNamespace(
        config=SimpleNamespace(),
        home_root="/tmp/openminion-home",
        resolve_agent_service=lambda _agent_id: SimpleNamespace(artifactctl=None),
    )
    interactive = RuntimeDelegationMixin()
    interactive._rt = runtime
    interactive.agent_id = "parent"
    interactive.working_dir = "/tmp/workspace"
    interactive.session_id = "session-1"

    result = interactive.delegate_task(
        mode="sync",
        target_agent_id="bogr-readonly-researcher",
        instruction="find candidate contracts",
        child_permission_mode="readonly",
    )

    assert result == {"ok": True}
    request = seen["request"]
    assert request.child_permission_mode == "readonly"
