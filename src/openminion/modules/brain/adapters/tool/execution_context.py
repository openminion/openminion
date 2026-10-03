"""Runtime tool context assembly for the brain tool adapter."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from openminion.modules.tool import (
    ToolExecutionContext,
    build_runtime_tool_routing_metadata,
    resolve_runtime_tool_config,
)
from openminion.modules.tool.exposure.service import (
    project_security_lab_metadata,
    resolve_security_lab_metadata,
)

from .policy_context import _runtime_env_from_policy


@dataclass(frozen=True)
class ToolExecutionContextBuilder:
    agent_id: str
    memory_service: Any
    sandbox_runner: Any
    security_lab_runner: Any
    security_lab_config: Any
    identity_security_lab_facts: Callable[[], dict[str, Any]] | None
    exposure_service: Any

    def project_security_lab(self, metadata: dict[str, Any], session_id: str) -> None:
        identity = (
            self.identity_security_lab_facts()
            if self.identity_security_lab_facts is not None
            else {}
        )
        project_security_lab_metadata(
            metadata,
            resolve_security_lab_metadata(
                self.exposure_service,
                config=self.security_lab_config,
                runner=self.security_lab_runner,
                identity=identity,
                session_id=session_id,
            ),
        )

    def build(
        self,
        *,
        policy: Any,
        session_id: str,
        trace_id: str,
        orchestration_metadata: Mapping[str, Any] | None,
        replay_confirmation_metadata: Mapping[str, str] | None,
    ) -> ToolExecutionContext:
        policy_raw = getattr(policy, "raw", {}) or {}
        raw_metadata = policy_raw.get("context_metadata")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
        workspace_root = str(policy_raw.get("workspace_root", "") or "").strip()
        if workspace_root:
            metadata.setdefault("workspace_root", workspace_root)
        metadata.update(
            agent_id=self.agent_id,
            trace_id=trace_id,
            runtime_env=_runtime_env_from_policy(policy),
        )
        if orchestration_metadata:
            metadata["orchestration"] = dict(orchestration_metadata)
        metadata.update(
            build_runtime_tool_routing_metadata(resolve_runtime_tool_config(policy))
        )
        self.project_security_lab(metadata, session_id)
        if replay_confirmation_metadata:
            metadata.update(
                {
                    key: value
                    for key, value in replay_confirmation_metadata.items()
                    if str(value or "").strip()
                }
            )
        return ToolExecutionContext(
            channel="console",
            target=session_id or "session",
            session_id=session_id,
            metadata=metadata,
            memory_service=self.memory_service,
            sandbox_runner=self.sandbox_runner,
            security_lab_runner=self.security_lab_runner,
            confirm=bool(replay_confirmation_metadata),
        )


__all__ = ["ToolExecutionContextBuilder"]
