import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Mapping

from pydantic import ValidationError

from openminion.modules.tool.base import (
    Tool,
    ToolCategoryInfo,
    ToolExecutionContext,
    ToolExecutionPolicy,
    ToolExecutionResult,
)
from openminion.modules.tool.family.events import emit_family_event
from openminion.modules.tool.runtime.context import RuntimeContext
from openminion.tools.config import resolve_tool_env

from .models import (
    ArtifactRef,
    BrowserCallArgs,
    BrowserError,
    BrowserResult,
    InstanceInfo,
    SUPPORTED_OPS,
    TextResult,
)
from .dispatch import BrowserDispatch
from .constants import (
    DEFAULT_BROWSER_SESSION_STATE_RELATIVE_PATH,
    OPENMINION_BROWSER_DEFAULT_PROVIDER_ENV,
)
from .providers import BrowserProvider, BrowserProviderContext, BrowserProviderRegistry
from .router import BrowserRouter, BrowserRoutingConfig
from .payloads import (
    extract_instance_id,
    extract_instances,
    extract_tab_id,
    extract_tabs,
    is_stale_recoverable_error,
)
from .session_state import BrowserSessionStateStore
from .tab_resolver import BrowserTabResolver
from .runtime import (
    BrowserToolError,
    _BrowserExecutionContext,
    _agent_profile_provider,
    _artifact_kind,
    _as_bool,
    _clear_session_state,
    _coerce_execution_context,
    _emit_event,
    _enforce_capabilities,
    _filter_tabs,
    _hydrate_call_with_session_state,
    _instance_spec,
    _materialize_artifact,
    _raise_capability_error,
    _remember_affinity,
    _remember_session_state,
    _resolve_tab,
    _runtime_context_from_execution_context,
    _runtime_env_from_context,
    _runtime_provider_preferences,
    _session_provider_override,
    _snapshot_result,
    _state_for,
    _state_key,
    _tab_info,
    _workspace_output_path,
    _workspace_root,
)

_LOG = logging.getLogger(__name__)

_BROWSER_OPS = tuple(dict.fromkeys(SUPPORTED_OPS))

BROWSER_TOOL_INPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "op": {"type": "string", "enum": list(_BROWSER_OPS)},
        "provider": {"type": "string"},
        "instance_id": {"type": "string"},
        "tab_id": {"type": "string"},
        "url": {"type": "string"},
        "instance": {
            "type": "object",
            "properties": {
                "profile": {"type": "string"},
                "mode": {"type": "string"},
                "port": {"type": "integer"},
                "user_data_dir": {"type": "string"},
                "downloads_path": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "profile": {"type": "string"},
        "mode": {"type": "string"},
        "port": {"type": "integer"},
        "snapshot": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["auto", "refs", "a11y", "dom", "min"],
                },
                "compact": {"type": "boolean"},
                "interactive": {"type": "boolean"},
                "max_nodes": {"type": "integer"},
                "max_text_chars": {"type": "integer"},
                "depth": {"type": "integer"},
                "max_tokens": {"type": "integer"},
            },
            "additionalProperties": True,
        },
        "text": {
            "type": "object",
            "properties": {
                "mode": {"type": "string"},
                "include_text": {"type": "boolean"},
                "max_chars": {"type": "integer"},
            },
            "additionalProperties": True,
        },
        "navigation": {
            "type": "object",
            "properties": {
                "timeout_ms": {"type": "integer"},
                "wait_until": {"type": "string"},
            },
            "additionalProperties": True,
        },
        "action": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "target": {
                    "type": "object",
                    "properties": {
                        "ref": {"type": "string"},
                        "selector": {"type": "string"},
                        "role": {
                            "type": "object",
                            "properties": {
                                "role": {"type": "string"},
                                "name": {"type": "string"},
                                "exact": {"type": "boolean"},
                            },
                            "required": ["role"],
                            "additionalProperties": False,
                        },
                    },
                    "additionalProperties": True,
                },
                "text": {"type": "string"},
                "key": {"type": "string"},
                "option": {"type": "string"},
                "delta": {"type": "integer"},
            },
            "required": ["kind"],
            "additionalProperties": True,
        },
        "actions": {
            "type": "array",
            "items": {"type": "object"},
        },
        "output": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "format": {"type": "string", "enum": ["png", "jpg", "pdf"]},
                "quality": {"type": "integer"},
            },
            "additionalProperties": False,
        },
        "owner": {"type": "string"},
        "ttl_s": {"type": "integer"},
        "options": {"type": "object"},
    },
    "required": ["op"],
    "additionalProperties": False,
}


