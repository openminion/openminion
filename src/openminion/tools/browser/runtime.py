"""Browser tool runtime state and policy."""

import base64
import copy
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, cast

from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.runtime.context import RuntimeContext
from openminion.modules.tool.runtime.policy import DEFAULT_POLICY, Policy
from openminion.modules.tool.runtime.registry_toolspec import (
    resolve_run_root,
    resolve_workspace,
)
from openminion.modules.tool.runtime.routing import resolve_runtime_tool_family_config
from openminion.tools.config import resolve_tool_env, resolve_tool_workspace_root

from .models import (
    ArtifactRef,
    BrowserAction,
    BrowserCallArgs,
    BrowserOp,
    BrowserResult,
    InstanceSpec,
    SnapshotResult,
    TabInfo,
    normalize_op,
)
from .payloads import normalize_path
from .providers import BrowserProvider, BrowserProviderContext
from .session_state import SessionBrowserState

_OP_CAPABILITIES: dict[str, tuple[str, ...]] = {
    BrowserOp.TAB_ACTIONS.value: ("batch_actions",),
    BrowserOp.TAB_PDF.value: ("pdf_export",),
    BrowserOp.TAB_LOCK.value: ("tab_locking",),
    BrowserOp.TAB_UNLOCK.value: ("tab_locking",),
}

_OPS_REQUIRE_TAB = {
    BrowserOp.TAB_SELECT.value,
    BrowserOp.TAB_CLOSE.value,
    BrowserOp.TAB_NAVIGATE.value,
    BrowserOp.TAB_SNAPSHOT.value,
    BrowserOp.TAB_TEXT.value,
    BrowserOp.TAB_ACTION.value,
    BrowserOp.TAB_ACTIONS.value,
    BrowserOp.TAB_SCREENSHOT.value,
    BrowserOp.TAB_PDF.value,
    BrowserOp.TAB_LOCK.value,
    BrowserOp.TAB_UNLOCK.value,
}

_OPS_REQUIRE_INSTANCE = {
    BrowserOp.INSTANCE_KILL.value,
    BrowserOp.INSTANCE_STOP.value,
    BrowserOp.TAB_NEW.value,
    BrowserOp.TAB_LIST.value,
}


