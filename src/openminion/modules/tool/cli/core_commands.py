from pathlib import Path
from typing import Any, Optional
from collections.abc import Callable

import typer
from pydantic import ValidationError

from ..adapters import LocalPolicyAdapter
from ..errors import ToolRuntimeError
from ..runtime.policy import Policy


def _tool_row(spec: Any, *, include_schema: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "name": spec.name,
        "min_scope": spec.min_scope,
        "dangerous": spec.dangerous,
        "idempotent": spec.idempotent,
        "tags": list(spec.tags),
        "capabilities": list(spec.resolved_capabilities()),
    }
    if include_schema:
        row["args_schema"] = spec.args_model.model_json_schema()
    return row


def _evaluate_policy_explain(
    *,
    pol: Policy,
    reg: Any,
    op_workspace: Path,
    payload: Optional[str],
    scope: Optional[str],
    confirm: bool,
    parse_call_payload: Callable[[Optional[str]], Any],
    effective_scope: Callable[[Policy, Optional[str]], Any],
) -> dict[str, Any]:
    """Run the request → spec → adapter chain and return the explain payload."""
    try:
        req = parse_call_payload(payload)
        effective_scope_value = effective_scope(pol, scope)
        spec = reg.get(req.tool)
        args = spec.args_model.model_validate(req.args).model_dump()
        adapter = LocalPolicyAdapter(
            policy=pol,
            workspace=op_workspace,
            scope=effective_scope_value,
            confirm=confirm or req.meta.confirm,
        )
        decision = adapter.evaluate(tool_name=req.tool, tool_spec=spec, args=args)
        if not decision.allowed:
            raise ToolRuntimeError(decision.code, decision.reason, decision.details)
        return {
            "allowed": True,
            "tool": req.tool,
            "effective_scope": effective_scope_value,
            "reason": "Call schema and policy checks passed",
        }
    except KeyError:
        return {"allowed": False, "reason": "Unknown tool"}
    except ValidationError as exc:
        return {
            "allowed": False,
            "reason": "Tool args validation failed",
            "errors": exc.errors(),
        }
    except ToolRuntimeError as exc:
        return {
            "allowed": False,
            "reason": exc.message,
            "error": {"code": exc.code, "details": exc.details},
        }


