import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from openminion.modules.tool import (
    DEFAULT_POLICY,
    Policy,
    ToolSpec,
    canonical_tool_name,
)
from openminion.modules.tool.plugin_api import (
    PolicyAdapter,
    PolicyDecision,
    is_policy_authorization_pair,
)
from openminion.modules.tool.adapters import LocalPolicyAdapter
from openminion.modules.tool.contracts.model_ids import (
    MODEL_TASK_WATCH,
    MODEL_GIT_STATUS,
    MODEL_FILE_LIST_DIR,
    MODEL_FILE_READ,
    MODEL_FILE_READ_RANGE,
    MODEL_FILE_FIND,
    MODEL_FILE_SEARCH,
    MODEL_FILE_WRITE,
    MODEL_FILE_EDIT,
    MODEL_CODE_PATCH,
    MODEL_CODE_GREP,
    MODEL_CODE_REPO_MAP,
    MODEL_CODE_REPO_INDEX,
    MODEL_CODE_SYMBOL_FIND,
    MODEL_AGENT_LIST,
    MODEL_AGENT_GET,
    MODEL_PLAN_SET,
    MODEL_PLAN_ADD,
    MODEL_PLAN_UPDATE,
    MODEL_PLAN_COMPLETE,
    MODEL_PLAN_LIST,
    MODEL_PLAN_CLEAR,
    MODEL_TASK_SHOW,
    MODEL_TASK_LIST,
    MODEL_TASK_DELEGATE,
)
from openminion.modules.tool.contracts.schemas import Scope, TOOL_ERROR_CONFIRM_REQUIRED
from openminion.modules.policy.models import build_policy_facts
from openminion.modules.brain.adapters.tool.permission_mode import (
    WORKSPACE_AUTO_TOOL_NAMES,
)
from openminion.tools.exec.command_parser import is_read_only_exec_command
from openminion.tools.exec.process import resolve_shell_family
from openminion.modules.task.project.models import ProjectCheckpoint
from openminion.modules.task.project.verification import check_security_repair_baseline
from openminion.modules.task.project.models import SecurityRepairContext
from openminion.modules.tool.errors import ToolRuntimeError

from .workspace_policy import workspace_context_policy
from .command_metadata import _merge_orchestration_context_metadata

if TYPE_CHECKING:
    from .execution_context import ToolExecutionContextBuilder

_REACTIONS_SET_TOOL_NAME = "reactions.set"
_REACTIONS_DEFAULT_POLICIES = frozenset({"allow", "deny", "confirm"})

_SECURITY_REPAIR_TOOLS = frozenset(
    {
        MODEL_FILE_LIST_DIR,
        MODEL_FILE_READ,
        MODEL_FILE_READ_RANGE,
        MODEL_FILE_FIND,
        MODEL_FILE_SEARCH,
        MODEL_FILE_WRITE,
        MODEL_FILE_EDIT,
        MODEL_CODE_PATCH,
        MODEL_CODE_GREP,
        MODEL_CODE_REPO_MAP,
        MODEL_CODE_REPO_INDEX,
        MODEL_CODE_SYMBOL_FIND,
        MODEL_GIT_STATUS,
        MODEL_AGENT_LIST,
        MODEL_AGENT_GET,
        MODEL_PLAN_SET,
        MODEL_PLAN_ADD,
        MODEL_PLAN_UPDATE,
        MODEL_PLAN_COMPLETE,
        MODEL_PLAN_LIST,
        MODEL_PLAN_CLEAR,
        MODEL_TASK_SHOW,
        MODEL_TASK_LIST,
        MODEL_TASK_DELEGATE,
    }
)


