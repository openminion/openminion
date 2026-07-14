from __future__ import annotations

from typing import Any

from openminion.cli.parser.contracts import CLI_INTERFACE_VERSION
from openminion.modules.task.surface import build_task_surface


class RuntimeTasksProvider:
    contract_version: str = CLI_INTERFACE_VERSION

    def __init__(
        self,
        task_ctl: Any | None,
        *,
        agent_id: str,
        session_id: str,
        digest_limit: int = 50,
        event_limit: int = 500,
    ) -> None:
        self._surface = build_task_surface(
            task_ctl,
            agent_id=agent_id,
            session_id=session_id,
            limit=digest_limit,
            event_limit=event_limit,
        )

    def list_tasks(self) -> list[dict[str, Any]]:
        return self._surface.list_tasks()

    def list_pending_actions(self) -> list[dict[str, Any]]:
        return self._surface.list_pending_actions()

    def resolve_action(self, decision_id: str, outcome: str) -> bool:
        try:
            self._surface.apply_action(
                task_id="",
                action=outcome,
                decision_id=decision_id,
            )
        except (
            AttributeError,
            TypeError,
            ValueError,
            RuntimeError,
            NotImplementedError,
        ):
            return False
        return True
