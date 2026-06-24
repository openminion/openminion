"""MCP fleet manager."""

import atexit
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openminion.base.config.mcp import MCPServerConfig

from .elicitation import OpenMinionElicitationHandler
from .interfaces import (
    MCPCapabilityChangeListener,
    MCPClientCapabilityState,
    MCPProgressListener,
)
from .sampling import sampling_handler_from_runtime_config
from .schemas import (
    MCPCompletionResult,
    MCPListedPrompt,
    MCPListedResource,
    MCPListedResourceTemplate,
    MCPListedTool,
    MCPLogMessage,
    MCPResourceUpdate,
    MCPRoot,
    build_mcp_runtime_prompt_name,
    build_mcp_runtime_resource_name,
    build_mcp_runtime_resource_template_name,
    build_mcp_runtime_tool_name,
)
from .session import (
    MCPCallError,
    MCPManagerError,
    MCPServerSession,
    _resolve_mcp_tool_posture,
)
from .transport import (
    MCPAuthorizationError,
    MCPProtocolError,
    MCPRemoteTransportError,
    MCPServerUnavailableError,
    MCPTimeoutError,
)


@dataclass(frozen=True)
class MCPServerError:
    server_name: str
    reason_code: str
    message: str


@dataclass(frozen=True)
class _DiscoveryCacheEntry:
    primitive: str
    items: tuple[Any, ...]
    cached_at: float
    ttl_seconds: float


def _percentile(samples: list[float], percentile: int) -> float:
    if not samples:
        return 0.0
    ordered = sorted(float(value) for value in samples)
    index = max(
        0, min(len(ordered) - 1, round((percentile / 100) * (len(ordered) - 1)))
    )
    return round(ordered[index], 3)


