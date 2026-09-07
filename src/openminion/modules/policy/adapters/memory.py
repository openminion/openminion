from typing import Any

from openminion.modules.tool.contracts.model_ids import MODEL_MEMORY_WRITE

from ..runtime.security import (
    SecurityPolicyAction,
    SecurityPolicyCheck,
    SecurityPolicyContext,
    default_internal_actor,
)


def memory_capture_recovery_allowed(
    session_id: str,
    root_turn_id: str,
    _event_id: str,
    _capture_id: str,
    *,
    policy: Any | None,
    tools: Any | None,
    agent_id: str,
) -> bool:
    if policy is None:
        return True
    if tools is None:
        return False
    profile = tools.policy_for(MODEL_MEMORY_WRITE)
    decision = policy.evaluate(
        SecurityPolicyCheck(
            actor=default_internal_actor(agent_id),
            action=SecurityPolicyAction(
                resource="tool",
                verb="execute",
                risk=str(profile.risk),
                tool_name=MODEL_MEMORY_WRITE,
                required_scopes_all=frozenset(profile.required_scopes_all),
            ),
            context=SecurityPolicyContext(
                channel="cron",
                target="memory-capture-recovery",
                session_id=session_id,
                run_id=root_turn_id,
            ),
        )
    )
    return bool(decision.allowed)


__all__ = ["memory_capture_recovery_allowed"]