_SESSION_STATE_RELATIVE_PATH = DEFAULT_BROWSER_SESSION_STATE_RELATIVE_PATH


@dataclass
class BrowserTool(Tool):
    name = "browser"
    description = (
        "Provider-neutral browser automation for interactive or visual web tasks. "
        "Use web.fetch for static URL content retrieval that does not require page "
        "interaction."
    )
    parameters = BROWSER_TOOL_INPUT_SCHEMA
    policy = ToolExecutionPolicy(
        required_scopes_all=(
            "tool.execute",
            "tool.browser.control",
            "tool.net.connect",
            "tool.fs.write",
        ),
        risk="high",
        budget_cost=3,
    )
    categories = ToolCategoryInfo(
        primary_category="browser",
        secondary_categories=("automation",),
    )

    router: BrowserRouter
    session_state_store: BrowserSessionStateStore | None = None
    tab_resolver: BrowserTabResolver | None = None
    dispatcher: BrowserDispatch | None = None

    _tab_info = _tab_info
    _snapshot_result = _snapshot_result
    _materialize_artifact = _materialize_artifact
    _workspace_output_path = _workspace_output_path
    _workspace_root = _workspace_root
    _agent_profile_provider = _agent_profile_provider
    _session_provider_override = _session_provider_override
    _runtime_provider_preferences = _runtime_provider_preferences
    _enforce_capabilities = _enforce_capabilities
    _raise_capability_error = _raise_capability_error
    _remember_affinity = _remember_affinity
    _state_key = _state_key
    _state_for = _state_for
    _hydrate_call_with_session_state = _hydrate_call_with_session_state
    _remember_session_state = _remember_session_state
    _clear_session_state = _clear_session_state
    _runtime_env_from_context = _runtime_env_from_context
    _resolve_tab = _resolve_tab
    _filter_tabs = _filter_tabs
    _as_bool = _as_bool
    _emit_event = _emit_event
    _instance_spec = _instance_spec
    _artifact_kind = _artifact_kind
    _coerce_execution_context = _coerce_execution_context
    _runtime_context_from_execution_context = _runtime_context_from_execution_context

    def __post_init__(self) -> None:
        if self.session_state_store is None:
            self.session_state_store = _SESSION_STATE_STORE
        if self.tab_resolver is None:
            self.tab_resolver = BrowserTabResolver(
                state_lookup=lambda provider_id, ctx: self._state_for(
                    provider_id=provider_id,
                    ctx=ctx,
                ),
                extract_tabs=lambda payload: extract_tabs(
                    payload, to_tab_info=self._tab_info
                ),
                is_stale_recoverable_error=is_stale_recoverable_error,
            )
        if self.dispatcher is None:
            self.dispatcher = BrowserDispatch(
                state_for=lambda provider_id, ctx: self._state_for(
                    provider_id=provider_id, ctx=ctx
                ),
                instance_spec=lambda call, ctx: self._instance_spec(call, ctx=ctx),
                resolve_tab=lambda provider, provider_ctx, call, prefer_url: (
                    self._resolve_tab(
                        provider=provider,
                        provider_ctx=provider_ctx,
                        call=call,
                        prefer_url=prefer_url,
                    )
                ),
                filter_tabs=lambda tabs, options: self._filter_tabs(
                    tabs, options=options
                ),
                as_bool=lambda value, default: self._as_bool(value, default=default),
                clear_session_state=lambda provider_id, ctx: self._clear_session_state(
                    provider_id=provider_id, ctx=ctx
                ),
                extract_tabs=lambda payload: extract_tabs(
                    payload, to_tab_info=self._tab_info
                ),
                extract_instances=extract_instances,
                extract_instance_id=extract_instance_id,
                extract_tab_id=extract_tab_id,
                is_stale_recoverable_error=is_stale_recoverable_error,
                error_factory=lambda code, message, details: BrowserToolError(
                    code, message, details
                ),
            )

    def execute(
        self,
        arguments: Mapping[str, Any],
        context: ToolExecutionContext | _BrowserExecutionContext,
    ) -> ToolExecutionResult:
        args = dict(arguments or {})
        ctx = self._coerce_execution_context(context)
        payload = self._execute_browser(args=args, ctx=ctx)
        data = payload.get("data")
        normalized_data = dict(data) if isinstance(data, Mapping) else {}
        error_payload = payload.get("error")
        error_message = ""
        if isinstance(error_payload, Mapping):
            normalized_data.setdefault("error", dict(error_payload))
            error_message = str(
                error_payload.get("message") or error_payload.get("code") or ""
            )
        elif error_payload:
            error_message = str(error_payload)
        if not error_message and not bool(payload.get("ok")):
            error_message = "browser tool execution failed"
        content = ""
        if normalized_data:
            try:
                content = json.dumps(normalized_data, sort_keys=True, default=str)
            except Exception:
                content = str(normalized_data)
        return ToolExecutionResult(
            tool_name=self.name,
            ok=bool(payload.get("ok")),
            content=content,
            verified=bool(payload.get("ok")),
            error=error_message,
            data=normalized_data,
            source="tool.browser",
        )

    def _execute_browser(
        self,
        *,
        args: Dict[str, Any],
        ctx: _BrowserExecutionContext,
    ) -> dict[str, Any]:
        _ensure_discovered_providers()
        call: BrowserCallArgs | None = None
        provider_id = ""
        try:
            call = BrowserCallArgs.model_validate(args)
            runtime_default_provider, runtime_provider_order = (
                self._runtime_provider_preferences(ctx)
            )
            provider = self.router.select_provider(
                requested_provider=call.provider,
                agent_profile_provider=self._agent_profile_provider(ctx),
                session_provider_override=self._session_provider_override(ctx),
                instance_id=call.instance_id,
                tab_id=call.tab_id,
                runtime_default_provider=runtime_default_provider,
                runtime_provider_order=runtime_provider_order,
            )
            provider_id = provider.provider_id
            emit_family_event(
                ctx.runtime,
                event="tool.browser.provider.selected",
                payload={
                    "requested_provider": str(call.provider or "").strip(),
                    "selected_provider": provider.provider_id,
                    "op": call.op,
                },
            )
            provider_ctx = BrowserProviderContext(
                tool_context=ctx.runtime
                if isinstance(ctx.runtime, RuntimeContext)
                else None,
                workspace_root=self._workspace_root(ctx),
                trace_id=ctx.trace_id,
                session_id=ctx.session_id,
                extras=dict(ctx.extras),
            )
            call = self._hydrate_call_with_session_state(
                provider=provider, provider_ctx=provider_ctx, call=call
            )
            self._emit_event(
                "requested",
                provider_id=provider.provider_id,
                call=call,
                payload={"args": dict(args)},
                ctx=ctx,
            )
            self._enforce_capabilities(provider=provider, call=call)
            payload = self._dispatch(
                provider=provider, provider_ctx=provider_ctx, call=call
            )
            result = self._normalize(
                provider=provider, call=call, payload=payload, ctx=ctx
            )
            self._remember_affinity(provider_id=provider.provider_id, result=result)
            self._remember_session_state(
                provider_id=provider.provider_id, call=call, result=result, ctx=ctx
            )
            self._emit_event(
                "completed",
                provider_id=provider.provider_id,
                call=call,
                payload={
                    "instance_id": result.instance.id if result.instance else None,
                    "tab_id": result.tab.id if result.tab else None,
                },
                ctx=ctx,
            )
            return {"ok": True, "data": result.model_dump(exclude_none=True)}
        except ValidationError as exc:
            return {
                "ok": False,
                "error": {
                    "code": "INVALID_ARGUMENT",
                    "message": f"validation_error: {exc}",
                    "details": {},
                },
            }
        except BrowserToolError as exc:
            self._emit_event(
                "failed",
                provider_id=provider_id,
                call=call,
                payload={"error": exc.message},
                ctx=ctx,
            )
            return {
                "ok": False,
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                },
            }
        except KeyError as exc:
            self._emit_event(
                "failed",
                provider_id=provider_id,
                call=call,
                payload={"error": str(exc)},
                ctx=ctx,
            )
            return {
                "ok": False,
                "error": {"code": "NOT_FOUND", "message": str(exc), "details": {}},
            }
        except Exception as exc:  # pragma: no cover - protective catch
            self._emit_event(
                "failed",
                provider_id=str(args.get("provider", "")).strip(),
                call=None,
                payload={"error": f"{type(exc).__name__}: {exc}"},
                ctx=ctx,
            )
            return {
                "ok": False,
                "error": {
                    "code": "EXEC_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                    "details": {},
                },
            }

    def _dispatch(
        self,
        *,
        provider: BrowserProvider,
        provider_ctx: BrowserProviderContext,
        call: BrowserCallArgs,
    ) -> dict[str, Any]:
        assert self.dispatcher is not None
        return self.dispatcher.dispatch(
            provider=provider,
            provider_ctx=provider_ctx,
            call=call,
        )

    def _normalize(
        self,
        *,
        provider: BrowserProvider,
        call: BrowserCallArgs,
        payload: dict[str, Any],
        ctx: _BrowserExecutionContext,
    ) -> BrowserResult:
        out = BrowserResult(
            provider=provider.provider_id,
            capabilities=provider.capabilities,
            data={"op": call.op},
        )

        instance = payload.get("instance")
        if isinstance(instance, Mapping):
            out.instance = InstanceInfo(
                id=str(instance.get("id", "")),
                profile=str(instance.get("profile"))
                if instance.get("profile") is not None
                else None,
                mode=str(instance.get("mode"))
                if instance.get("mode") is not None
                else None,
            )
        elif isinstance(payload.get("instance_id"), str):
            out.instance = InstanceInfo(id=str(payload["instance_id"]))
        instances = payload.get("instances")
        if isinstance(instances, list):
            out.instances = [
                InstanceInfo(
                    id=str(
                        row.get("id")
                        or row.get("instance_id")
                        or row.get("instanceId")
                        or ""
                    ),
                    profile=str(row.get("profile"))
                    if row.get("profile") is not None
                    else None,
                    mode=str(row.get("mode")) if row.get("mode") is not None else None,
                )
                for row in instances
                if isinstance(row, Mapping)
                and str(
                    row.get("id")
                    or row.get("instance_id")
                    or row.get("instanceId")
                    or ""
                ).strip()
            ]

        tab = payload.get("tab")
        if isinstance(tab, Mapping):
            out.tab = self._tab_info(tab)
        elif isinstance(payload.get("tab_id"), str):
            out.tab = self._tab_info(payload)

        tabs = payload.get("tabs")
        if isinstance(tabs, list):
            out.tabs = [self._tab_info(row) for row in tabs if isinstance(row, Mapping)]

        snapshot = payload.get("snapshot")
        if isinstance(snapshot, Mapping):
            out.snapshot = self._snapshot_result(snapshot)
        elif payload.get("root") is not None:
            out.snapshot = self._snapshot_result(payload)

        text = payload.get("text")
        if isinstance(text, Mapping):
            content = str(text.get("content", ""))
            out.text = TextResult(
                content=content,
                truncated=bool(text.get("truncated", False)),
                chars=int(text.get("chars", len(content))),
            )
        elif isinstance(text, str):
            out.text = TextResult(content=text, truncated=False, chars=len(text))

        artifact = payload.get("artifact")
        if isinstance(artifact, Mapping):
            kind = str(artifact.get("kind") or self._artifact_kind(call.op))
            artifact_path = str(artifact.get("path", "")).strip()
            if call.output and call.output.path:
                artifact_path = self._workspace_output_path(ctx, call.output.path)
            elif artifact_path:
                artifact_path = self._workspace_output_path(ctx, artifact_path)
            out.artifact = ArtifactRef(
                kind=kind,
                path=artifact_path,
                sha256=str(artifact.get("sha256"))
                if artifact.get("sha256") is not None
                else None,
                mime=str(artifact.get("mime"))
                if artifact.get("mime") is not None
                else None,
            )

        if isinstance(payload.get("content"), (bytes, bytearray)):
            blob = bytes(payload["content"])
            out.artifact = self._materialize_artifact(
                blob=blob,
                kind=str(payload.get("kind") or self._artifact_kind(call.op)),
                output_path=call.output.path if call.output else None,
                ctx=ctx,
            )

        if isinstance(payload.get("error"), Mapping):
            err = payload["error"]
            out.error = BrowserError(
                code=str(err.get("code", "provider_error")),
                message=str(err.get("message", "provider returned an error")),
                provider_id=provider.provider_id,
                details=dict(err.get("details", {}))
                if isinstance(err.get("details"), Mapping)
                else {},
            )

        if isinstance(payload.get("raw"), Mapping):
            out.data["raw"] = dict(payload["raw"])
        if isinstance(payload.get("resolution"), Mapping):
            out.data["resolution"] = dict(payload["resolution"])

        return out