class MCPFleetManager:
    def __init__(
        self,
        servers: list[MCPServerConfig],
        *,
        client_capability_state: MCPClientCapabilityState | None = None,
        capability_change_listener: MCPCapabilityChangeListener | None = None,
        progress_listener: MCPProgressListener | None = None,
        discovery_cache_ttl_seconds: float = 0.0,
        deferred_discovery_enabled: bool = False,
    ) -> None:
        self._client_capability_state = (
            client_capability_state or _default_client_capability_state(servers)
        )
        self._capability_change_listener = capability_change_listener
        self._progress_listener = progress_listener
        self._failed_servers: dict[str, MCPServerError] = {}
        self._capability_change_events: list[dict[str, Any]] = []
        self._tool_catalog_by_server: dict[str, tuple[str, ...]] = {}
        self._prompt_catalog_by_server: dict[str, tuple[str, ...]] = {}
        self._resource_catalog_by_server: dict[str, tuple[str, ...]] = {}
        self._metrics_by_server: dict[str, dict[str, Any]] = {}
        self._live_registry: Any | None = None
        self._discovery_cache_ttl_seconds = max(
            0.0,
            float(discovery_cache_ttl_seconds or 0.0),
        )
        self._deferred_discovery_enabled = bool(deferred_discovery_enabled)
        self._discovery_cache: dict[str, _DiscoveryCacheEntry] = {}
        self._sessions = {
            server.name: MCPServerSession(
                server,
                client_capability_state=self._client_capability_state,
                capability_change_handler=self._on_capability_change,
                progress_listener=self._progress_listener,
            )
            for server in servers
        }
        self._registered_atexit = False
        if self._sessions:
            atexit.register(self.close)
            self._registered_atexit = True

    def server_config(self, server_name: str) -> MCPServerConfig | None:
        session = self._sessions.get(str(server_name or "").strip())
        if session is None:
            return None
        return session._server

    def attach_registry(self, registry: Any) -> None:
        self._live_registry = registry

    @property
    def client_capability_state(self) -> MCPClientCapabilityState:
        return self._client_capability_state

    @property
    def failed_servers(self) -> dict[str, MCPServerError]:
        return dict(self._failed_servers)

    @classmethod
    def from_runtime_config(cls, runtime_config: Any | None) -> "MCPFleetManager":
        servers = list(getattr(runtime_config, "mcp_servers", []) or [])
        sampling_handler = sampling_handler_from_runtime_config(runtime_config)
        default_state = _default_client_capability_state(servers)
        client_capability_state = (
            MCPClientCapabilityState(
                roots=default_state.roots,
                sampling_handler=sampling_handler,
                elicitation_handler=default_state.elicitation_handler,
                elicitation_url_supported=default_state.elicitation_url_supported,
            )
            if sampling_handler is not None
            else None
        )
        return cls(
            servers=servers,
            client_capability_state=client_capability_state,
            discovery_cache_ttl_seconds=float(
                getattr(runtime_config, "mcp_discovery_cache_ttl_seconds", 0.0) or 0.0
            ),
            deferred_discovery_enabled=bool(
                getattr(runtime_config, "mcp_deferred_discovery_enabled", False)
            ),
        )

    def bind_sampling_executor(self, executor: Any) -> None:
        handler = self._client_capability_state.sampling_handler
        bind = getattr(handler, "bind_executor", None)
        if callable(bind):
            bind(executor)

    def bind_elicitation_responder(
        self,
        responder: Any,
        *,
        url_supported: bool = False,
    ) -> None:
        handler = self._client_capability_state.elicitation_handler
        bind = getattr(handler, "bind_responder", None)
        if callable(bind):
            bind(responder)
        set_url_supported = getattr(handler, "set_url_supported", None)
        if callable(set_url_supported):
            set_url_supported(bool(url_supported))

    def mcp_sampling_events(self) -> list[dict[str, Any]]:
        handler = self._client_capability_state.sampling_handler
        events_fn = getattr(handler, "events", None)
        if not callable(events_fn):
            return []
        return [
            {
                "server_name": str(getattr(item, "server_name", "") or "").strip(),
                "mode": str(getattr(item, "mode", "") or "").strip(),
                "allowed": bool(getattr(item, "allowed", False)),
                "model": str(getattr(item, "model", "") or "").strip(),
                "stop_reason": str(getattr(item, "stop_reason", "") or "").strip(),
                "message_count": int(getattr(item, "message_count", 0) or 0),
                "timestamp": float(getattr(item, "timestamp", 0.0) or 0.0),
            }
            for item in events_fn()
        ]

    def mcp_elicitation_events(self) -> list[dict[str, Any]]:
        handler = self._client_capability_state.elicitation_handler
        events_fn = getattr(handler, "events", None)
        if not callable(events_fn):
            return []
        return [
            {
                "server_name": str(getattr(item, "server_name", "") or "").strip(),
                "mode": str(getattr(item, "mode", "") or "").strip(),
                "request_mode": str(getattr(item, "request_mode", "") or "").strip(),
                "action": str(getattr(item, "action", "") or "").strip(),
                "elicitation_id": str(
                    getattr(item, "elicitation_id", "") or ""
                ).strip(),
                "timestamp": float(getattr(item, "timestamp", 0.0) or 0.0),
            }
            for item in events_fn()
        ]

    def has_servers(self) -> bool:
        return bool(self._sessions)

    def discover_tools(self, *, parallel: bool = False) -> list[MCPListedTool]:
        discovered = self._discover_primitive(
            primitive="tools",
            parallel=parallel,
        )
        return [item for item in discovered if isinstance(item, MCPListedTool)]

    def discover_prompts(self, *, parallel: bool = False) -> list[MCPListedPrompt]:
        discovered = self._discover_primitive(
            primitive="prompts",
            parallel=parallel,
        )
        return [item for item in discovered if isinstance(item, MCPListedPrompt)]

    def discover_resources(self, *, parallel: bool = False) -> list[MCPListedResource]:
        discovered = self._discover_primitive(
            primitive="resources",
            parallel=parallel,
        )
        return [item for item in discovered if isinstance(item, MCPListedResource)]

    def discover_resource_templates(
        self, *, parallel: bool = False
    ) -> list[MCPListedResourceTemplate]:
        discovered = self._discover_primitive(
            primitive="resource_templates",
            parallel=parallel,
        )
        return [
            item for item in discovered if isinstance(item, MCPListedResourceTemplate)
        ]

    def call_tool(
        self,
        *,
        server_name: str,
        remote_name: str,
        arguments: dict[str, Any],
        progress_token: str = "",
    ) -> dict[str, Any]:
        session = self._require_session(server_name)
        started = time.monotonic()
        try:
            result = session.call_tool(
                remote_name=remote_name,
                arguments=arguments,
                progress_token=progress_token,
            )
        except Exception:
            self._record_call_metric(
                server_name=session.server_name,
                started=started,
                ok=False,
                restart_total=session._restart_total,
            )
            raise
        self._record_call_metric(
            server_name=session.server_name,
            started=started,
            ok=True,
            restart_total=session._restart_total,
        )
        return result

    def get_prompt(
        self,
        *,
        server_name: str,
        remote_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        session = self._require_session(server_name)
        return session.get_prompt(remote_name=remote_name, arguments=arguments)

    def read_resource(
        self,
        *,
        server_name: str,
        resource_uri: str,
    ) -> dict[str, Any]:
        session = self._require_session(server_name)
        return session.read_resource(resource_uri=resource_uri)

    def subscribe_resource(self, *, server_name: str, resource_uri: str) -> None:
        session = self._require_session(server_name)
        session.subscribe_resource(resource_uri=resource_uri)

    def unsubscribe_resource(self, *, server_name: str, resource_uri: str) -> None:
        session = self._require_session(server_name)
        session.unsubscribe_resource(resource_uri=resource_uri)

    def set_log_level(self, *, server_name: str, level: str) -> None:
        session = self._require_session(server_name)
        session.set_log_level(level)

    def complete(
        self,
        *,
        server_name: str,
        ref_type: str,
        ref_name: str,
        argument_name: str,
        argument_value: str = "",
        context_arguments: dict[str, Any] | None = None,
    ) -> MCPCompletionResult:
        session = self._require_session(server_name)
        return session.complete(
            ref_type=ref_type,
            ref_name=ref_name,
            argument_name=argument_name,
            argument_value=argument_value,
            context_arguments=context_arguments,
        )

    def mcp_server_metrics(self) -> dict[str, dict[str, Any]]:
        snapshot: dict[str, dict[str, Any]] = {}
        for server_name, payload in self._metrics_by_server.items():
            latencies = list(payload.get("latencies_ms", []) or [])
            snapshot[server_name] = {
                "call_total": int(payload.get("call_total", 0) or 0),
                "call_error_total": int(payload.get("call_error_total", 0) or 0),
                "call_latency_ms_p50": _percentile(latencies, 50),
                "call_latency_ms_p95": _percentile(latencies, 95),
                "restart_total": int(payload.get("restart_total", 0) or 0),
            }
        return snapshot

    def mcp_server_logs(self, *, limit: int = 10) -> dict[str, list[MCPLogMessage]]:
        return {
            server_name: session.recent_log_messages(limit=limit)
            for server_name, session in self._sessions.items()
        }

    def mcp_resource_updates(
        self, *, limit: int = 10
    ) -> dict[str, list[MCPResourceUpdate]]:
        return {
            server_name: session.recent_resource_updates(limit=limit)
            for server_name, session in self._sessions.items()
        }

    def capability_change_events(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._capability_change_events]

    @property
    def deferred_discovery_enabled(self) -> bool:
        return self._deferred_discovery_enabled

    def invalidate_discovery_cache(self, primitive: str | None = None) -> None:
        token = str(primitive or "").strip()
        if token:
            self._discovery_cache.pop(token, None)
            return
        self._discovery_cache.clear()

    def discovery_cache_snapshot(self) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        return {
            primitive: {
                "item_count": len(entry.items),
                "cached_at": entry.cached_at,
                "ttl_seconds": entry.ttl_seconds,
                "stale": bool(now - entry.cached_at > entry.ttl_seconds),
            }
            for primitive, entry in self._discovery_cache.items()
        }

    def close_server(self, server_name: str) -> None:
        session = self._sessions.get(str(server_name or "").strip())
        if session is None:
            return
        session.close(reset_initialized=False)

    def close(self) -> None:
        for session in self._sessions.values():
            session.close(reset_initialized=True)

    def _require_session(self, server_name: str) -> MCPServerSession:
        session = self._sessions.get(str(server_name or "").strip())
        if session is None:
            raise MCPServerUnavailableError(
                f"MCP server '{server_name}' is not configured.",
                reason_code="mcp_server_not_configured",
            )
        return session

    def _discover_primitive(
        self,
        *,
        primitive: str,
        parallel: bool,
    ) -> list[Any]:
        cached = self._cached_discovery(primitive)
        if cached is not None:
            return list(cached)

        if not parallel:
            discovered: list[Any] = []
            for server_name, session in self._sessions.items():
                items = self._discover_for_session(primitive=primitive, session=session)
                discovered.extend(items)
                self._update_catalog(
                    primitive=primitive,
                    server_name=server_name,
                    items=items,
                )
            self._store_discovery_cache(primitive=primitive, items=discovered)
            return discovered

        discovered: list[Any] = []
        max_workers = min(max(len(self._sessions), 1), 8)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    self._discover_for_session, primitive=primitive, session=session
                ): server_name
                for server_name, session in self._sessions.items()
            }
            for future in as_completed(future_map):
                server_name = future_map[future]
                try:
                    items = future.result()
                except (
                    MCPServerUnavailableError,
                    MCPTimeoutError,
                    MCPProtocolError,
                ) as exc:
                    self._failed_servers[server_name] = MCPServerError(
                        server_name=server_name,
                        reason_code=str(getattr(exc, "reason_code", "") or primitive),
                        message=str(exc),
                    )
                    continue
                discovered.extend(items)
                self._update_catalog(
                    primitive=primitive,
                    server_name=server_name,
                    items=items,
                )
        self._store_discovery_cache(primitive=primitive, items=discovered)
        return discovered

    def _cached_discovery(self, primitive: str) -> tuple[Any, ...] | None:
        if self._discovery_cache_ttl_seconds <= 0:
            return None
        entry = self._discovery_cache.get(primitive)
        if entry is None:
            return None
        if time.monotonic() - entry.cached_at > entry.ttl_seconds:
            self._discovery_cache.pop(primitive, None)
            return None
        return tuple(entry.items)

    def _store_discovery_cache(self, *, primitive: str, items: list[Any]) -> None:
        if self._discovery_cache_ttl_seconds <= 0:
            return
        self._discovery_cache[primitive] = _DiscoveryCacheEntry(
            primitive=primitive,
            items=tuple(items),
            cached_at=time.monotonic(),
            ttl_seconds=self._discovery_cache_ttl_seconds,
        )

    def _discover_for_session(
        self,
        *,
        primitive: str,
        session: MCPServerSession,
    ) -> list[Any]:
        if primitive == "tools":
            return list(session.list_tools())
        if primitive == "prompts":
            return list(session.list_prompts())
        if primitive == "resources":
            return list(session.list_resources())
        if primitive == "resource_templates":
            return list(session.list_resource_templates())
        raise ValueError(f"Unsupported MCP primitive: {primitive}")

    def _update_catalog(
        self,
        *,
        primitive: str,
        server_name: str,
        items: list[Any],
    ) -> None:
        if primitive == "tools":
            self._tool_catalog_by_server[server_name] = tuple(
                sorted(str(item.remote_name or "").strip() for item in items)
            )
            return
        if primitive == "prompts":
            self._prompt_catalog_by_server[server_name] = tuple(
                sorted(str(item.remote_name or "").strip() for item in items)
            )
            return
        if primitive == "resources":
            self._resource_catalog_by_server[server_name] = tuple(
                sorted(str(item.resource_uri or "").strip() for item in items)
            )
            return
        if primitive == "resource_templates":
            self._resource_catalog_by_server[server_name] = tuple(
                sorted(str(item.uri_template or "").strip() for item in items)
            )
            return

    def _on_capability_change(self, *, server_name: str, primitive: str) -> None:
        thread = threading.Thread(
            target=self._refresh_capability_change,
            kwargs={"server_name": server_name, "primitive": primitive},
            daemon=True,
        )
        thread.start()

    def _refresh_capability_change(self, *, server_name: str, primitive: str) -> None:
        session = self._sessions.get(server_name)
        if session is None:
            return
        self.invalidate_discovery_cache(primitive)
        old_catalog = set(self._catalog_for(primitive, server_name))
        try:
            items = self._discover_for_session(primitive=primitive, session=session)
        except (MCPServerUnavailableError, MCPTimeoutError, MCPProtocolError) as exc:
            self._failed_servers[server_name] = MCPServerError(
                server_name=server_name,
                reason_code=str(getattr(exc, "reason_code", "") or primitive),
                message=str(exc),
            )
            return
        self._update_catalog(primitive=primitive, server_name=server_name, items=items)
        new_catalog = set(self._catalog_for(primitive, server_name))
        added = tuple(sorted(new_catalog - old_catalog))
        removed = tuple(sorted(old_catalog - new_catalog))
        self._apply_live_registry_delta(
            primitive=primitive,
            server_name=server_name,
            items=items,
            added=added,
            removed=removed,
        )
        event = {
            "server_name": server_name,
            "primitive": primitive,
            "added": list(added),
            "removed": list(removed),
        }
        self._capability_change_events.append(event)
        listener = self._capability_change_listener
        if listener is not None:
            listener.capability_changed(
                server_name=server_name,
                primitive=primitive,
                added=added,
                removed=removed,
            )

    def _apply_live_registry_delta(
        self,
        *,
        primitive: str,
        server_name: str,
        items: list[Any],
        added: tuple[str, ...],
        removed: tuple[str, ...],
    ) -> None:
        registry = self._live_registry
        if registry is None:
            return
        register = getattr(registry, "register", None)
        unregister = getattr(registry, "unregister", None)
        if not callable(register) or not callable(unregister):
            return

        live_items = {
            _mcp_catalog_key_for_item(primitive=primitive, item=item): item
            for item in items
        }
        for token in removed:
            runtime_name = _mcp_runtime_name_for_catalog_token(
                primitive=primitive,
                server_name=server_name,
                token=token,
                item=None,
            )
            if runtime_name:
                unregister(runtime_name)
        for token in added:
            item = live_items.get(token)
            if item is None:
                continue
            spec = _mcp_tool_spec_for_item(manager=self, primitive=primitive, item=item)
            if spec is None:
                continue
            try:
                register(spec)
            except Exception:
                continue

    def _catalog_for(self, primitive: str, server_name: str) -> tuple[str, ...]:
        if primitive == "tools":
            return self._tool_catalog_by_server.get(server_name, ())
        if primitive == "prompts":
            return self._prompt_catalog_by_server.get(server_name, ())
        if primitive == "resources":
            return self._resource_catalog_by_server.get(server_name, ())
        if primitive == "resource_templates":
            return self._resource_catalog_by_server.get(server_name, ())
        return ()

    def _record_call_metric(
        self,
        *,
        server_name: str,
        started: float,
        ok: bool,
        restart_total: int,
    ) -> None:
        payload = self._metrics_by_server.setdefault(
            server_name,
            {
                "call_total": 0,
                "call_error_total": 0,
                "latencies_ms": deque(maxlen=128),
                "restart_total": 0,
            },
        )
        payload["call_total"] = int(payload.get("call_total", 0) or 0) + 1
        if not ok:
            payload["call_error_total"] = (
                int(payload.get("call_error_total", 0) or 0) + 1
            )
        payload["latencies_ms"].append((time.monotonic() - started) * 1000.0)
        payload["restart_total"] = max(
            int(payload.get("restart_total", 0) or 0), restart_total
        )


