"""Agent query helpers for the developer API."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

from openminion.api.config import close_api_runtime_if_owned
from openminion.api.core.deps import configured_agent_ids, resolve_runtime_manager
from openminion.base.config.core import resolve_default_agent_id

from openminion.services.runtime.daemon import agent_status_to_dict


@dataclass
class AgentQueryError(RuntimeError):
    message: str
    code: str = "invalid_request"
    status: HTTPStatus = HTTPStatus.BAD_REQUEST

    def __str__(self) -> str:
        return self.message


def list_agents(
    *,
    config_path: str | None,
    runtime,
) -> dict[str, Any]:
    manager, active_runtime, own_runtime = resolve_runtime_manager(
        config_path=config_path,
        runtime=runtime,
    )
    try:
        agents = [agent_status_to_dict(item) for item in manager.list_agents()]
        hot_agent_ids = [
            str(item.get("agent_id", "")).strip()
            for item in agents
            if isinstance(item, dict)
        ]
        return {
            "ok": True,
            "agents": agents,
            "hot_agent_ids": sorted(item for item in hot_agent_ids if item),
            "registry_agent_ids": configured_agent_ids(active_runtime),
            "default_agent_id": resolve_default_agent_id(active_runtime.config),
        }
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)


def inspect_agent(
    *,
    config_path: str | None,
    runtime,
    agent_id: str,
) -> dict[str, Any]:
    normalized_agent_id = str(agent_id or "").strip()
    if not normalized_agent_id:
        raise AgentQueryError("`agent_id` is required.")
    _, active_runtime, own_runtime = resolve_runtime_manager(
        config_path=config_path,
        runtime=runtime,
    )
    try:
        storage_path = active_runtime.config.storage.path
        from openminion.cli.commands.agents import agent_inspect
        from openminion.modules.storage.runtime.registry_store import (
            AgentRegistryStore,
        )

        registry = AgentRegistryStore(storage_path)
        buf = io.StringIO()
        with redirect_stdout(buf):
            agent_inspect(registry, normalized_agent_id, as_json=True)
        text = buf.getvalue().strip()
        if not text:
            raise AgentQueryError(
                f"Agent '{normalized_agent_id}' inspection produced no payload.",
                code="inspect_failed",
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )
        return json.loads(text)
    except AgentQueryError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AgentQueryError(
            str(exc),
            code="inspect_error",
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        ) from exc
    finally:
        close_api_runtime_if_owned(active_runtime, own_runtime=own_runtime)