class BrowserToolError(RuntimeError):
    def __init__(
        self, code: str, message: str, details: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.details = dict(details or {})


@dataclass(frozen=True)
class _BrowserExecutionContext:
    runtime: Any | None = None
    trace_id: str = ""
    session_id: str = ""
    extras: Mapping[str, Any] = field(default_factory=dict)


def _tab_info(self: Any, row: Mapping[str, Any]) -> TabInfo:
    return TabInfo(
        id=str(row.get("id") or row.get("tabId") or row.get("tab_id") or ""),
        url=str(row.get("url", "")),
        title=str(row.get("title", "")),
    )


def _snapshot_result(self: Any, snapshot: Mapping[str, Any]) -> SnapshotResult:
    if isinstance(snapshot.get("nodes"), list):
        nodes = list(snapshot["nodes"])
    elif snapshot.get("root") is not None:
        nodes = [snapshot["root"]]
    else:
        nodes = []
    refs = snapshot.get("interactive_refs")
    return SnapshotResult(
        format=str(snapshot.get("format", "auto")),
        nodes=nodes,
        interactive_refs=[str(item) for item in refs] if isinstance(refs, list) else [],
        meta=dict(snapshot.get("meta", {}))
        if isinstance(snapshot.get("meta"), Mapping)
        else {},
    )


def _materialize_artifact(
    self: Any,
    *,
    blob: bytes,
    kind: str,
    output_path: str | None,
    ctx: _BrowserExecutionContext,
) -> ArtifactRef:
    digest = hashlib.sha256(blob).hexdigest()
    target = str(output_path or "").strip()
    if target:
        normalized = self._workspace_output_path(ctx, target)
        runtime_obj = ctx.runtime
        if isinstance(runtime_obj, RuntimeContext):
            Path(normalized).parent.mkdir(parents=True, exist_ok=True)
            Path(normalized).write_bytes(blob)
            return ArtifactRef(kind=kind, path=normalized, sha256=digest)
        if runtime_obj is not None and callable(getattr(runtime_obj, "fs_write", None)):
            runtime_obj.fs_write({"path": normalized, "content": blob})
            return ArtifactRef(kind=kind, path=normalized, sha256=digest)
        return ArtifactRef(
            kind=kind,
            path=normalized,
            sha256=digest,
            content_base64=base64.b64encode(blob).decode("ascii"),
        )
    return ArtifactRef(
        kind=kind,
        path="",
        sha256=digest,
        content_base64=base64.b64encode(blob).decode("ascii"),
    )


def _workspace_output_path(
    self: Any,
    ctx: BrowserProviderContext | _BrowserExecutionContext,
    raw_path: str,
) -> str:
    workspace_root = str(
        Path(self._workspace_root(ctx)).expanduser().resolve(strict=False)
    )
    try:
        return normalize_path(path=raw_path, base=workspace_root)
    except ValueError as exc:
        raise BrowserToolError(
            "INVALID_ARGUMENT",
            "output.path must stay inside workspace root",
            {"workspace_root": workspace_root, "path": str(raw_path)},
        ) from exc


def _workspace_root(
    self: Any, ctx: BrowserProviderContext | _BrowserExecutionContext
) -> str:
    extras = ctx.extras if isinstance(getattr(ctx, "extras", None), Mapping) else {}
    return str(
        resolve_tool_workspace_root(context=ctx, extras=extras).resolve(strict=False)
    )


def _agent_profile_provider(
    self: Any, ctx: BrowserProviderContext | _BrowserExecutionContext
) -> str | None:
    extras = ctx.extras if isinstance(ctx.extras, Mapping) else {}
    for key in (
        "agent_profile_browser_provider",
        "agent_profile.tools.browser.provider",
    ):
        value = str(extras.get(key, "")).strip()
        if value:
            return value
    return None


def _session_provider_override(
    self: Any, ctx: BrowserProviderContext | _BrowserExecutionContext
) -> str | None:
    extras = ctx.extras if isinstance(ctx.extras, Mapping) else {}
    for key in (
        "session_browser_provider_override",
        "session.browser.provider_override",
    ):
        value = str(extras.get(key, "")).strip()
        if value:
            return value
    return None


def _runtime_provider_preferences(
    self: Any, ctx: _BrowserExecutionContext
) -> tuple[str, tuple[str, ...]]:
    family_cfg = resolve_runtime_tool_family_config(ctx.runtime, family_name="browser")
    if family_cfg is None:
        return "", ()
    return family_cfg.default_provider, tuple(family_cfg.provider_order)


def _enforce_capabilities(
    self: Any, *, provider: BrowserProvider, call: BrowserCallArgs
) -> None:
    op = normalize_op(call.op)
    for capability in _OP_CAPABILITIES.get(op, ()):
        if bool(getattr(provider.capabilities, capability, False)):
            continue
        self._raise_capability_error(
            provider_id=provider.provider_id, capability=capability
        )

    if op in (BrowserOp.TAB_ACTION.value, BrowserOp.TAB_ACTIONS.value):
        actions: list[BrowserAction] = []
        if op == BrowserOp.TAB_ACTION.value and call.action is not None:
            actions = [call.action]
        if op == BrowserOp.TAB_ACTIONS.value:
            actions = list(call.actions)

        uses_ref = any(action.target and action.target.ref for action in actions)
        uses_selector = any(
            action.target and (action.target.selector or action.target.role)
            for action in actions
        )
        if uses_ref and not provider.capabilities.snapshot_refs:
            self._raise_capability_error(
                provider_id=provider.provider_id, capability="snapshot_refs"
            )
        if uses_selector and not provider.capabilities.selector_actions:
            self._raise_capability_error(
                provider_id=provider.provider_id, capability="selector_actions"
            )


def _raise_capability_error(self: Any, *, provider_id: str, capability: str) -> None:
    alternatives = [
        name
        for name in self.router.providers_with_capability(capability)
        if name != provider_id
    ]
    raise BrowserToolError(
        "capability_not_supported",
        f"provider '{provider_id}' does not support capability '{capability}'",
        {
            "provider_id": provider_id,
            "capability": capability,
            "alternatives": alternatives,
        },
    )


def _remember_affinity(self: Any, *, provider_id: str, result: BrowserResult) -> None:
    if result.instance and result.instance.id:
        self.router.remember_affinity(
            provider_id=provider_id, instance_id=result.instance.id
        )
    for instance in result.instances:
        if instance.id:
            self.router.remember_affinity(
                provider_id=provider_id, instance_id=instance.id
            )
    if result.tab and result.tab.id:
        self.router.remember_affinity(provider_id=provider_id, tab_id=result.tab.id)
    for tab in result.tabs:
        if tab.id:
            self.router.remember_affinity(provider_id=provider_id, tab_id=tab.id)


def _state_key(
    self: Any,
    *,
    provider_id: str,
    ctx: BrowserProviderContext | _BrowserExecutionContext | None,
) -> tuple[str, str, str] | None:
    if ctx is None:
        return None
    if isinstance(ctx, BrowserProviderContext):
        session_id = str(ctx.session_id or "").strip()
        workspace_root = str(ctx.workspace_root or "").strip()
        if not workspace_root:
            extras = ctx.extras if isinstance(ctx.extras, Mapping) else {}
            workspace_root = str(
                extras.get("workspace_root")
                or extras.get("workspace")
                or extras.get("cwd")
                or ""
            ).strip()
        if workspace_root:
            workspace_root = str(
                Path(workspace_root).expanduser().resolve(strict=False)
            )
    else:
        session_id = str(ctx.session_id or "").strip()
        workspace_root = str(
            Path(self._workspace_root(ctx)).expanduser().resolve(strict=False)
        )
    assert self.session_state_store is not None
    return cast(
        tuple[str, str, str] | None,
        self.session_state_store.state_key(
            provider_id=provider_id,
            session_id=session_id,
            workspace_root=workspace_root,
        ),
    )


def _state_for(
    self: Any,
    *,
    provider_id: str,
    ctx: BrowserProviderContext | _BrowserExecutionContext | None,
) -> SessionBrowserState:
    assert self.session_state_store is not None
    if ctx is None:
        return SessionBrowserState()
    if isinstance(ctx, BrowserProviderContext):
        session_id = str(ctx.session_id or "").strip()
        extras = ctx.extras if isinstance(ctx.extras, Mapping) else {}
        workspace_root = str(ctx.workspace_root or "").strip() or self._workspace_root(
            _BrowserExecutionContext(
                session_id=session_id,
                extras=extras,
                runtime=ctx.tool_context,
                trace_id=ctx.trace_id,
            )
        )
    else:
        session_id = str(ctx.session_id or "").strip()
        workspace_root = str(
            Path(self._workspace_root(ctx)).expanduser().resolve(strict=False)
        )
        extras = ctx.extras if isinstance(getattr(ctx, "extras", None), Mapping) else {}
    return cast(
        SessionBrowserState,
        self.session_state_store.state_for(
            provider_id=provider_id,
            session_id=session_id,
            workspace_root=workspace_root,
            extras=extras,
            env=self._runtime_env_from_context(ctx),
        ),
    )


def _hydrate_call_with_session_state(
    self: Any,
    *,
    provider: BrowserProvider,
    provider_ctx: BrowserProviderContext,
    call: BrowserCallArgs,
) -> BrowserCallArgs:
    assert self.session_state_store is not None
    workspace_root = str(
        Path(provider_ctx.workspace_root or str(Path.cwd()))
        .expanduser()
        .resolve(strict=False)
    )
    extras = provider_ctx.extras if isinstance(provider_ctx.extras, Mapping) else {}
    return cast(
        BrowserCallArgs,
        self.session_state_store.hydrate_call_with_session_state(
            provider_id=provider.provider_id,
            session_id=str(provider_ctx.session_id or ""),
            workspace_root=workspace_root,
            extras=extras,
            call=call,
            ops_require_instance=_OPS_REQUIRE_INSTANCE,
            ops_require_tab=_OPS_REQUIRE_TAB,
            env=self._runtime_env_from_context(provider_ctx),
        ),
    )


def _remember_session_state(
    self: Any,
    *,
    provider_id: str,
    call: BrowserCallArgs,
    result: BrowserResult,
    ctx: _BrowserExecutionContext,
) -> None:
    assert self.session_state_store is not None
    workspace_root = str(
        Path(self._workspace_root(ctx)).expanduser().resolve(strict=False)
    )
    self.session_state_store.remember_session_state(
        provider_id=provider_id,
        session_id=ctx.session_id,
        workspace_root=workspace_root,
        call=call,
        result=result,
        env=self._runtime_env_from_context(ctx),
    )


def _clear_session_state(
    self: Any,
    *,
    provider_id: str,
    ctx: BrowserProviderContext | _BrowserExecutionContext | None,
) -> None:
    assert self.session_state_store is not None
    if ctx is None:
        return
    if isinstance(ctx, BrowserProviderContext):
        session_id = str(ctx.session_id or "").strip()
        workspace_root = str(ctx.workspace_root or "").strip() or self._workspace_root(
            _BrowserExecutionContext(
                session_id=session_id,
                extras=ctx.extras if isinstance(ctx.extras, Mapping) else {},
                runtime=ctx.tool_context,
                trace_id=ctx.trace_id,
            )
        )
    else:
        session_id = str(ctx.session_id or "").strip()
        workspace_root = str(
            Path(self._workspace_root(ctx)).expanduser().resolve(strict=False)
        )
    self.session_state_store.clear_session_state(
        provider_id=provider_id,
        session_id=session_id,
        workspace_root=workspace_root,
        env=self._runtime_env_from_context(ctx),
    )


def _runtime_env_from_context(
    self: Any,
    ctx: BrowserProviderContext | _BrowserExecutionContext | None,
) -> Any:
    if ctx is None:
        return None
    runtime_obj: Any | None = None
    if isinstance(ctx, BrowserProviderContext):
        runtime_obj = ctx.tool_context
    elif isinstance(ctx, _BrowserExecutionContext):
        runtime_obj = ctx.runtime
    if runtime_obj is None:
        return None
    return getattr(runtime_obj, "env", None)


def _resolve_tab(
    self: Any,
    *,
    provider: BrowserProvider,
    provider_ctx: BrowserProviderContext,
    call: BrowserCallArgs,
    prefer_url: bool = False,
) -> tuple[str, TabInfo | None, dict[str, Any]]:
    assert self.tab_resolver is not None
    return cast(
        tuple[str, TabInfo | None, dict[str, Any]],
        self.tab_resolver.resolve_tab(
            provider=provider,
            provider_ctx=provider_ctx,
            call=call,
            prefer_url=prefer_url,
        ),
    )


def _filter_tabs(
    self: Any, tabs: list[TabInfo], *, options: Mapping[str, Any]
) -> tuple[list[TabInfo], dict[str, Any]]:
    assert self.tab_resolver is not None
    return cast(
        tuple[list[TabInfo], dict[str, Any]],
        self.tab_resolver.filter_tabs(tabs, options=options),
    )


def _as_bool(self: Any, value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    token = str(value).strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def _emit_event(
    self: Any,
    phase: str,
    *,
    provider_id: str,
    call: BrowserCallArgs | None,
    payload: Mapping[str, Any],
    ctx: _BrowserExecutionContext,
) -> None:
    sink = None
    extras = ctx.extras if isinstance(ctx.extras, Mapping) else {}
    for key in ("session_event_sink", "event_sink"):
        candidate = extras.get(key)
        if callable(candidate):
            sink = candidate
            break
    if sink is None:
        return

    event = {
        "event": f"tool.browser.{phase}",
        "trace_id": ctx.trace_id,
        "session_id": ctx.session_id,
        "provider_id": provider_id,
        "op": call.op if call is not None else "",
        "tab_id": call.tab_id if call is not None else "",
        "instance_id": call.instance_id if call is not None else "",
        "url": call.url if call is not None else "",
        "payload": dict(payload),
    }
    try:
        sink(event)
    except Exception:
        return


def _instance_spec(
    self: Any,
    call: BrowserCallArgs,
    *,
    ctx: BrowserProviderContext | _BrowserExecutionContext | None,
) -> InstanceSpec:
    if call.instance is not None:
        spec = call.instance
    else:
        spec = InstanceSpec(profile=call.profile, mode=call.mode, port=call.port)

    normalized_ctx: BrowserProviderContext | _BrowserExecutionContext | None = ctx
    if isinstance(ctx, BrowserProviderContext):
        normalized_ctx = _BrowserExecutionContext(
            runtime=ctx.tool_context,
            trace_id=ctx.trace_id,
            session_id=ctx.session_id,
            extras=ctx.extras if isinstance(ctx.extras, Mapping) else {},
        )

    updates: dict[str, Any] = {}
    if spec.user_data_dir and normalized_ctx is not None:
        updates["user_data_dir"] = self._workspace_output_path(
            normalized_ctx, spec.user_data_dir
        )
    if spec.downloads_path and normalized_ctx is not None:
        updates["downloads_path"] = self._workspace_output_path(
            normalized_ctx, spec.downloads_path
        )
    if updates:
        return spec.model_copy(update=updates)
    return spec


def _artifact_kind(self: Any, op: str) -> str:
    normalized = normalize_op(op)
    if normalized == BrowserOp.TAB_PDF.value:
        return "pdf"
    if normalized == BrowserOp.TAB_SCREENSHOT.value:
        return "screenshot"
    return "artifact"


def _coerce_execution_context(
    self: Any,
    context: ToolExecutionContext | _BrowserExecutionContext,
) -> _BrowserExecutionContext:
    if isinstance(context, _BrowserExecutionContext):
        return context
    if isinstance(context, ToolExecutionContext):
        metadata = (
            dict(context.metadata)
            if isinstance(getattr(context, "metadata", None), Mapping)
            else {}
        )
        runtime_ctx = self._runtime_context_from_execution_context(context)
        return _BrowserExecutionContext(
            runtime=runtime_ctx,
            trace_id=str(metadata.get("trace_id", "")).strip(),
            session_id=str(context.session_id or "").strip(),
            extras=metadata,
        )
    metadata = (
        dict(getattr(context, "metadata", {}) or {})
        if isinstance(getattr(context, "metadata", None), Mapping)
        else {}
    )
    extras = (
        dict(getattr(context, "extras", {}) or {})
        if isinstance(getattr(context, "extras", None), Mapping)
        else metadata
    )
    session_id = str(
        getattr(context, "session_id", "")
        or metadata.get("session_id")
        or extras.get("session_id")
        or ""
    ).strip()
    trace_id = str(
        getattr(context, "trace_id", "")
        or metadata.get("trace_id")
        or extras.get("trace_id")
        or ""
    ).strip()
    runtime_obj = getattr(context, "runtime", None)
    return _BrowserExecutionContext(
        runtime=runtime_obj,
        trace_id=trace_id,
        session_id=session_id,
        extras=extras,
    )


def _runtime_context_from_execution_context(
    self: Any,
    context: ToolExecutionContext,
) -> RuntimeContext:
    workspace = resolve_workspace(context=context)
    run_root = resolve_run_root(workspace=workspace, context=context)
    policy_payload = copy.deepcopy(DEFAULT_POLICY)
    policy_payload["workspace_root"] = str(workspace)
    metadata = (
        dict(context.metadata)
        if isinstance(getattr(context, "metadata", None), Mapping)
        else {}
    )
    raw_runtime_env = metadata.get("runtime_env")
    runtime_env: Mapping[str, object] | None = (
        cast(Mapping[str, object], raw_runtime_env)
        if isinstance(raw_runtime_env, Mapping)
        else None
    )
    policy_payload["context_metadata"] = metadata
    agent_id = str(metadata.get("agent_id", "")).strip()
    if agent_id:
        policy_payload["agent_id"] = agent_id
    return RuntimeContext(
        policy=Policy(raw=policy_payload),
        workspace=workspace,
        run_root=run_root,
        scope="UI_AUTOMATION",
        confirm=False,
        env=resolve_tool_env(env=runtime_env),
    )


__all__ = [
    "BrowserToolError",
    "_BrowserExecutionContext",
    "_agent_profile_provider",
    "_artifact_kind",
    "_as_bool",
    "_clear_session_state",
    "_coerce_execution_context",
    "_emit_event",
    "_enforce_capabilities",
    "_filter_tabs",
    "_hydrate_call_with_session_state",
    "_instance_spec",
    "_materialize_artifact",
    "_raise_capability_error",
    "_remember_affinity",
    "_remember_session_state",
    "_resolve_tab",
    "_runtime_context_from_execution_context",
    "_runtime_env_from_context",
    "_runtime_provider_preferences",
    "_session_provider_override",
    "_snapshot_result",
    "_state_for",
    "_state_key",
    "_tab_info",
    "_workspace_output_path",
    "_workspace_root",
]
