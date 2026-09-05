import copy
import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator, Mapping

from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.base.config.env import resolve_environment_config
from openminion.modules.artifact.refs import create_default_artifactctl
from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_STATE_ERROR,
)
from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from .permission_mode import permission_mode_from_inputs
from openminion.modules.tool import (
    DEFAULT_POLICY,
    Policy,
    RuntimeContext,
    ToolExecutionContext,
    ToolRegistry,
    ToolSpec,
    build_runtime_repositories,
    create_run_root,
    enforce_watch_target_binding,
    new_run_id,
    resolve_binding_for_call,
)
from openminion.modules.tool.adapters import AllowAllSafetyAdapter, LocalPolicyAdapter
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.plugin_api import PolicyAdapter, PolicyDecision
from openminion.modules.tool.contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED
from openminion.modules.tool.runtime.routing import (
    build_runtime_tool_routing_metadata,
    resolve_runtime_tool_config,
)
from .command_metadata import (
    _confirmation_replay_metadata,
    _extract_runtime_message_ref,
    _inject_runtime_message_ref,
    _merge_orchestration_context_metadata,
    _orchestration_metadata_from_command,
    _runtime_workspace_from_command,
)
from .blockchain_authorization import consume_blockchain_send_authorization
from .policy_context import (
    _agent_id_from_policy,
    _apply_agent_command_policy,
    _apply_reactions_default_policy,
    _background_write_authorized,
    _resolve_auto_confirm,
    _runtime_background_write_authorization_enabled,
    _runtime_env_from_policy,
    _watch_write_authorization_requested,
)
from .results import (
    _error_envelope,
    _normalized_artifact_refs,
    _tool_allowlist_error,
    run_tool_spec,
)
from .workspace_policy import workspace_context_policy

_WORKSPACE_OVERRIDE: ContextVar[Path | None] = ContextVar(
    "openminion_tool_workspace_override",
    default=None,
)
_ADDED_WORKSPACE_ROOTS: ContextVar[tuple[Path, ...]] = ContextVar(
    "openminion_tool_added_workspace_roots",
    default=(),
)
_TURN_TOOL_ALLOWLIST: ContextVar[frozenset[str] | None] = ContextVar(
    "openminion_turn_tool_allowlist", default=None
)


def _is_confirm_required_code(code: Any) -> bool:
    return str(code or "").strip().upper() == TOOL_ERROR_CONFIRM_REQUIRED


def _policy_context_metadata(policy: Policy) -> Any:
    raw = getattr(policy, "raw", {}) or {}
    return raw.get("context_metadata") if isinstance(raw, Mapping) else None