__all__ = [
    "MCPAuthorizationError",
    "MCPCallError",
    "MCPFleetManager",
    "MCPManagerError",
    "MCPProtocolError",
    "MCPRemoteTransportError",
    "MCPServerUnavailableError",
    "MCPTimeoutError",
    "_resolve_mcp_tool_posture",
]


def _default_client_capability_state(
    servers: list[MCPServerConfig],
) -> MCPClientCapabilityState:
    roots: list[MCPRoot] = []
    seen_uris: set[str] = set()
    candidate_paths = [str(server.cwd or "").strip() for server in servers]
    if not any(candidate_paths):
        candidate_paths.append(str(Path.cwd()))
    for raw_path in candidate_paths:
        if not raw_path:
            continue
        resolved = Path(raw_path).expanduser().resolve(strict=False)
        try:
            uri = resolved.as_uri()
        except ValueError:
            continue
        if uri in seen_uris:
            continue
        seen_uris.add(uri)
        roots.append(
            MCPRoot(
                uri=uri,
                name=resolved.name or str(resolved),
            )
        )
    return MCPClientCapabilityState(
        roots=tuple(roots),
        elicitation_handler=OpenMinionElicitationHandler(mode="decline"),
    )


def _mcp_catalog_key_for_item(*, primitive: str, item: Any) -> str:
    if primitive == "tools" and isinstance(item, MCPListedTool):
        return str(item.remote_name or "").strip()
    if primitive == "prompts" and isinstance(item, MCPListedPrompt):
        return str(item.remote_name or "").strip()
    if primitive == "resources" and isinstance(item, MCPListedResource):
        return str(item.resource_uri or "").strip()
    if primitive == "resource_templates" and isinstance(
        item, MCPListedResourceTemplate
    ):
        return str(item.uri_template or "").strip()
    return ""


