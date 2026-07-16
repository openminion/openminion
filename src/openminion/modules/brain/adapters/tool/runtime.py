import copy
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Mapping

from openminion.base.logging import get_logger
from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.base.config.env import resolve_environment_config
from openminion.modules.artifact.refs import create_default_artifactctl
from openminion.modules.brain.constants import (
    BRAIN_ACTION_STATUS_NEEDS_USER,
    BRAIN_ACTION_STATUS_SUCCESS,
    BRAIN_JOB_STATUS_RUNNING,
    BRAIN_STATE_ERROR,
)
from openminion.modules.brain.interfaces import BRAIN_ADAPTER_INTERFACE_VERSION
from .permission_mode import canonical_permission_mode
from openminion.modules.tool import (
    DEFAULT_POLICY,
    Policy,
    RuntimeContext,
    ToolRegistry,
    ToolSpec,
    build_runtime_repositories,
    create_run_root,
    new_run_id,
    preferred_artifact_ref,
    resolve_binding_for_call,
)
from openminion.modules.tool.adapters import AllowAllSafetyAdapter, LocalPolicyAdapter
from openminion.modules.tool.errors import ToolRuntimeError
from openminion.modules.tool.plugin_api import PolicyAdapter, PolicyDecision
from openminion.modules.tool.contracts.model_ids import MODEL_FILE_WRITE
from openminion.modules.tool.contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED
from openminion.modules.tool.runtime.routing import (
    build_runtime_tool_routing_metadata,
    resolve_runtime_tool_config,
)
from openminion.tools.exec.command_parser import is_read_only_exec_command
from openminion.tools.exec.process import resolve_shell_family
from .command_metadata import (
    _confirmation_replay_metadata,
    _extract_runtime_message_ref,
    _merge_orchestration_context_metadata,
    _orchestration_metadata_from_command,
)
from .policy_context import (
    _agent_id_from_policy,
    _apply_reactions_default_policy,
    _runtime_background_write_authorization_enabled,
    _runtime_env_from_policy,
    _watch_write_authorization_requested,
)
from .results import (
    _derive_toolspec_summary,
    _error_envelope,
    _normalized_artifact_refs,
)

_log = get_logger("brain.adapters.tool.runtime")


def _is_confirm_required_code(code: Any) -> bool:
    return str(code or "").strip().upper() == TOOL_ERROR_CONFIRM_REQUIRED


try:
    import openminion_tool_os.plugin

    HAS_OS_PLUGIN = True
except ImportError:
    HAS_OS_PLUGIN = False

try:
    import openminion.tools.browser.providers.pinchtab.plugin as openminion_tool_browser_pinchtab_plugin

    HAS_BROWSER_PINCHTAB_PLUGIN = True
except ImportError:
    HAS_BROWSER_PINCHTAB_PLUGIN = False

try:
    import openminion.tools.reaction.plugin as openminion_tools_reaction_plugin

    HAS_REACTIONS_PLUGIN = True
except ImportError:
    HAS_REACTIONS_PLUGIN = False


