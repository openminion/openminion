"""Resource resolution and tool execution context assembly."""

from pathlib import Path
from typing import Any, Mapping

from openminion.base.config import resolve_data_root
from openminion.base.constants import OPENMINION_DATA_ROOT_ENV
from openminion.modules.memory.smoke import EphemeralMemorySmokeProvider
from openminion.modules.storage.runtime.sqlite import resolve_database_path
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.runtime.delegation import A2ADelegateApi
from openminion.modules.tool.runtime.memory import MemoryToolRuntimeService
from openminion.modules.tool.runtime.routing import build_runtime_tool_routing_metadata
from openminion.services.agent.memory import resolve_memory_root
from openminion.services.agent.memory.gateway_adapter import (
    DisabledMemoryGatewayAdapter,
    MemoryServiceGatewayAdapter,
)
from openminion.services.runtime.a2a_delegate import build_a2a_delegate_api
from openminion.services.runtime.bootstrap import build_agent_memory_service

from .ports import TurnFlowServicePort


class ExecutionResources:
    def __init__(self, service_port: TurnFlowServicePort, runtime: Any) -> None:
        self._service_port = service_port
        self._runtime = runtime
        self._memory_tool_service: MemoryToolRuntimeService | None = None
        self._memory_tool_service_resolved = False
        self._a2a_delegate_api: A2ADelegateApi | None = None
        self._a2a_delegate_api_resolved = False

    def _resolve_memory_tool_service(self) -> MemoryToolRuntimeService | None:
        if self._memory_tool_service_resolved:
            return self._memory_tool_service
        self._memory_tool_service_resolved = True

        config = getattr(self._service_port, "config", None)
        runtime_cfg = getattr(config, "runtime", None)
        if config is None or runtime_cfg is None:
            return None
        if not bool(getattr(runtime_cfg, "memory_enabled", True)):
            return None
        if (
            str(getattr(runtime_cfg, "memory_provider", "memory_v2") or "").strip()
            != "memory_v2"
        ):
            return None

        runtime_env = getattr(runtime_cfg, "env", None)
        env_payload = dict(runtime_env) if isinstance(runtime_env, Mapping) else {}
        home_root_raw = getattr(self._service_port, "home_root", None)
        home_root = (
            Path(home_root_raw).expanduser().resolve(strict=False)
            if home_root_raw is not None
            else Path.cwd().resolve(strict=False)
        )
        data_root = resolve_data_root(
            home_root,
            data_root=str(env_payload.get(OPENMINION_DATA_ROOT_ENV, "") or ""),
        )
        storage_path = resolve_database_path(
            getattr(getattr(config, "storage", None), "path", None),
            env=env_payload,
        )
        memory_root = resolve_memory_root(
            config=config,
            config_path=Path(),
            storage_path=storage_path,
            data_root=data_root,
        )
        try:
            built = build_agent_memory_service(
                config=config,
                agent_id=self._service_port.identity_agent_id,
                memory_root=memory_root,
                logger=self._service_port.logger.getChild("memory_tools"),
                home_root=home_root,
                data_root=data_root,
                storage_path=storage_path,
            )
        except Exception:
            return None

        if isinstance(built, MemoryServiceGatewayAdapter):
            service = getattr(built, "_service", None)
            if isinstance(service, MemoryToolRuntimeService):
                self._memory_tool_service = service
                return service
            return None
        if isinstance(
            built, (DisabledMemoryGatewayAdapter, EphemeralMemorySmokeProvider)
        ):
            return None
        if isinstance(built, MemoryToolRuntimeService):
            self._memory_tool_service = built
            return built
        return None

    def _resolve_a2a_delegate_api(self) -> A2ADelegateApi | None:
        if self._a2a_delegate_api_resolved:
            return self._a2a_delegate_api
        self._a2a_delegate_api_resolved = True

        config = getattr(self._service_port, "config", None)
        if config is None:
            return None
        runtime_env = getattr(getattr(config, "runtime", None), "env", None)
        env_payload = dict(runtime_env) if isinstance(runtime_env, Mapping) else {}
        home_root_raw = getattr(self._service_port, "home_root", None)
        home_root = (
            Path(home_root_raw).expanduser().resolve(strict=False)
            if home_root_raw is not None
            else Path.cwd().resolve(strict=False)
        )
        try:
            self._a2a_delegate_api = build_a2a_delegate_api(
                config=config,
                home_root=home_root,
                agent_id=self._service_port.identity_agent_id,
                env=env_payload or None,
            )
        except Exception:
            self._a2a_delegate_api = None
        return self._a2a_delegate_api

    def build_context(self) -> ToolExecutionContext:
        inbound = self._runtime.inbound
        config = self._service_port.config
        runtime_cfg = getattr(config, "runtime", None)
        tool_metadata = dict(inbound.metadata or {})
        runtime_env = getattr(runtime_cfg, "env", None)
        env_payload = dict(runtime_env) if isinstance(runtime_env, Mapping) else None
        resolved_storage_path = getattr(self._runtime, "storage_path", None)
        if resolved_storage_path is None:
            resolved_storage_path = resolve_database_path(
                getattr(getattr(config, "storage", None), "path", None),
                env=env_payload,
            )
        if env_payload is not None:
            tool_metadata.setdefault("runtime_env", env_payload)
        runtime_tools = getattr(runtime_cfg, "tools", None)
        for key, value in build_runtime_tool_routing_metadata(runtime_tools).items():
            tool_metadata.setdefault(key, value)
        tool_metadata.setdefault("agent_id", self._service_port.identity_agent_id)
        tool_metadata.setdefault("tool_call_origin", "model")
        if self._service_port.tool_selection is not None:
            for (
                key,
                value,
            ) in self._service_port.tool_selection.runtime_binding_policy_metadata().items():
                tool_metadata.setdefault(key, value)
        tool_metadata.setdefault("storage_path", str(resolved_storage_path or ""))
        tool_metadata.setdefault(
            "memory_enabled",
            str(bool(getattr(runtime_cfg, "memory_enabled", True))).lower(),
        )
        tool_metadata.setdefault(
            "memory_provider",
            str(getattr(runtime_cfg, "memory_provider", "memory_v2") or "").strip(),
        )
        return ToolExecutionContext(
            channel=inbound.channel,
            target=inbound.target,
            session_id=inbound.metadata.get("session_id", ""),
            metadata=tool_metadata,
            memory_service=self._resolve_memory_tool_service(),
            sandbox_runner=getattr(self._runtime, "sandbox_runner", None),
            authored_tools_api=getattr(self._runtime, "authored_tools", None),
            a2a_delegate_api=self._resolve_a2a_delegate_api(),
        )

    def build_context_with_overrides(
        self,
        *,
        context_metadata_overrides: Mapping[str, Any] | None,
        turn_boundary_adapter: Any,
    ) -> ToolExecutionContext:
        context = self.build_context()
        if context_metadata_overrides and isinstance(context.metadata, dict):
            for key, value in context_metadata_overrides.items():
                token = str(key or "").strip()
                if not token:
                    continue
                context.metadata[token] = (
                    dict(value)
                    if token == "runtime_env" and isinstance(value, Mapping)
                    else str(value)
                )
        try:
            context.blast_radius_adapter = turn_boundary_adapter
        except AttributeError:
            pass
        return context


__all__ = ["ExecutionResources"]