_PROVIDER_REGISTRY = BrowserProviderRegistry()
_SESSION_STATE_STORE = BrowserSessionStateStore(
    state_relative_path=_SESSION_STATE_RELATIVE_PATH
)
_SESSION_STATE = _SESSION_STATE_STORE.session_state
_SESSION_STATE_LOADED_ROOTS = _SESSION_STATE_STORE.loaded_workspace_roots
_DEFAULT_PROVIDER = (
    resolve_tool_env().get(OPENMINION_BROWSER_DEFAULT_PROVIDER_ENV, "").strip()
)
_TOOL = BrowserTool(
    router=BrowserRouter(
        _PROVIDER_REGISTRY,
        config=BrowserRoutingConfig(default_provider=_DEFAULT_PROVIDER),
    )
)
_DISCOVERED_PROVIDER_ENTRYPOINTS = False


def _ensure_discovered_providers() -> None:
    global _DISCOVERED_PROVIDER_ENTRYPOINTS
    if _DISCOVERED_PROVIDER_ENTRYPOINTS:
        return
    try:
        _PROVIDER_REGISTRY.load_entry_points()
    except Exception as exc:  # noqa: BLE001
        _LOG.warning("skipping browser provider entry points due to error: %s", exc)
    _DISCOVERED_PROVIDER_ENTRYPOINTS = True


def register_provider(provider: BrowserProvider) -> None:
    _PROVIDER_REGISTRY.register(provider)


def provider_registry() -> BrowserProviderRegistry:
    _ensure_discovered_providers()
    return _PROVIDER_REGISTRY


def register(registry: Any) -> None:
    _ensure_discovered_providers()
    registry.register(_TOOL)
