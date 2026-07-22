from pathlib import Path
from typing import Any, Optional
from collections.abc import Callable

import typer

from ..constants import TOOL_EXEC_ASK_ON_MISS, TOOL_EXEC_SECURITY_DENY


def _build_exec_run_args(
    *,
    command: str,
    workdir: Optional[str],
    env: list[str],
    yield_ms: int,
    background: bool,
    timeout_s: int,
    pty: bool,
    host: str,
    security: str,
    ask: str,
    node: Optional[str],
    parse_env_pairs: Callable[[list[str]], dict[str, str]],
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "command": command,
        "yield_ms": int(yield_ms),
        "background": bool(background),
        "timeout_s": int(timeout_s),
        "pty": bool(pty),
        "host": host,
        "security": security,
        "ask": ask,
        "env": parse_env_pairs(env),
    }
    if workdir:
        args["workdir"] = workdir
    if node:
        args["node"] = node
    return args


def _dispatch_exec(
    *,
    tool: str,
    args: dict[str, Any],
    policy: Path,
    workspace: Optional[Path],
    scope: Optional[str],
    confirm: bool,
    outer_timeout_sec: Optional[int],
    json_out: bool,
    invoke_exec_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    """Common dispatch path: `invoke_exec_tool` + `finalize_cli_call`.
    Preserves exact `tool=<name>` strings, arg shape, exit codes, and output."""
    env_out, exit_code = invoke_exec_tool(
        tool=tool,
        args=args,
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=outer_timeout_sec,
    )
    finalize_cli_call(env_out, exit_code, json_out)


def _register_exec_run(
    *,
    exec_app: typer.Typer,
    default_policy_path: Path,
    parse_env_pairs: Callable[[list[str]], dict[str, str]],
    invoke_exec_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @exec_app.command("run")
    def exec_run(
        command: str = typer.Argument(..., help="Shell command to execute"),
        workdir: Optional[str] = typer.Option(None, "--workdir"),
        env: list[str] = typer.Option(
            [], "--env", help="Environment override KEY=VALUE (repeatable)"
        ),
        yield_ms: int = typer.Option(10000, "--yield-ms"),
        background: bool = typer.Option(False, "--background"),
        timeout_s: int = typer.Option(1800, "--timeout-s"),
        pty: bool = typer.Option(False, "--pty"),
        host: str = typer.Option("sandbox", "--host"),
        security: str = typer.Option(TOOL_EXEC_SECURITY_DENY, "--security"),
        ask: str = typer.Option(TOOL_EXEC_ASK_ON_MISS, "--ask"),
        node: Optional[str] = typer.Option(None, "--node"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        _dispatch_exec(
            tool="exec.run",
            args=_build_exec_run_args(
                command=command,
                workdir=workdir,
                env=env,
                yield_ms=yield_ms,
                background=background,
                timeout_s=timeout_s,
                pty=pty,
                host=host,
                security=security,
                ask=ask,
                node=node,
                parse_env_pairs=parse_env_pairs,
            ),
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            outer_timeout_sec=outer_timeout_sec,
            json_out=json_out,
            invoke_exec_tool=invoke_exec_tool,
            finalize_cli_call=finalize_cli_call,
        )


def _register_exec_poll(
    *,
    exec_app: typer.Typer,
    default_policy_path: Path,
    invoke_exec_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @exec_app.command("poll")
    def exec_poll(
        session_id: str = typer.Argument(..., help="Session id returned by exec run"),
        tail_lines: int = typer.Option(200, "--tail-lines"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        _dispatch_exec(
            tool="exec.poll",
            args={"session_id": session_id, "tail_lines": int(tail_lines)},
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            outer_timeout_sec=outer_timeout_sec,
            json_out=json_out,
            invoke_exec_tool=invoke_exec_tool,
            finalize_cli_call=finalize_cli_call,
        )


def _register_exec_send_keys(
    *,
    exec_app: typer.Typer,
    default_policy_path: Path,
    invoke_exec_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @exec_app.command("send-keys")
    def exec_send_keys(
        session_id: str = typer.Argument(..., help="Session id"),
        keys: list[str] = typer.Argument(
            ..., help="Keys to send (examples: Enter, C-c, Up)"
        ),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        _dispatch_exec(
            tool="exec.send_keys",
            args={"session_id": session_id, "keys": keys},
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            outer_timeout_sec=outer_timeout_sec,
            json_out=json_out,
            invoke_exec_tool=invoke_exec_tool,
            finalize_cli_call=finalize_cli_call,
        )


def _register_exec_submit(
    *,
    exec_app: typer.Typer,
    default_policy_path: Path,
    invoke_exec_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @exec_app.command("submit")
    def exec_submit(
        session_id: str = typer.Argument(..., help="Session id"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        _dispatch_exec(
            tool="exec.submit",
            args={"session_id": session_id},
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            outer_timeout_sec=outer_timeout_sec,
            json_out=json_out,
            invoke_exec_tool=invoke_exec_tool,
            finalize_cli_call=finalize_cli_call,
        )


def _register_exec_paste(
    *,
    exec_app: typer.Typer,
    default_policy_path: Path,
    invoke_exec_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @exec_app.command("paste")
    def exec_paste(
        session_id: str = typer.Argument(..., help="Session id"),
        text: str = typer.Argument(..., help="Text to paste"),
        bracketed: bool = typer.Option(True, "--bracketed/--no-bracketed"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        _dispatch_exec(
            tool="exec.paste",
            args={"session_id": session_id, "text": text, "bracketed": bool(bracketed)},
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            outer_timeout_sec=outer_timeout_sec,
            json_out=json_out,
            invoke_exec_tool=invoke_exec_tool,
            finalize_cli_call=finalize_cli_call,
        )


def _register_exec_kill(
    *,
    exec_app: typer.Typer,
    default_policy_path: Path,
    invoke_exec_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @exec_app.command("kill")
    def exec_kill(
        session_id: str = typer.Argument(..., help="Session id"),
        signal_name: Optional[str] = typer.Option(None, "--signal"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        args: dict[str, Any] = {"session_id": session_id}
        if signal_name:
            args["signal"] = signal_name
        _dispatch_exec(
            tool="exec.kill",
            args=args,
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            outer_timeout_sec=outer_timeout_sec,
            json_out=json_out,
            invoke_exec_tool=invoke_exec_tool,
            finalize_cli_call=finalize_cli_call,
        )


def _register_exec_clear(
    *,
    exec_app: typer.Typer,
    default_policy_path: Path,
    invoke_exec_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @exec_app.command("clear")
    def exec_clear(
        session_id: str = typer.Argument(..., help="Session id"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        _dispatch_exec(
            tool="exec.clear",
            args={"session_id": session_id},
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            outer_timeout_sec=outer_timeout_sec,
            json_out=json_out,
            invoke_exec_tool=invoke_exec_tool,
            finalize_cli_call=finalize_cli_call,
        )


def _register_exec_list(
    *,
    exec_app: typer.Typer,
    default_policy_path: Path,
    invoke_exec_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @exec_app.command("list")
    def exec_list(
        include_exited: bool = typer.Option(False, "--include-exited"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        _dispatch_exec(
            tool="exec.list",
            args={"include_exited": bool(include_exited)},
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            outer_timeout_sec=outer_timeout_sec,
            json_out=json_out,
            invoke_exec_tool=invoke_exec_tool,
            finalize_cli_call=finalize_cli_call,
        )


def register_exec_commands(
    *,
    exec_app: typer.Typer,
    default_policy_path: Path,
    parse_env_pairs: Callable[[list[str]], dict[str, str]],
    invoke_exec_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    common = {
        "exec_app": exec_app,
        "default_policy_path": default_policy_path,
        "invoke_exec_tool": invoke_exec_tool,
        "finalize_cli_call": finalize_cli_call,
    }
    _register_exec_run(parse_env_pairs=parse_env_pairs, **common)
    _register_exec_poll(**common)
    _register_exec_send_keys(**common)
    _register_exec_submit(**common)
    _register_exec_paste(**common)
    _register_exec_kill(**common)
    _register_exec_clear(**common)
    _register_exec_list(**common)