def _mcp_runtime_name_for_catalog_token(
    *,
    primitive: str,
    server_name: str,
    token: str,
    item: Any | None,
) -> str:
    if primitive == "tools":
        remote_name = str(getattr(item, "remote_name", "") or token).strip()
        return build_mcp_runtime_tool_name(
            server_name=server_name or str(getattr(item, "server_name", "") or ""),
            remote_name=remote_name,
        )
    if primitive == "prompts":
        remote_name = str(getattr(item, "remote_name", "") or token).strip()
        return build_mcp_runtime_prompt_name(
            server_name=server_name or str(getattr(item, "server_name", "") or ""),
            remote_name=remote_name,
        )
    if primitive == "resources":
        resource_uri = str(getattr(item, "resource_uri", "") or token).strip()
        resource_name = str(getattr(item, "resource_name", "") or "").strip()
        return build_mcp_runtime_resource_name(
            server_name=server_name or str(getattr(item, "server_name", "") or ""),
            resource_uri=resource_uri,
            resource_name=resource_name,
        )
    if primitive == "resource_templates":
        uri_template = str(getattr(item, "uri_template", "") or token).strip()
        template_name = str(getattr(item, "template_name", "") or "").strip()
        return build_mcp_runtime_resource_template_name(
            server_name=server_name or str(getattr(item, "server_name", "") or ""),
            uri_template=uri_template,
            template_name=template_name,
        )
    return ""


def _mcp_tool_spec_for_item(*, manager: Any, primitive: str, item: Any) -> Any | None:
    from .plugin import (
        build_mcp_prompt_spec,
        build_mcp_resource_spec,
        build_mcp_resource_template_spec,
        build_mcp_tool_spec,
    )

    if primitive == "tools" and isinstance(item, MCPListedTool):
        return build_mcp_tool_spec(manager=manager, tool=item)
    if primitive == "prompts" and isinstance(item, MCPListedPrompt):
        return build_mcp_prompt_spec(manager=manager, prompt=item)
    if primitive == "resources" and isinstance(item, MCPListedResource):
        return build_mcp_resource_spec(manager=manager, resource=item)
    if primitive == "resource_templates" and isinstance(
        item, MCPListedResourceTemplate
    ):
        return build_mcp_resource_template_spec(manager=manager, template=item)
    return None