class ToolAdapter:
    """Adapter for executing OS tools using openminion-tool."""

    contract_version = BRAIN_ADAPTER_INTERFACE_VERSION

    def __init__(
        self,
        workspace_root: Path,
        runtime_config: Any | None = None,
        runtime_registry: ToolRegistry | None = None,
        artifactctl: Any | None = None,
        policy: Policy | None = None,
        policy_adapter: PolicyAdapter | None = None,
        reactions_enabled: bool = True,
        skill_api: Any | None = None,
        agent_id: str | None = None,
        agent_profile: Any | None = None,
    ) -> None:
        self.workspace_root = workspace_root
        policy_from_none = policy is None
        self.policy = self._coerce_policy(policy)
        self.policy_adapter = policy_adapter
        self._approval_callback: Callable[[str, dict[str, Any], str], bool] | None = (
            None
        )
        self.reactions_enabled = reactions_enabled
        self.skill_api = skill_api
        self.agent_profile = agent_profile
        self.allow_background_write_authorization = (
            _runtime_background_write_authorization_enabled(runtime_config)
        )
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
        if isinstance(policy_raw, Mapping):
            workspace_value = str(policy_raw.get("workspace_root", "") or "").strip()
            if policy_from_none or not workspace_value:
                policy_raw["workspace_root"] = str(self.workspace_root)
            policy_raw["agent_id"] = self.agent_id
            context_metadata = policy_raw.get("context_metadata")
            if isinstance(context_metadata, Mapping):
                if not isinstance(context_metadata, dict):
                    context_metadata = dict(context_metadata)
                    policy_raw["context_metadata"] = context_metadata
                context_metadata.setdefault("agent_id", self.agent_id)
            else:
                context_metadata = {"agent_id": self.agent_id}
                policy_raw["context_metadata"] = context_metadata
            context_metadata.setdefault(
                "allow_background_write_authorization",
                str(self.allow_background_write_authorization).lower(),
            )
        _apply_reactions_default_policy(
            policy=self.policy,
            runtime_config=runtime_config,
        )
        registry_prepopulated = runtime_registry is not None
        if runtime_registry is not None:
            self.registry = runtime_registry
        else:
            try:
                from openminion.modules.tool import build_default_tool_registry

                self.registry = build_default_tool_registry(config=runtime_config)
                registry_prepopulated = True
            except ImportError:
                self.registry = ToolRegistry()
            except RuntimeError as e:
                self.registry = ToolRegistry()
                _log.warning(
                    "tool registry build failed due to missing optional modules; using fallback registry: %s",
                    e,
                )

        if not registry_prepopulated and HAS_OS_PLUGIN:
            openminion_tool_os.plugin.register(self.registry)
        if not registry_prepopulated and HAS_BROWSER_PINCHTAB_PLUGIN:
            openminion_tool_browser_pinchtab_plugin.register(self.registry)
        if (
            not registry_prepopulated
            and HAS_REACTIONS_PLUGIN
            and self.reactions_enabled
        ):
            openminion_tools_reaction_plugin.register(self.registry)

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

        class _CompositePolicyAdapter(PolicyAdapter):
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
        raw = getattr(policy or self.policy, "raw", None)
        if isinstance(raw, Mapping):
            workspace_root = str(raw.get("workspace_root", "") or "").strip()
            if workspace_root:
                return Path(workspace_root).expanduser()
        return self.workspace_root

    def execute(
        self, *, command: dict[str, Any], session_id: str, trace_id: str
    ) -> dict[str, Any]:
        tool_name = str(command.get("tool_name", ""))
        raw_args = command.get("args", {})
        args = dict(raw_args) if isinstance(raw_args, Mapping) else {}
        inputs = command.get("inputs")
        permission_mode = canonical_permission_mode(
            str(inputs.get("permission_mode")).strip()
            if isinstance(inputs, Mapping) and inputs.get("permission_mode")
            else "default"
        )
        replay_confirmation_metadata = _confirmation_replay_metadata(inputs)
        start_time = time.monotonic()
        runtime_message_ref = _extract_runtime_message_ref(command=command, args=args)
        orchestration_metadata = _orchestration_metadata_from_command(command)
        if (
            runtime_message_ref is not None
            and tool_name.startswith("reactions.")
            and not args.get("message")
        ):
            args["message"] = runtime_message_ref

        spec = None
        runtime_tool = None

        def _assign_registry_entry(entry: Any) -> bool:
            nonlocal spec, runtime_tool
            if entry is None:
                return False
            if hasattr(entry, "execute") and not hasattr(entry, "handler"):
                runtime_tool = entry
                return True
            if spec is None:
                spec = entry
            return True

        if hasattr(self.registry, "get"):
            try:
                spec = self.registry.get(tool_name)
            except KeyError:
                spec = None
        if spec is None:
            tools_dict = getattr(self.registry, "_tools", None)
            if isinstance(tools_dict, Mapping):
                found = _assign_registry_entry(tools_dict.get(tool_name))
                if not found:
                    resolution = resolve_binding_for_call(
                        raw_tool_name=tool_name,
                        available_tool_names=tuple(tools_dict.keys()),
                    )
                    if resolution is not None and resolution.runtime_tool_name:
                        resolved_name = str(resolution.runtime_tool_name).strip()
                        if resolved_name:
                            if _assign_registry_entry(tools_dict.get(resolved_name)):
                                tool_name = resolved_name
        elif (
            runtime_tool is None
            and hasattr(spec, "execute")
            and not hasattr(spec, "handler")
        ):
            runtime_tool = spec

        if spec is None and runtime_tool is None:
            return _error_envelope(
                status=BRAIN_STATE_ERROR,
                summary=f"Unknown tool: {tool_name}",
                code="NOT_FOUND",
                message=f"Tool '{tool_name}' is not registered.",
            )
        if runtime_tool is not None:
            if isinstance(runtime_tool, ToolSpec):
                spec = runtime_tool
                runtime_tool = None

        policy_for_run = self.policy
        if runtime_message_ref is not None:
            policy_raw = copy.deepcopy(getattr(self.policy, "raw", {}) or {})
            tools_cfg = policy_raw.setdefault("tools", {})
            if isinstance(tools_cfg, dict):
                reactions_cfg = tools_cfg.setdefault("reactions", {})
                if isinstance(reactions_cfg, dict):
                    reactions_cfg["runtime_message_ref"] = runtime_message_ref
            policy_for_run = Policy(raw=policy_raw)
        policy_raw = getattr(policy_for_run, "raw", None)
        if isinstance(policy_raw, Mapping):
            policy_raw["agent_id"] = self.agent_id
            _merge_orchestration_context_metadata(policy_raw, orchestration_metadata)
            context_metadata = policy_raw.get("context_metadata")
            if isinstance(context_metadata, Mapping):
                if not isinstance(context_metadata, dict):
                    context_metadata = dict(context_metadata)
                    policy_raw["context_metadata"] = context_metadata
                context_metadata.setdefault("agent_id", self.agent_id)
            else:
                context_metadata = {"agent_id": self.agent_id}
                policy_raw["context_metadata"] = context_metadata
            if isinstance(replay_confirmation_metadata, Mapping):
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
            args_model = getattr(spec, "args_model", None)
            if args_model is None:
                validated_args = dict(args) if isinstance(args, Mapping) else {}
            elif args_model is dict:
                validated_args = dict(args) if isinstance(args, Mapping) else {}
            elif hasattr(args_model, "model_validate"):
                validated_args = args_model.model_validate(args).model_dump()
            else:
                validated_args = dict(args) if isinstance(args, Mapping) else {}
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
        background_write_authorized = (
            isinstance(inputs, Mapping)
            and bool(inputs.get("background_write_authorized"))
            and str(inputs.get("background_write_authorization_source", "") or "")
            == "watch_subscription"
        )

        auto_confirm = False
        if permission_mode == "bypass":
            auto_confirm = True
        elif permission_mode == "auto":
            auto_confirm = tool_name in {
                MODEL_FILE_WRITE,
                "file.copy",
                "file.move",
            }
        elif (
            replay_confirmed
            or watch_write_authorization_requested
            and permission_mode == "bypass"
            or background_write_authorized
        ):
            auto_confirm = True
        elif tool_name == "exec.run":
            auto_confirm = is_read_only_exec_command(
                str(validated_args.get("command", "") or ""),
                shell_family=resolve_shell_family(),
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

        ctx = RuntimeContext(
            policy=policy_for_run,
            workspace=effective_workspace_root,
            run_root=run_root,
            scope=policy_for_run.max_scope(),
            confirm=auto_confirm,
            repositories=build_runtime_repositories(
                context_metadata=(getattr(policy_for_run, "raw", {}) or {}).get(
                    "context_metadata"
                )
            ),
            logs=[],
            artifacts=[],
            safety_adapter=AllowAllSafetyAdapter(),
            policy_adapter=policy_adapter,
            skill_api=self.skill_api,
            artifactctl=self.artifactctl,
            agent_profile=self.agent_profile,
        )
        ctx.session_id = session_id
        ctx.trace_id = trace_id
        ctx.agent_id = self.agent_id
        ctx.run_id = run_id
        ctx.tool_name = tool_name
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
            data = spec.handler(validated_args, ctx)
            if isinstance(data, Mapping) and "status" in data:
                inner_status = str(data.get("status", BRAIN_STATE_ERROR))
            elif isinstance(data, Mapping) and isinstance(data.get("ok"), bool):
                inner_status = "ok" if bool(data.get("ok")) else BRAIN_STATE_ERROR
            else:
                inner_status = "ok"
            status = (
                BRAIN_ACTION_STATUS_SUCCESS
                if inner_status in ("ok", BRAIN_JOB_STATUS_RUNNING)
                else BRAIN_STATE_ERROR
            )

            summary = _derive_toolspec_summary(data, status=status, tool_name=spec.name)

            artifact_refs = []
            for art in ctx.artifacts:
                ref = preferred_artifact_ref(art)
                if ref:
                    artifact_refs.append({"ref": ref, "role": "output"})

            result = {
                "status": status,
                "summary": summary,
                "outputs": data,
                "artifact_refs": artifact_refs,
                "memory_refs": [],
                "metrics": {
                    "latency_ms": int((time.monotonic() - start_time) * 1000),
                    "tokens_used": 0,
                    "cost_estimate": 0.0,
                },
            }
            if background_write_authorized:
                result["outputs"] = dict(result["outputs"])
                result["outputs"]["background_watch_write_authorized"] = True
                result["outputs"]["background_watch_write_tool"] = tool_name
            if status != "success":
                error_payload = {}
                if isinstance(data, Mapping):
                    raw_error = data.get("error")
                    if isinstance(raw_error, Mapping):
                        error_payload = {
                            "code": str(raw_error.get("code", "") or "EXEC_ERROR"),
                            "message": str(
                                raw_error.get("message", "") or summary
                            ).strip(),
                        }
                    elif raw_error:
                        error_payload = {
                            "code": "EXEC_ERROR",
                            "message": str(raw_error).strip(),
                        }
                if not error_payload:
                    error_payload = {"code": "EXEC_ERROR", "message": summary}
                result["error"] = error_payload
            return result

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
        except Exception as exc:
            return _error_envelope(
                status=BRAIN_STATE_ERROR,
                summary="Tool execution failed",
                code="EXEC_ERROR",
                message=str(exc),
                latency_ms=int((time.monotonic() - start_time) * 1000),
            )

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
        try:
            from openminion.modules.tool import ToolExecutionContext
        except Exception as exc:
            return _error_envelope(
                status=BRAIN_STATE_ERROR,
                summary="Tool runtime unavailable",
                code="EXEC_ERROR",
                message=str(exc),
                latency_ms=int((time.monotonic() - start_time) * 1000),
            )

        effective_policy = policy or self.policy
        policy_raw = getattr(effective_policy, "raw", {}) or {}
        context_metadata = policy_raw.get("context_metadata")
        metadata = (
            dict(context_metadata) if isinstance(context_metadata, Mapping) else {}
        )
        workspace_root = str(policy_raw.get("workspace_root", "") or "").strip()
        if workspace_root:
            metadata.setdefault("workspace_root", workspace_root)
        metadata.update(
            {
                "agent_id": self.agent_id,
                "trace_id": trace_id,
                "runtime_env": _runtime_env_from_policy(effective_policy),
                **(
                    {"orchestration": dict(orchestration_metadata)}
                    if isinstance(orchestration_metadata, Mapping)
                    and orchestration_metadata
                    else {}
                ),
                **build_runtime_tool_routing_metadata(
                    resolve_runtime_tool_config(effective_policy)
                ),
            }
        )
        if isinstance(replay_confirmation_metadata, Mapping):
            metadata.update(
                {
                    key: value
                    for key, value in replay_confirmation_metadata.items()
                    if str(value or "").strip()
                }
            )
        context = ToolExecutionContext(
            channel="console",
            target=session_id or "session",
            session_id=session_id,
            metadata=metadata,
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