def _register_tools_list(
    *,
    app: typer.Typer,
    default_policy_path: Path,
    print_obj: Callable[[dict[str, Any], bool], None],
    build_registry: Callable[[Policy], tuple[Any, list[dict[str, Any]]]],
) -> None:
    @app.command()
    def tools(
        json_out: bool = typer.Option(True, "--json/--no-json"),
        prefix: Optional[str] = typer.Option(None, "--prefix"),
        capability: Optional[str] = typer.Option(None, "--capability"),
        schema: bool = typer.Option(False, "--schema"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
    ) -> None:
        pol = Policy.load(policy)
        reg, _ = build_registry(pol)
        rows: list[dict[str, Any]] = []
        for name, spec in reg.list().items():
            if prefix and not name.startswith(prefix):
                continue
            if capability and capability not in spec.resolved_capabilities():
                continue
            row = _tool_row(spec, include_schema=schema)
            row["name"] = name
            rows.append(row)
        out = {"tools": sorted(rows, key=lambda item: item["name"])}
        print_obj(out, json_out=json_out)


def _register_tool_describe(
    *,
    tool_app: typer.Typer,
    default_policy_path: Path,
    print_obj: Callable[[dict[str, Any], bool], None],
    build_registry: Callable[[Policy], tuple[Any, list[dict[str, Any]]]],
) -> None:
    @tool_app.command("describe")
    def tool_describe(
        name: str = typer.Argument(..., help="Tool name, e.g. cmd.run"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
    ) -> None:
        pol = Policy.load(policy)
        reg, _ = build_registry(pol)
        try:
            spec = reg.get(name)
        except KeyError as exc:
            raise typer.BadParameter(f"Unknown tool: {name}") from exc
        out = _tool_row(spec, include_schema=True)
        print_obj(out, json_out=json_out)


def _register_policy_show(
    *,
    policy_app: typer.Typer,
    default_policy_path: Path,
    print_obj: Callable[[dict[str, Any], bool], None],
) -> None:
    @policy_app.command("show")
    def policy_show(
        policy: Path = typer.Option(default_policy_path, "--policy"),
        json_out: bool = typer.Option(False, "--json/--no-json"),
    ) -> None:
        pol = Policy.load(policy)
        print_obj(pol.raw, json_out=json_out)


def _register_policy_explain(
    *,
    policy_app: typer.Typer,
    default_policy_path: Path,
    print_obj: Callable[[dict[str, Any], bool], None],
    parse_call_payload: Callable[[Optional[str]], Any],
    build_registry: Callable[[Policy], tuple[Any, list[dict[str, Any]]]],
    effective_scope: Callable[[Policy, Optional[str]], Any],
) -> None:
    @policy_app.command("explain")
    def policy_explain(
        payload: Optional[str] = typer.Argument(
            None, help="Call payload JSON; stdin if omitted"
        ),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        confirm: bool = typer.Option(False, "--confirm"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        pol = Policy.load(policy)
        reg, _ = build_registry(pol)
        op_workspace = (workspace or Path.cwd()).expanduser().resolve(strict=False)
        out = _evaluate_policy_explain(
            pol=pol,
            reg=reg,
            op_workspace=op_workspace,
            payload=payload,
            scope=scope,
            confirm=confirm,
            parse_call_payload=parse_call_payload,
            effective_scope=effective_scope,
        )
        print_obj(out, json_out=json_out)


def _register_plugins_list(
    *,
    plugins_app: typer.Typer,
    default_policy_path: Path,
    print_obj: Callable[[dict[str, Any], bool], None],
    build_registry: Callable[[Policy], tuple[Any, list[dict[str, Any]]]],
) -> None:
    @plugins_app.command("list")
    def plugins_list(
        policy: Path = typer.Option(default_policy_path, "--policy"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        pol = Policy.load(policy)
        reg, statuses = build_registry(pol)
        del reg
        print_obj({"plugins": statuses}, json_out=json_out)


def _register_call(
    *,
    app: typer.Typer,
    default_policy_path: Path,
    execute_call_payload: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @app.command()
    def call(
        payload: Optional[str] = typer.Argument(
            None, help="Call JSON payload; reads stdin when omitted"
        ),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        env, exit_code = execute_call_payload(
            payload=payload,
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            timeout_sec=timeout_sec,
        )
        finalize_cli_call(env, exit_code, json_out)


def register_core_commands(
    *,
    app: typer.Typer,
    tool_app: typer.Typer,
    policy_app: typer.Typer,
    plugins_app: typer.Typer,
    default_policy_path: Path,
    print_obj: Callable[[dict[str, Any], bool], None],
    parse_call_payload: Callable[[Optional[str]], Any],
    build_registry: Callable[[Policy], tuple[Any, list[dict[str, Any]]]],
    effective_scope: Callable[[Policy, Optional[str]], Any],
    execute_call_payload: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    _register_tools_list(
        app=app,
        default_policy_path=default_policy_path,
        print_obj=print_obj,
        build_registry=build_registry,
    )
    _register_tool_describe(
        tool_app=tool_app,
        default_policy_path=default_policy_path,
        print_obj=print_obj,
        build_registry=build_registry,
    )
    _register_policy_show(
        policy_app=policy_app,
        default_policy_path=default_policy_path,
        print_obj=print_obj,
    )
    _register_policy_explain(
        policy_app=policy_app,
        default_policy_path=default_policy_path,
        print_obj=print_obj,
        parse_call_payload=parse_call_payload,
        build_registry=build_registry,
        effective_scope=effective_scope,
    )
    _register_plugins_list(
        plugins_app=plugins_app,
        default_policy_path=default_policy_path,
        print_obj=print_obj,
        build_registry=build_registry,
    )
    _register_call(
        app=app,
        default_policy_path=default_policy_path,
        execute_call_payload=execute_call_payload,
        finalize_cli_call=finalize_cli_call,
    )
