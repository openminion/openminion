import copy
import inspect
import json
import os
from pathlib import Path
from typing import Any, Mapping

from openminion.base.config.env import EnvironmentConfig
from openminion.base.config.env import resolve_environment_config
from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.modules.artifact.refs import create_default_artifactctl
from openminion.modules.tool.base import ToolExecutionContext, ToolExecutionResult
from openminion.modules.tool.runtime.argument_repair import (
    repair_structured_tool_arguments,
)
from openminion.modules.tool.runtime.result_cleaner import strip_tool_result_noise
from openminion.modules.tool.runtime.grounding_footer import with_source_footer

_POLICY_REPLAY_SOURCE = "policy_replay"
_CONFIRMATION_SOURCE_METADATA_KEY = "confirmation_source"
_CONFIRMATION_GRANT_ID_METADATA_KEY = "confirmation_grant_id"


def _context_confirm_requested(context: ToolExecutionContext) -> bool:
    metadata = context.metadata if isinstance(context.metadata, Mapping) else {}
    source = str(metadata.get(_CONFIRMATION_SOURCE_METADATA_KEY, "") or "").strip()
    grant_id = str(metadata.get(_CONFIRMATION_GRANT_ID_METADATA_KEY, "") or "").strip()
    return bool(grant_id) and source == _POLICY_REPLAY_SOURCE


