from pathlib import Path
from typing import Any

from openminion.modules.policy.adapters.brain import PolicyCtlBrainAdapter


def create_policy_runtime_adapter(
    *,
    db_path: str | Path,
    policy_service: Any | None = None,
    action_policy_config: Any | None = None,
) -> PolicyCtlBrainAdapter:
    if policy_service is not None:
        return PolicyCtlBrainAdapter(
            policy_service,
            action_policy_config=action_policy_config,
        )
    resolved_path = Path(db_path)
    if resolved_path.suffix == "":
        resolved_path = resolved_path / "policy.db"
    return PolicyCtlBrainAdapter.with_sqlite(
        resolved_path,
        action_policy_config=action_policy_config,
    )


__all__ = ["PolicyCtlBrainAdapter", "create_policy_runtime_adapter"]