class ToolAdapter:
    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(
        self,
        workspace_root: Path,
        runtime_config: Any | None = None,
        runtime_registry: ToolRegistry | None = None,
        artifactctl: Any | None = None,
        policy: Policy | None = None,
        policy_adapter: PolicyAdapter | None = None,
        policy_ctl: Any | None = None,
        reactions_enabled: bool = True,
        skill_api: Any | None = None,
        secret_service: Any | None = None,
        memory_service: Any | None = None,
        a2a_delegate_api: Any | None = None,
        agent_query: Callable[[], list[dict[str, Any]]] | None = None,
        agent_id: str | None = None,
        agent_profile: Any | None = None,
        telemetryctl: Any | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        policy_from_none = policy is None
        self.policy = self._coerce_policy(policy)
        self.policy_adapter = policy_adapter
        self.policy_ctl = policy_ctl
        self._approval_callback: Callable[[str, dict[str, Any], str], bool] | None = (
            None
        )
        self.reactions_enabled = reactions_enabled
        self.skill_api = skill_api
        self.secret_service = secret_service
        self.memory_service = memory_service
        self.a2a_delegate_api = a2a_delegate_api
        self.agent_query = agent_query
        self.agent_profile = agent_profile
        self.telemetryctl = telemetryctl
        self.allow_background_write_authorization = (
            _runtime_background_write_authorization_enabled(runtime_config)
        )
        self._owns_artifactctl = artifactctl is None
        if artifactctl is not None:
            self.artifactctl = artifactctl
        else:
            try:
                self.artifactctl = create_default_artifactctl()
            except Exception:
                self.artifactctl = None
        self.agent_id = str(agent_id or "").strip() or _agent_id_from_policy(
            self.policy
        )
        policy_raw = getattr(self.policy, "raw", None)
        if isinstance(policy_raw, dict):
            workspace_value = str(policy_raw.get("workspace_root", "") or "").strip()
            if policy_from_none or not workspace_value:
                policy_raw["workspace_root"] = str(self.workspace_root)
            policy_raw["agent_id"] = self.agent_id
            context_metadata = policy_raw.get("context_metadata")
            context_metadata = (
                dict(context_metadata) if isinstance(context_metadata, Mapping) else {}
            )
            policy_raw["context_metadata"] = context_metadata
            context_metadata.setdefault("agent_id", self.agent_id)
            context_metadata.setdefault(
                "allow_background_write_authorization",
                str(self.allow_background_write_authorization).lower(),
            )
            context_metadata.update(
                build_runtime_tool_routing_metadata(
                    getattr(runtime_config, "tools", None)
                )
            )
        _apply_reactions_default_policy(self.policy, runtime_config)
        _apply_agent_command_policy(self.policy, agent_profile)
        if runtime_registry is not None:
            self.registry = runtime_registry
        else:
            from openminion.modules.tool import build_default_tool_registry

            self.registry = build_default_tool_registry(config=runtime_config)

    def close(self) -> None:
        if self.secret_service is not None:
            secret_service = self.secret_service
            self.secret_service = None
            secret_service.close_sync()
        if self._owns_artifactctl and self.artifactctl is not None:
            self._owns_artifactctl = False
            artifactctl = self.artifactctl
            self.artifactctl = None
            artifactctl.close()

    @contextmanager
    def restrict_tools(self, allowed_tools: tuple[str, ...]) -> Iterator[None]:
        allowlist = frozenset(
            name for item in allowed_tools if (name := str(item or "").strip())
        )
        token = _TURN_TOOL_ALLOWLIST.set(allowlist)
        try:
            yield
        finally:
            _TURN_TOOL_ALLOWLIST.reset(token)

    @staticmethod
    def is_tool_allowed(tool_name: str) -> bool:
        allowlist = _TURN_TOOL_ALLOWLIST.get()
        return allowlist is None or str(tool_name or "").strip() in allowlist

    @staticmethod
    def _coerce_policy(policy: Any) -> Policy:
        """Normalize policy inputs for the tool runtime context."""
        if policy is None:
            return Policy(raw=copy.deepcopy(DEFAULT_POLICY))
        if isinstance(policy, Policy):
            return policy
        if isinstance(policy, Mapping):
            merged = copy.deepcopy(DEFAULT_POLICY)
            merged.update(dict(policy))
            return Policy(raw=merged)
        raw = getattr(policy, "raw", None)
        if isinstance(raw, Mapping):
            merged = copy.deepcopy(DEFAULT_POLICY)
            merged.update(dict(raw))
            return Policy(raw=merged)
        raise ValueError(
            f"policy_mismatch: Unsupported policy type: {type(policy).__name__}"
        )

    def set_approval_callback(
        self,
        callback: Callable[[str, dict[str, Any], str], bool] | None,
    ) -> Callable[[str, dict[str, Any], str], bool] | None:
        previous = self._approval_callback
        self._approval_callback = callback if callable(callback) else None
        delegate_setter = getattr(self.a2a_delegate_api, "set_approval_callback", None)
        if callable(delegate_setter):
            delegate_setter(self._approval_callback)
        return previous

    def _replay_inline_approval(
        self,
        *,
        command: dict[str, Any],
        tool_name: str,
        args: dict[str, Any],
        approval_id: str,
        session_id: str,
        trace_id: str,
        start_time: float,
    ) -> dict[str, Any] | None:
        callback = self._approval_callback
        if callback is None:
            return None
        try:
            approved = bool(callback(tool_name, dict(args), approval_id))
        except Exception as exc:
            return _error_envelope(
                status=BRAIN_STATE_ERROR,
                summary="Tool approval failed",
                code="POLICY_DENIED",
                message=str(exc) or "Tool approval failed",
                latency_ms=int((time.monotonic() - start_time) * 1000),
                details={"reason": "approval_callback_failed"},
            )
        if not approved:
            return _error_envelope(
                status=BRAIN_STATE_ERROR,
                summary="Tool execution denied by operator",
                code="POLICY_DENIED",
                message="Tool execution denied by operator.",
                latency_ms=int((time.monotonic() - start_time) * 1000),
                details={"reason": "operator_denied"},
            )

        inputs = command.get("inputs")
        replay_inputs = dict(inputs) if isinstance(inputs, Mapping) else {}
        replay_inputs.update(
            {
                "confirmation_grant_id": approval_id,
                "confirmation_source": "policy_replay",
            }
        )
        return self.execute(
            command={**command, "inputs": replay_inputs},
            session_id=session_id,
            trace_id=trace_id,
        )

    @staticmethod
    def _compose_policy_adapter(
        *,
        base_adapter: PolicyAdapter,
        extra_adapter: PolicyAdapter | None,
    ) -> PolicyAdapter:
        if extra_adapter is None:
            return base_adapter

        class _CompositePolicyAdapter:
            def __init__(self, adapters: list[PolicyAdapter]):
                self._adapters = adapters

            def evaluate(
                self, *, tool_name: str, tool_spec: ToolSpec, args: dict[str, Any]
            ) -> PolicyDecision:
                current_args = dict(args)
                for adapter in self._adapters:
                    decision = adapter.evaluate(
                        tool_name=tool_name, tool_spec=tool_spec, args=current_args
                    )
                    if not decision.allowed:
                        return decision
                    if decision.modified_args:
                        current_args = dict(decision.modified_args)
                return PolicyDecision(
                    allowed=True,
                    reason="policy passed",
                    code="OK",
                    modified_args=current_args,
                )

        return _CompositePolicyAdapter([base_adapter, extra_adapter])

    def _effective_workspace_root(self, policy: Policy | None = None) -> Path:
        override = _WORKSPACE_OVERRIDE.get()
        if override is not None:
            return override
        raw = getattr(policy or self.policy, "raw", None)
        if isinstance(raw, Mapping):
            workspace_root = str(raw.get("workspace_root", "") or "").strip()
            if workspace_root:
                return Path(workspace_root).expanduser()
        return self.workspace_root

    @contextmanager
    def workspace_override(
        self,
        workspace_root: Path,
        *,
        added_roots: tuple[Path, ...] = (),
    ) -> Iterator[None]:
        workspace = Path(workspace_root).expanduser()
        workspace_token = _WORKSPACE_OVERRIDE.set(workspace)
        roots_token = _ADDED_WORKSPACE_ROOTS.set(
            tuple(Path(root).expanduser().resolve() for root in added_roots)
        )
        try:
            yield
        finally:
            _ADDED_WORKSPACE_ROOTS.reset(roots_token)
            _WORKSPACE_OVERRIDE.reset(workspace_token)

    def execute(
        self, *, command: dict[str, Any], session_id: str, trace_id: str
    ) -> dict[str, Any]:
        tool_name = str(command.get("tool_name", ""))
        raw_args = command.get("args", {})
        args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
        inputs = command.get("inputs")
        permission_mode = permission_mode_from_inputs(inputs)
        replay_confirmation_metadata = _confirmation_replay_metadata(inputs)
        start_time = time.monotonic()
        runtime_message_ref = _extract_runtime_message_ref(command=command, args=args)
        orchestration_metadata = _orchestration_metadata_from_command(command)
        requested_workspace = _runtime_workspace_from_command(command)
        _inject_runtime_message_ref(
            tool_name=tool_name, args=args, message_ref=runtime_message_ref
        )
        tool_name, spec, runtime_tool = self._resolve_registry_tool(tool_name)
        if not self.is_tool_allowed(tool_name):
            return _tool_allowlist_error(tool_name)
        if spec is None and runtime_tool is None:
            return _error_envelope(
                status=BRAIN_STATE_ERROR,
                summary=f"Unknown tool: {tool_name}",
                code="NOT_FOUND",
                message=f"Tool '{tool_name}' is not registered.",
            )
        if isinstance(runtime_tool, ToolSpec):
            spec = runtime_tool
            runtime_tool = None
        try:
            policy_for_run = workspace_context_policy(
                self.policy,
                args=args,
                parent=self.workspace_root,
                requested=requested_workspace,
                active=_WORKSPACE_OVERRIDE.get(),
                added_roots=_ADDED_WORKSPACE_ROOTS.get(),
            )
        except ValueError as exc:
            return _error_envelope(
                status=BRAIN_STATE_ERROR,
                summary="Invalid runtime workspace context",
                code="INVALID_RUNTIME_CONTEXT",
                message=str(exc),
            )
        if runtime_message_ref is not None:
            policy_raw = copy.deepcopy(getattr(policy_for_run, "raw", {}) or {})
            tools_cfg = policy_raw.setdefault("tools", {})
            if isinstance(tools_cfg, dict):
                reactions_cfg = tools_cfg.setdefault("reactions", {})
                if isinstance(reactions_cfg, dict):
                    reactions_cfg["runtime_message_ref"] = runtime_message_ref
            policy_for_run = Policy(raw=policy_raw)
        if isinstance(spec, ToolSpec) and bool(
            getattr(spec, "prompt_visible_runtime_name", False)
        ):
            policy_raw = copy.deepcopy(getattr(policy_for_run, "raw", {}) or {})
            tools_cfg = policy_raw.setdefault("tools", {})
            if isinstance(tools_cfg, dict):
                allow_exact = list(tools_cfg.get("allow_exact", []) or [])
                if tool_name not in allow_exact:
                    tools_cfg["allow_exact"] = [*allow_exact, tool_name]
            policy_for_run = Policy(raw=policy_raw)
        policy_raw = getattr(policy_for_run, "raw", None)
        if isinstance(policy_raw, dict):
            policy_raw["agent_id"] = self.agent_id
            _merge_orchestration_context_metadata(policy_raw, orchestration_metadata)
            context_metadata = policy_raw["context_metadata"]
            context_metadata.setdefault("agent_id", self.agent_id)
            if replay_confirmation_metadata:
                context_metadata.update(
                    {
                        key: value
                        for key, value in replay_confirmation_metadata.items()
                        if str(value or "").strip()
                    }
                )
        if runtime_tool is not None:
            return self._execute_openminion_runtime_tool(
                tool=runtime_tool,
                tool_name=tool_name,
                args=args,
                session_id=session_id,
                trace_id=trace_id,
                start_time=start_time,
                policy=policy_for_run,
                orchestration_metadata=orchestration_metadata,
                replay_confirmation_metadata=replay_confirmation_metadata,
            )
        if not isinstance(spec, ToolSpec):
            handler = getattr(spec, "handler", None)
            if handler is None:
                return _error_envelope(
                    status=BRAIN_STATE_ERROR,
                    summary=f"Invalid tool spec for: {tool_name}",
                    code="INVALID_SPEC",
                    message=f"Tool '{tool_name}' did not provide a handler.",
                )
            spec = ToolSpec(
                name=tool_name,
                args_model=getattr(spec, "args_model", dict),
                min_scope=str(getattr(spec, "min_scope", "READ_ONLY") or "READ_ONLY"),
                handler=handler,
                dangerous=bool(getattr(spec, "dangerous", False)),
                idempotent=bool(getattr(spec, "idempotent", True)),
                tags=tuple(getattr(spec, "tags", ("core",)) or ("core",)),
                capabilities=getattr(spec, "capabilities", None),
            )
        try:
            args_model = spec.args_model
            if hasattr(args_model, "model_validate"):
                validated_args = args_model.model_validate(args).model_dump()
            else:
                validated_args = dict(args)
        except Exception as exc:
            return _error_envelope(
                status=BRAIN_STATE_ERROR,
                summary="Invalid tool arguments",
                code="INVALID_ARGUMENT",
                message=str(exc),
                latency_ms=int((time.monotonic() - start_time) * 1000),
            )

        run_id = new_run_id()
        try:
            home_root = resolve_home_root()
            env_owner = resolve_environment_config(
                runtime_env=_runtime_env_from_policy(policy_for_run)
            )
            data_root = resolve_data_root(
                home_root,
                data_root=env_owner.get("OPENMINION_DATA_ROOT", ""),
            )
            run_root = create_run_root(
                policy_for_run, run_id, root_override=data_root / "tool-runs"
            )
        except Exception as exc:
            return _error_envelope(
                status=BRAIN_STATE_ERROR,
                summary="Failed to configure execution environment",
                code="EXEC_ERROR",
                message=str(exc),
                latency_ms=int((time.monotonic() - start_time) * 1000),
            )

        effective_workspace_root = self._effective_workspace_root(policy_for_run)
        replay_confirmed = bool(replay_confirmation_metadata)
        watch_write_authorization_requested = _watch_write_authorization_requested(
            tool_name=tool_name,
            args=args,
        )
        if (
            watch_write_authorization_requested
            and not self.allow_background_write_authorization
            and not replay_confirmed
            and permission_mode != "bypass"
        ):
            approval_id = new_run_id()
            replay = self._replay_inline_approval(
                command=command,
                tool_name=tool_name,
                args=args,
                approval_id=approval_id,
                session_id=session_id,
                trace_id=trace_id,
                start_time=start_time,
            )
            if replay is not None:
                return replay
            return _error_envelope(
                status=BRAIN_ACTION_STATUS_NEEDS_USER,
                summary="Background watch write authorization requires approval.",
                code=TOOL_ERROR_CONFIRM_REQUIRED,
                message=(
                    "Background watch write authorization requires explicit "
                    "operator confirmation."
                ),
                latency_ms=int((time.monotonic() - start_time) * 1000),
                details={
                    "requires_confirm": True,
                    "approval_id": approval_id,
                    "choices": ["allow_once", "allow_session", "deny"],
                    "reason": "background_write_authorization_requested",
                    "tool_name": tool_name,
                },
            )
        background_write_authorized = _background_write_authorized(inputs)
        auto_confirm = _resolve_auto_confirm(
            tool_name=tool_name,
            args=validated_args,
            permission_mode=permission_mode,
            replay_confirmed=replay_confirmed,
            background_write_authorized=background_write_authorized,
        )

        extra_adapter = None if permission_mode == "bypass" else self.policy_adapter
        local_adapter = LocalPolicyAdapter(
            policy=policy_for_run,
            workspace=effective_workspace_root,
            scope=policy_for_run.max_scope(),
            confirm=auto_confirm,
        )
        policy_adapter = (
            None
            if replay_confirmed
            else self._compose_policy_adapter(
                base_adapter=local_adapter,
                extra_adapter=extra_adapter,
            )
        )

        context_metadata = _policy_context_metadata(policy_for_run)
        enforce_watch_target_binding(validated_args, context_metadata)
        ctx = RuntimeContext(
            policy=policy_for_run,
            workspace=effective_workspace_root,
            run_root=run_root,
            scope=policy_for_run.max_scope(),
            confirm=auto_confirm,
            repositories=build_runtime_repositories(context_metadata=context_metadata),
            logs=[],
            artifacts=[],
            safety_adapter=AllowAllSafetyAdapter(),
            policy_adapter=policy_adapter,
            skill_api=self.skill_api,
            secret_service=self.secret_service,
            telemetryctl=self.telemetryctl,
            artifactctl=self.artifactctl,
            memory_service=self.memory_service,
            a2a_delegate_api=self.a2a_delegate_api,
            agent_query=self.agent_query,
            telemetry_session_id=session_id,
            telemetry_turn_id=trace_id,
            permission_mode=permission_mode,
            agent_profile=self.agent_profile,
            tool_registry=self.registry,
        )
        ctx.session_id, ctx.trace_id = session_id, trace_id
        ctx.agent_id, ctx.run_id = self.agent_id, run_id
        ctx.tool_name = tool_name
        ctx.tool_call_id = str(command.get("command_id", "") or "")
        ctx.invocation_id = str(command.get("idempotency_key", "") or "")
        if runtime_message_ref is not None:
            ctx.message_ref = dict(runtime_message_ref)
        if ctx.policy_adapter is not None:
            policy_decision = ctx.policy_adapter.evaluate(
                tool_name=tool_name,
                tool_spec=spec,
                args=validated_args,
            )
            if not policy_decision.allowed:
                details = dict(policy_decision.details or {})
                details.setdefault(
                    "requires_confirm",
                    bool(policy_decision.requires_confirm),
                )
                requires_confirm = bool(policy_decision.requires_confirm) or str(
                    policy_decision.code or ""
                ).lower() in {"require_approval", "confirm_required"}
                status = (
                    BRAIN_ACTION_STATUS_NEEDS_USER
                    if requires_confirm
                    else BRAIN_STATE_ERROR
                )
                error_code = (
                    TOOL_ERROR_CONFIRM_REQUIRED
                    if requires_confirm
                    else str(policy_decision.code or "POLICY_DENIED")
                )
                if requires_confirm:
                    approval_id = str(details.get("approval_id", "") or new_run_id())
                    details.setdefault("approval_id", approval_id)
                    details.setdefault(
                        "choices",
                        ["allow_once", "allow_session", "deny"],
                    )
                    details.setdefault("reason", "policy_confirmation_required")
                    replay = self._replay_inline_approval(
                        command=command,
                        tool_name=tool_name,
                        args=validated_args,
                        approval_id=approval_id,
                        session_id=session_id,
                        trace_id=trace_id,
                        start_time=start_time,
                    )
                    if replay is not None:
                        return replay
                return _error_envelope(
                    status=status,
                    summary=str(
                        policy_decision.reason or "Policy denied tool execution"
                    ),
                    code=error_code,
                    message=str(policy_decision.reason or "Policy denied"),
                    latency_ms=int((time.monotonic() - start_time) * 1000),
                    details=details,
                )
            if policy_decision.modified_args:
                validated_args = dict(policy_decision.modified_args)

        try:
            if tool_name == "blockchain.send_transaction":
                ctx.policy_authorization = consume_blockchain_send_authorization(
                    policy_ctl=self.policy_ctl,
                    permission_mode=permission_mode,
                    args=args,
                )
            return run_tool_spec(
                spec=spec,
                validated_args=validated_args,
                context=ctx,
                start_time=start_time,
                background_write_authorized=background_write_authorized,
                tool_name=tool_name,
            )
        except ToolRuntimeError as exc:
            requires_confirm = _is_confirm_required_code(exc.code)
            if requires_confirm and not replay_confirmed:
                details = dict(exc.details or {})
                approval_id = str(details.get("approval_id", "") or new_run_id())
                replay = self._replay_inline_approval(
                    command=command,
                    tool_name=tool_name,
                    args=validated_args,
                    approval_id=approval_id,
                    session_id=session_id,
                    trace_id=trace_id,
                    start_time=start_time,
                )
                if replay is not None:
                    return replay
            return _error_envelope(
                status=(
                    BRAIN_ACTION_STATUS_NEEDS_USER
                    if requires_confirm
                    else BRAIN_STATE_ERROR
                ),
                summary=exc.message or "Tool execution failed",
                code=TOOL_ERROR_CONFIRM_REQUIRED if requires_confirm else exc.code,
                message=exc.message or "Tool execution failed",
                latency_ms=int((time.monotonic() - start_time) * 1000),
                details=dict(exc.details or {}),
            )
        except Exception:
            return _error_envelope(
                status=BRAIN_STATE_ERROR,
                summary="Tool execution failed",
                code="EXEC_ERROR",
                message="Tool execution failed",
                latency_ms=int((time.monotonic() - start_time) * 1000),
            )

    def _resolve_registry_tool(self, tool_name: str) -> tuple[str, Any, Any]:
        spec = None
        runtime_tool = None

        def assign(entry: Any) -> bool:
            nonlocal spec, runtime_tool
            if entry is None:
                return False
            if hasattr(entry, "execute") and not hasattr(entry, "handler"):
                runtime_tool = entry
            elif spec is None:
                spec = entry
            return True

        if hasattr(self.registry, "get"):
            try:
                spec = self.registry.get(tool_name)
            except KeyError:
                pass
        if spec is None:
            tools = getattr(self.registry, "_tools", None)
            if isinstance(tools, Mapping) and not assign(tools.get(tool_name)):
                resolution = resolve_binding_for_call(
                    raw_tool_name=tool_name,
                    available_tool_names=tuple(tools),
                )
                resolved_name = str(
                    getattr(resolution, "runtime_tool_name", "") or ""
                ).strip()
                if resolved_name and assign(tools.get(resolved_name)):
                    tool_name = resolved_name
        elif hasattr(spec, "execute") and not hasattr(spec, "handler"):
            runtime_tool = spec
        return tool_name, spec, runtime_tool

    def _execute_openminion_runtime_tool(
        self,
        *,
        tool: Any,
        tool_name: str,
        args: dict[str, Any],
        session_id: str,
        trace_id: str,
        start_time: float,
        policy: Policy | None = None,
        orchestration_metadata: Mapping[str, Any] | None = None,
        replay_confirmation_metadata: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        effective_policy = policy or self.policy
        context = self._runtime_tool_context(
            policy=effective_policy,
            session_id=session_id,
            trace_id=trace_id,
            orchestration_metadata=orchestration_metadata,
            replay_confirmation_metadata=replay_confirmation_metadata,
        )
        try:
            result = tool.execute(arguments=args, context=context)
        except Exception as exc:
            return _error_envelope(
                status=BRAIN_STATE_ERROR,
                summary="Tool execution failed",
                code="EXEC_ERROR",
                message=str(exc),
                latency_ms=int((time.monotonic() - start_time) * 1000),
            )

        ok = bool(getattr(result, "ok", False))
        content = str(getattr(result, "content", "") or "")
        error_message = str(getattr(result, "error", "") or "")
        data = getattr(result, "data", {})
        outputs = dict(data) if isinstance(data, Mapping) else {"data": data}
        outputs.update(
            {
                "tool_name": str(getattr(result, "tool_name", "") or tool_name),
                "content": content,
                "verified": bool(getattr(result, "verified", False)),
                "source": str(getattr(result, "source", "") or "openminion"),
            }
        )

        summary = (
            content if ok else (error_message or content or "Tool execution failed")
        )
        error_code = ""
        error_details: dict[str, Any] = {}
        if isinstance(data, Mapping):
            error_code = str(data.get("error_code", "") or "").strip()
            raw_details = data.get("details")
            if isinstance(raw_details, Mapping):
                error_details = dict(raw_details)
        requires_confirm = _is_confirm_required_code(error_code)
        if requires_confirm and not replay_confirmation_metadata:
            approval_id = str(error_details.get("approval_id", "") or new_run_id())
            replay = self._replay_inline_approval(
                command={"tool_name": tool_name, "args": args},
                tool_name=tool_name,
                args=args,
                approval_id=approval_id,
                session_id=session_id,
                trace_id=trace_id,
                start_time=start_time,
            )
            if replay is not None:
                return replay
        response: dict[str, Any] = {
            "status": (
                BRAIN_ACTION_STATUS_SUCCESS
                if ok
                else (
                    BRAIN_ACTION_STATUS_NEEDS_USER
                    if requires_confirm
                    else BRAIN_STATE_ERROR
                )
            ),
            "summary": summary,
            "outputs": outputs,
            "artifact_refs": _normalized_artifact_refs(
                outputs.get("artifact_refs")
                or outputs.get("artifacts")
                or data.get("artifact_refs")
                or data.get("artifacts")
            ),
            "memory_refs": [],
            "metrics": {
                "latency_ms": int((time.monotonic() - start_time) * 1000),
                "tokens_used": 0,
                "cost_estimate": 0.0,
            },
        }
        if not ok:
            response["error"] = {
                "code": error_code or "EXEC_ERROR",
                "message": error_message or "Tool execution failed",
                "details": error_details,
            }
        return response

    def _runtime_tool_context(
        self,
        *,
        policy: Policy,
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
        )