def execute_tool_spec_call(
    *,
    tool: Any,
    arguments: Mapping[str, Any],
    context: ToolExecutionContext,
) -> ToolExecutionResult:
    from openminion.modules.tool.errors import ToolRuntimeError
    from openminion.modules.tool.runtime.policy import DEFAULT_POLICY, Policy
    from openminion.modules.tool.runtime import (
        RuntimeContext,
        build_runtime_repositories,
    )

    # Lazy import to break tool -> brain -> tool circular import (CQRC-161).
    from openminion.modules.brain.runtime.recovery import (
        TCRPContext,
        TCRPRetryBudget,
        validate_payload,
    )

    tool_name = str(getattr(tool, "name", "")).strip() or "unknown"
    args_model = getattr(tool, "args_model", dict)
    validated_args: dict[str, Any]
    try:
        if hasattr(args_model, "model_validate"):
            validation = validate_payload(
                repair_structured_tool_arguments(
                    dict(arguments),
                    channel_name=f"tool_spec:{tool_name}.args",
                    alias_map=_model_alias_map(args_model),
                ),
                model=args_model,
                ctx=TCRPContext(
                    channel_name=f"tool_spec:{tool_name}.args",
                    session_id=str(context.session_id or "").strip(),
                    agent_id=str((context.metadata or {}).get("agent_id", "") or ""),
                    trace_id=str((context.metadata or {}).get("trace_id", "") or ""),
                ),
                retry_budget=TCRPRetryBudget(
                    channel_name=f"tool_spec:{tool_name}.args",
                    max_retries=0,
                ),
            )
            if validation.structured_payload is None:
                message = "Invalid tool arguments."
                if validation.validation_errors:
                    message = (
                        f"Invalid tool arguments: "
                        f"{validation.validation_errors[0].field_path} "
                        f"{validation.validation_errors[0].error_code.value}"
                    )
                return ToolExecutionResult(
                    tool_name=tool_name,
                    ok=False,
                    content="",
                    verified=False,
                    error=message,
                    data={
                        "error_code": "invalid_arguments",
                        "reason_code": "tool_arg_validation_failed",
                        "tcrp.validation_errors": [
                            item.model_dump(mode="json")
                            for item in validation.validation_errors
                        ],
                    },
                )
            parsed = validation.structured_payload
            if hasattr(parsed, "model_dump"):
                validated_args = parsed.model_dump(exclude_none=True)
            elif isinstance(parsed, Mapping):
                validated_args = dict(parsed)
            else:
                validated_args = dict(arguments)
        elif args_model is dict or args_model is None:
            validated_args = dict(arguments)
        else:
            validated_args = dict(arguments)
    except Exception as exc:
        return ToolExecutionResult(
            tool_name=tool_name,
            ok=False,
            content="",
            verified=False,
            error=f"Invalid tool arguments: {exc}",
            data={
                "error_code": "invalid_arguments",
                "reason_code": "tool_arg_validation_failed",
            },
        )

    metadata = _normalized_workspace_metadata(context.metadata)
    workspace = resolve_workspace(context=context)
    run_root = resolve_run_root(workspace=workspace, context=context)
    policy_payload = copy.deepcopy(DEFAULT_POLICY)
    policy_payload["workspace_root"] = str(workspace)
    policy_payload["context_metadata"] = metadata
    agent_id = str(metadata.get("agent_id", "")).strip()
    if agent_id:
        policy_payload["agent_id"] = agent_id
    runtime_ctx = RuntimeContext(
        policy=Policy(raw=policy_payload),
        workspace=workspace,
        run_root=run_root,
        scope=resolve_tool_scope(tool=tool),
        confirm=_context_confirm_requested(context),
        env=EnvironmentConfig.from_sources(
            runtime_env=resolve_runtime_env(context=context),
        ),
        repositories=build_runtime_repositories(context_metadata=metadata),
        artifactctl=resolve_artifactctl(),
        memory_service=context.memory_service,
        sandbox_runner=context.sandbox_runner,
        authored_tools_api=context.authored_tools_api,
        a2a_delegate_api=context.a2a_delegate_api,
    )
    runtime_ctx.session_id = str(context.session_id or "").strip() or None
    runtime_ctx.trace_id = str(metadata.get("trace_id", "")).strip()
    runtime_ctx.agent_id = agent_id or None
    runtime_ctx.tool_name = tool_name

    handler = getattr(tool, "handler", None)
    if not callable(handler):
        return ToolExecutionResult(
            tool_name=tool_name,
            ok=False,
            content="",
            verified=False,
            error="tool handler is not callable",
            data={},
        )

    try:
        payload = invoke_tool_spec_handler(
            handler=handler,
            arguments=validated_args,
            runtime_ctx=runtime_ctx,
        )
    except ToolRuntimeError as exc:
        return ToolExecutionResult(
            tool_name=tool_name,
            ok=False,
            content="",
            verified=False,
            error=exc.message,
            data={"error_code": exc.code, "details": dict(exc.details or {})},
        )
    except Exception as exc:
        return ToolExecutionResult(
            tool_name=tool_name,
            ok=False,
            content="",
            verified=False,
            error=f"{type(exc).__name__}: {exc}",
            data={},
        )

    if isinstance(payload, Mapping):
        payload_dict = dict(payload)
    else:
        payload_dict = {"ok": True, "data": {"result": payload}}

    ok_field = payload_dict.get("ok")
    if ok_field is None:
        status_field = str(payload_dict.get("status", "") or "").strip().lower()
        error_field = payload_dict.get("error")
        if isinstance(error_field, Mapping):
            error_present = bool(error_field.get("message") or error_field.get("code"))
        else:
            error_present = bool(error_field)
        if status_field in {"error", "denied", "timeout"}:
            ok = False
        else:
            ok = not error_present
    else:
        ok = bool(ok_field)
    verified = bool(payload_dict.get("verified", ok))
    content = str(payload_dict.get("content", "") or "")
    raw_source = payload_dict.get("source", "")
    if isinstance(raw_source, Mapping):
        # Some tools (weather) carry source as a structured sub-dict; the
        # provider id is the structural identifier the footer needs.
        source = str(raw_source.get("provider_id", "") or "").strip()
    else:
        source = str(raw_source or "").strip()

    raw_error = payload_dict.get("error")
    if isinstance(raw_error, Mapping):
        error_message = str(raw_error.get("message") or raw_error.get("code") or "")
    else:
        error_message = str(raw_error or "")

    data: dict[str, Any] = {}
    data_field = payload_dict.get("data")
    if isinstance(data_field, Mapping):
        data = dict(data_field)
    else:
        for key, value in payload_dict.items():
            if key in {"ok", "verified", "content", "error", "source"}:
                continue
            data[key] = value

    data = strip_tool_result_noise(data)

    if not content and data:
        try:
            content = json.dumps(data, sort_keys=True, default=str)
        except Exception:
            content = str(data)

    # TGFC: append `source=<provider>` structural footer when execution
    if ok and source:
        content = with_source_footer(content, source)

    return ToolExecutionResult(
        tool_name=tool_name,
        ok=ok,
        content=content,
        verified=verified,
        error=error_message,
        data=data,
        source=source,
    )