def _prepare_tool_policy(
    policy: Policy,
    *,
    parent: Path,
    active: Path | None,
    added_roots: tuple[Path, ...],
    task_manager: Any,
    agent_id: str,
    builder: "ToolExecutionContextBuilder",
    scan_context: str | None,
    args: dict[str, Any],
    requested_workspace: str,
    spec: Any,
    runtime_message_ref: Mapping[str, Any] | None,
    tool_name: str,
    session_id: str,
    orchestration_metadata: dict[str, Any],
    replay_confirmation_metadata: dict[str, str],
) -> Policy:
    policy = workspace_context_policy(
        policy,
        args=args,
        parent=parent,
        requested=requested_workspace,
        active=active,
        added_roots=added_roots,
    )
    policy = _tool_run_policy(
        policy,
        runtime_message_ref=runtime_message_ref,
        spec=spec if isinstance(spec, ToolSpec) else None,
        tool_name=tool_name,
    )
    policy.raw["agent_id"] = agent_id
    _merge_orchestration_context_metadata(policy.raw, orchestration_metadata)
    metadata = cast(dict[str, Any], policy.raw["context_metadata"])
    metadata.setdefault("agent_id", agent_id)
    builder.project_security_lab(metadata, session_id)
    if scan_context is not None:
        metadata["security_scan_context"] = scan_context
    metadata.update(replay_confirmation_metadata)
    workspace = Path(policy.raw.get("workspace_root") or parent).expanduser()
    try:
        return _apply_security_repair_policy(
            policy,
            task_manager=task_manager,
            agent_id=agent_id,
            workspace=workspace,
            orchestration=orchestration_metadata,
            tool_name=tool_name,
            args=args,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolRuntimeError("POLICY_DENIED", str(exc)) from exc


def _apply_security_repair_policy(
    policy: Policy,
    *,
    task_manager: Any,
    agent_id: str,
    workspace: Path,
    orchestration: Mapping[str, Any],
    tool_name: str,
    args: Mapping[str, Any],
) -> Policy:
    binding_raw = orchestration.get("security_repair_binding")
    task_id = str(orchestration.get("project_task_id") or "")
    binding = json.loads(binding_raw) if binding_raw else None
    if binding is not None:
        task_id = binding["task_id"]
    if not task_id or task_manager is None:
        if binding is not None:
            raise ValueError("Security repair child has no project owner")
        return policy
    latest = task_manager.get_latest_checkpoint(task_id)
    if latest is None or "security_repair" not in latest[1].get("payload", {}):
        if binding is not None:
            raise ValueError("Security repair child has no canonical repair context")
        return policy
    checkpoint = ProjectCheckpoint.model_validate(latest[1])
    context = SecurityRepairContext.model_validate(
        checkpoint.payload["security_repair"]
    )
    task = task_manager.get_task(task_id)
    if binding is not None:
        active = task.metadata.get("security_repair_child")
        if (
            not isinstance(active, dict)
            or binding
            != {
                "task_id": task_id,
                "binding_id": active["binding_id"],
                "parent_agent_id": active["parent_agent_id"],
                "target_agent_id": active["target_agent_id"],
            }
            or active["checkpoint_id"] != checkpoint.checkpoint_id
            or active["target_agent_id"] != agent_id
            or Path(active["workspace"]).resolve() != workspace.resolve()
        ):
            raise ValueError("Security repair child binding does not match its owner")
    elif (
        task.agent_id != agent_id
        or workspace.resolve() != Path(context.repository).resolve()
    ):
        raise ValueError("Security repair project workspace or agent does not match")
    check_security_repair_baseline(checkpoint)
    if (binding is None or active["readonly"]) and tool_name in {
        MODEL_FILE_WRITE,
        MODEL_FILE_EDIT,
        MODEL_CODE_PATCH,
    }:
        raise ToolRuntimeError(
            "POLICY_DENIED",
            "Security repair writes require a scoped implementation child",
        )
    if tool_name not in _SECURITY_REPAIR_TOOLS or (
        tool_name == MODEL_TASK_DELEGATE
        and (
            binding is not None
            or args.get("mode", "sync") not in {"sync", "review", "accept", "reject"}
            or (
                args.get("mode", "sync") == "sync"
                and args.get("code_bearing") is not True
            )
        )
    ):
        raise ToolRuntimeError(
            "POLICY_DENIED", "Tool is outside the approved security repair scope"
        )
    if binding is None or not active["readonly"]:
        for path in context.write_paths:
            policy.ensure_path_allowed(
                str(Path(context.repository) / path), Path(context.repository), "write"
            )
    policy = Policy(raw=copy.deepcopy(policy.raw))
    policy.raw["paths"]["write_allow"] = [
        str(workspace / path) for path in context.write_paths
    ]
    policy.raw["context_metadata"]["security_repair"] = context.model_dump(mode="json")
    return policy


def _coerce_policy(policy: Any) -> Policy:
    if policy is None:
        return Policy(raw=copy.deepcopy(DEFAULT_POLICY))
    if isinstance(policy, Policy):
        return policy
    raw = policy if isinstance(policy, Mapping) else getattr(policy, "raw", None)
    if isinstance(raw, Mapping):
        merged = copy.deepcopy(DEFAULT_POLICY)
        merged.update(dict(raw))
        return Policy(raw=merged)
    raise ValueError(
        f"policy_mismatch: Unsupported policy type: {type(policy).__name__}"
    )


def _is_confirm_required_code(code: Any) -> bool:
    return str(code or "").strip().upper() == str(TOOL_ERROR_CONFIRM_REQUIRED)


def _policy_context_metadata(policy: Policy) -> Any:
    raw = getattr(policy, "raw", {}) or {}
    return raw.get("context_metadata") if isinstance(raw, Mapping) else None


def _tool_run_policy(
    policy: Policy,
    *,
    runtime_message_ref: Mapping[str, Any] | None,
    spec: ToolSpec | None,
    tool_name: str,
) -> Policy:
    expose_runtime_name = bool(
        isinstance(spec, ToolSpec)
        and getattr(spec, "prompt_visible_runtime_name", False)
    )
    if runtime_message_ref is None and not expose_runtime_name:
        return policy
    policy_raw = copy.deepcopy(getattr(policy, "raw", {}) or {})
    tools_cfg = _ensure_mutable_mapping(policy_raw, "tools")
    if runtime_message_ref is not None:
        reactions_cfg = _ensure_mutable_mapping(tools_cfg, "reactions")
        reactions_cfg["runtime_message_ref"] = runtime_message_ref
    if expose_runtime_name:
        allow_exact = list(tools_cfg.get("allow_exact", []) or [])
        if tool_name not in allow_exact:
            tools_cfg["allow_exact"] = [*allow_exact, tool_name]
    return Policy(raw=policy_raw)


def _compose_policy_adapter(
    *,
    base_adapter: PolicyAdapter,
    extra_adapter: PolicyAdapter | None,
    approval_satisfied: bool,
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
                    if decision.requires_confirm and approval_satisfied:
                        continue
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


def _build_policy_adapter(
    *,
    tool_name: str,
    tool_spec: ToolSpec,
    policy: Policy,
    workspace: Path,
    confirm: bool,
    extra_adapter: PolicyAdapter | None,
    approval_satisfied: bool,
) -> PolicyAdapter | None:
    if tool_name == "ops.command.run":
        return None
    scope: Scope = tool_spec.min_scope if approval_satisfied else policy.max_scope()
    return _compose_policy_adapter(
        base_adapter=LocalPolicyAdapter(
            policy=policy, workspace=workspace, scope=scope, confirm=confirm
        ),
        extra_adapter=extra_adapter,
        approval_satisfied=approval_satisfied,
    )


def _ensure_mutable_mapping(owner: dict[str, Any], key: str) -> dict[str, Any]:
    current = owner.get(key)
    if not isinstance(current, dict):
        current = dict(current) if isinstance(current, Mapping) else {}
        owner[key] = current
    return current


def _ensure_mutable_str_list(owner: dict[str, Any], key: str) -> list[str]:
    current = owner.get(key)
    items = current if isinstance(current, (list, tuple, set, frozenset)) else ()
    replacement = [str(item).strip() for item in items if str(item).strip()]
    owner[key] = replacement
    return replacement


def _append_unique_tool_token(tokens: list[str], tool_name: str) -> None:
    target = canonical_tool_name(tool_name)
    existing = {canonical_tool_name(str(item)) for item in tokens if str(item).strip()}
    if target in existing:
        return
    tokens.append(tool_name)


def _normalize_reactions_default_policy(runtime_config: Any | None) -> str:
    runtime_cfg = getattr(runtime_config, "runtime", runtime_config)
    if runtime_cfg is None:
        return "allow"
    raw = str(getattr(runtime_cfg, "reactions_default_policy", "allow") or "allow")
    token = raw.strip().lower()
    if token not in _REACTIONS_DEFAULT_POLICIES:
        return "allow"
    return token


def _apply_reactions_default_policy(
    policy: Policy,
    runtime_config: Any | None,
) -> None:
    mode = _normalize_reactions_default_policy(runtime_config)
    if mode == "allow":
        return
    policy_raw = getattr(policy, "raw", None)
    if not isinstance(policy_raw, dict):
        return
    tools_cfg = _ensure_mutable_mapping(policy_raw, "tools")
    deny_exact = _ensure_mutable_str_list(tools_cfg, "deny_exact")
    if mode == "deny":
        _append_unique_tool_token(deny_exact, _REACTIONS_SET_TOOL_NAME)
        return
    if canonical_tool_name(_REACTIONS_SET_TOOL_NAME) in {
        canonical_tool_name(item) for item in deny_exact if str(item).strip()
    }:
        return
    confirm_cfg = _ensure_mutable_mapping(policy_raw, "confirm")
    required_tools = _ensure_mutable_str_list(confirm_cfg, "required_tools")
    _append_unique_tool_token(required_tools, _REACTIONS_SET_TOOL_NAME)


def _apply_agent_command_policy(policy: Policy, agent_profile: Any | None) -> None:
    command_policy = getattr(agent_profile, "command_policy", {})
    allowed = [
        str(item).strip()
        for item in command_policy.get("allow", ())
        if str(item).strip()
    ]
    allow_host = command_policy.get("allow_host") is True
    if not allowed and not allow_host:
        return

    policy_raw = policy.raw
    commands_cfg = _ensure_mutable_mapping(policy_raw, "commands")
    command_allowlist = _ensure_mutable_str_list(commands_cfg, "allow")
    for command in allowed:
        if command not in command_allowlist:
            command_allowlist.append(command)

    if allow_host:
        exec_cfg = _ensure_mutable_mapping(policy_raw, "exec")
        exec_cfg["host_enabled"] = True
        host_allowlist = _ensure_mutable_str_list(exec_cfg, "allowlist")
        for command in allowed:
            if command not in host_allowlist:
                host_allowlist.append(command)


def _runtime_env_from_policy(policy: Policy | None) -> dict[str, str]:
    raw = getattr(policy, "raw", None)
    if not isinstance(raw, Mapping):
        return {}
    context_metadata = raw.get("context_metadata")
    if isinstance(context_metadata, Mapping):
        runtime_env = context_metadata.get("runtime_env")
        if isinstance(runtime_env, Mapping):
            return {
                str(k): str(v) for k, v in runtime_env.items() if str(k or "").strip()
            }
    runtime_env = raw.get("runtime_env")
    if isinstance(runtime_env, Mapping):
        return {str(k): str(v) for k, v in runtime_env.items() if str(k or "").strip()}
    return {}


def _agent_id_from_policy(policy: Policy | None) -> str:
    raw = getattr(policy, "raw", None)
    if not isinstance(raw, Mapping):
        return "openminion"
    context_metadata = raw.get("context_metadata")
    if isinstance(context_metadata, Mapping):
        token = str(context_metadata.get("agent_id", "") or "").strip()
        if token:
            return token
    token = str(raw.get("agent_id", "") or "").strip()
    if token:
        return token
    runtime_env = _runtime_env_from_policy(policy)
    env_token = str(runtime_env.get("OPENMINION_AGENT_ID", "") or "").strip()
    if env_token:
        return env_token
    return "openminion"


def _runtime_background_write_authorization_enabled(runtime_config: Any | None) -> bool:
    runtime_cfg = getattr(runtime_config, "runtime", runtime_config)
    brain_cfg = getattr(runtime_cfg, "brain", None)
    return bool(getattr(brain_cfg, "allow_background_write_authorization", False))


def _watch_write_authorization_requested(
    *,
    tool_name: str,
    args: Mapping[str, Any],
) -> bool:
    return tool_name == MODEL_TASK_WATCH and bool(args.get("write_authorized", False))


def _background_write_authorized(inputs: Any) -> bool:
    return (
        isinstance(inputs, Mapping)
        and bool(inputs.get("background_write_authorized"))
        and str(inputs.get("background_write_authorization_source", "") or "")
        == "watch_subscription"
    )


def _permission_mode_origin(inputs: Any) -> str:
    if not isinstance(inputs, Mapping):
        return "global"
    return str(inputs.get("permission_mode_origin", "global"))


def _approval_policy_facts(
    *,
    tool_name: str,
    details: Mapping[str, Any] | None = None,
    reason_code: str = "",
) -> dict[str, Any]:
    source = details or {}
    raw_risk = source.get("risk")
    risk = (
        {str(key): value for key, value in raw_risk.items()}
        if isinstance(raw_risk, Mapping)
        else {}
    )
    return dict(
        build_policy_facts(
            canonical_tool=canonical_tool_name(tool_name),
            reason_code=str(reason_code or source.get("reason_code", "") or ""),
            risk=risk,
            duration_options=list(
                source.get("duration_options", source.get("choices", ())) or ()
            ),
        )
    )


def _resolve_auto_confirm(
    *,
    tool_name: str,
    args: Mapping[str, Any],
    permission_mode: str,
    permission_mode_origin: str = "global",
    replay_confirmed: bool,
    background_write_authorized: bool,
) -> bool:
    tool, method = (
        tool_name.rsplit(".", 1) if "." in tool_name else (tool_name, "default")
    )
    if is_policy_authorization_pair(tool, method):
        return replay_confirmed
    if permission_mode == "bypass":
        return True
    if replay_confirmed or background_write_authorized:
        return True
    if permission_mode == "auto":
        return (
            permission_mode_origin == "tool_override"
            or tool_name in WORKSPACE_AUTO_TOOL_NAMES
        )
    if tool_name == "blockchain.send_transaction":
        return True
    if tool_name in {"github.dispatch_workflow", "github.create_release"}:
        return True
    if tool_name == "exec.run":
        return bool(
            is_read_only_exec_command(
                str(args.get("command", "") or ""),
                shell_family=resolve_shell_family(),
            )
        )
    return False


__all__ = [
    "_agent_id_from_policy",
    "_approval_policy_facts",
    "_apply_agent_command_policy",
    "_apply_reactions_default_policy",
    "_background_write_authorized",
    "_build_policy_adapter",
    "_compose_policy_adapter",
    "_coerce_policy",
    "_is_confirm_required_code",
    "_policy_context_metadata",
    "_tool_run_policy",
    "_resolve_auto_confirm",
    "_permission_mode_origin",
    "_runtime_background_write_authorization_enabled",
    "_runtime_env_from_policy",
    "_watch_write_authorization_requested",
]