def invoke_tool_spec_handler(
    *,
    handler: Any,
    arguments: Mapping[str, Any],
    runtime_ctx: Any,
) -> Any:
    try:
        signature = inspect.signature(handler)
        params = list(signature.parameters.values())
    except Exception:
        params = []

    if len(params) >= 2:
        first = str(params[0].name or "").strip().lower()
        second = str(params[1].name or "").strip().lower()
        first_is_ctx = "ctx" in first or "context" in first
        second_is_ctx = "ctx" in second or "context" in second
        if first_is_ctx and not second_is_ctx:
            return handler(runtime_ctx, arguments)
        if second_is_ctx and not first_is_ctx:
            return handler(arguments, runtime_ctx)

    first_error: Exception | None = None
    try:
        return handler(arguments, runtime_ctx)
    except Exception as exc:
        first_error = exc
    try:
        return handler(runtime_ctx, arguments)
    except Exception:
        if first_error is not None:
            raise first_error
        raise


def resolve_workspace(*, context: ToolExecutionContext) -> Path:
    metadata = _normalized_workspace_metadata(context.metadata)
    raw = str(metadata.get("workspace_root", "")).strip()
    if raw:
        candidate = Path(raw).expanduser()
    else:
        candidate = Path(os.getcwd())
    try:
        return candidate.resolve(strict=False)
    except Exception:
        return Path(os.getcwd()).resolve(strict=False)


def _normalized_workspace_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    normalized = dict(metadata or {})
    raw_root = str(normalized.get("workspace_root", "") or "").strip()
    raw_working_dir = str(normalized.get("working_dir", "") or "").strip()
    if not raw_root and raw_working_dir:
        normalized["workspace_root"] = raw_working_dir
    if not str(normalized.get("cwd", "") or "").strip() and raw_working_dir:
        normalized["cwd"] = raw_working_dir
    return normalized


def _model_alias_map(args_model: Any) -> dict[str, str]:
    fields = getattr(args_model, "model_fields", None)
    if not isinstance(fields, Mapping):
        return {}
    alias_map: dict[str, str] = {}
    for field_name, field_info in fields.items():
        alias = str(getattr(field_info, "alias", "") or "").strip()
        canonical = str(field_name or "").strip()
        if alias and canonical and alias != canonical:
            alias_map[alias] = canonical
    return alias_map


def resolve_runtime_env(*, context: ToolExecutionContext) -> dict[str, Any]:
    metadata = context.metadata if isinstance(context.metadata, Mapping) else {}
    payload = metadata.get("runtime_env")
    if isinstance(payload, Mapping):
        return dict(payload)
    if isinstance(payload, str):
        token = payload.strip()
        if token:
            try:
                decoded = json.loads(token)
            except Exception:
                decoded = None
            if isinstance(decoded, Mapping):
                return dict(decoded)
    return {}


def resolve_artifactctl() -> Any | None:
    try:
        return create_default_artifactctl()
    except Exception:
        return None


def resolve_run_root(*, workspace: Path, context: ToolExecutionContext) -> Path:
    del workspace
    session = str(context.session_id or "default").strip() or "default"
    safe_session = "".join(
        ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in session
    ).strip("-")
    if not safe_session:
        safe_session = "default"
    home_root = resolve_home_root()
    env_config = resolve_environment_config(
        runtime_env=resolve_runtime_env(context=context)
    )
    data_root = resolve_data_root(
        home_root,
        data_root=env_config.openminion_data_root or None,
    )
    run_root = data_root / "tool-runtime" / "sessions" / safe_session
    (run_root / "artifacts").mkdir(parents=True, exist_ok=True)
    return run_root


def resolve_tool_scope(*, tool: Any) -> str:
    min_scope = (
        str(getattr(tool, "min_scope", "WRITE_SAFE") or "WRITE_SAFE").strip().upper()
    )
    if min_scope in {"READ_ONLY", "WRITE_SAFE", "POWER_USER", "UI_AUTOMATION"}:
        return min_scope
    return "WRITE_SAFE"
