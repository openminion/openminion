import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import typer

from openminion.base.generated_paths import resolve_generated_config_path
from openminion.modules.tool.cli import _execute_call_payload
from openminion.modules.tool.contracts.schemas import ResultEnvelope
from openminion.tools.config import get_tool_env

from openminion.modules.tool.constants import DEFAULT_POLICY_FILENAME

from .constants import EXEC_POLICY_PATH_ENV

app = typer.Typer(add_completion=False, no_args_is_help=True)

_POLICY_OVERRIDE = get_tool_env(EXEC_POLICY_PATH_ENV, "").strip()
DEFAULT_POLICY_PATH = (
    Path(_POLICY_OVERRIDE).expanduser()
    if _POLICY_OVERRIDE
    else resolve_generated_config_path(DEFAULT_POLICY_FILENAME)
)


def _parse_env_overrides(items: List[str]) -> Dict[str, str]:
    parsed: Dict[str, str] = {}
    for item in items:
        if "=" not in str(item):
            raise typer.BadParameter(
                f"Invalid --env value '{item}'; expected KEY=VALUE"
            )
        key, value = str(item).split("=", 1)
        key = key.strip()
        if not key:
            raise typer.BadParameter("Environment variable key cannot be empty")
        parsed[key] = value
    return parsed


def _print_env_and_exit(env: ResultEnvelope, exit_code: int, json_out: bool) -> None:
    payload = env.model_dump_json(indent=2)
    print(payload)
    if exit_code:
        raise typer.Exit(code=exit_code)


def _run_exec_tool(
    *,
    tool: str,
    args: Dict[str, Any],
    policy: Path,
    workspace: Optional[Path],
    scope: Optional[str],
    confirm: bool,
    timeout_sec: Optional[int],
    json_out: bool,
) -> None:
    payload = json.dumps({"tool": tool, "args": args}, ensure_ascii=True)
    env, exit_code = _execute_call_payload(
        payload=payload,
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=timeout_sec,
    )
    _print_env_and_exit(env, exit_code, json_out=json_out)


@app.command("run")
def run_cmd(
    command: str = typer.Argument(..., help="Shell command to execute"),
    workdir: Optional[str] = typer.Option(None, "--workdir"),
    env: List[str] = typer.Option(
        [], "--env", help="Environment override KEY=VALUE (repeatable)"
    ),
    yield_ms: int = typer.Option(10000, "--yield-ms"),
    background: bool = typer.Option(False, "--background"),
    timeout_s: int = typer.Option(1800, "--timeout-s"),
    pty: bool = typer.Option(False, "--pty"),
    host: str = typer.Option("sandbox", "--host"),
    security: str = typer.Option("deny", "--security"),
    ask: str = typer.Option("on-miss", "--ask"),
    node: Optional[str] = typer.Option(None, "--node"),
    policy: Path = typer.Option(DEFAULT_POLICY_PATH, "--policy"),
    workspace: Optional[Path] = typer.Option(None, "--workspace"),
    scope: Optional[str] = typer.Option(None, "--scope"),
    confirm: bool = typer.Option(False, "--confirm"),
    outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    args: Dict[str, Any] = {
        "command": command,
        "yield_ms": int(yield_ms),
        "background": bool(background),
        "timeout_s": int(timeout_s),
        "pty": bool(pty),
        "host": host,
        "security": security,
        "ask": ask,
        "env": _parse_env_overrides(env),
    }
    if workdir:
        args["workdir"] = workdir
    if node:
        args["node"] = node
    _run_exec_tool(
        tool="exec.run",
        args=args,
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=outer_timeout_sec,
        json_out=json_out,
    )


@app.command("poll")
def poll_cmd(
    session_id: str = typer.Argument(..., help="Session id from exec.run"),
    tail_lines: int = typer.Option(200, "--tail-lines"),
    policy: Path = typer.Option(DEFAULT_POLICY_PATH, "--policy"),
    workspace: Optional[Path] = typer.Option(None, "--workspace"),
    scope: Optional[str] = typer.Option(None, "--scope"),
    confirm: bool = typer.Option(False, "--confirm"),
    outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    _run_exec_tool(
        tool="exec.poll",
        args={"session_id": session_id, "tail_lines": int(tail_lines)},
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=outer_timeout_sec,
        json_out=json_out,
    )


@app.command("send-keys")
def send_keys_cmd(
    session_id: str = typer.Argument(..., help="Session id"),
    keys: List[str] = typer.Argument(..., help="Keys to send (e.g. Enter C-c Up)"),
    policy: Path = typer.Option(DEFAULT_POLICY_PATH, "--policy"),
    workspace: Optional[Path] = typer.Option(None, "--workspace"),
    scope: Optional[str] = typer.Option(None, "--scope"),
    confirm: bool = typer.Option(False, "--confirm"),
    outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    _run_exec_tool(
        tool="exec.send_keys",
        args={"session_id": session_id, "keys": keys},
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=outer_timeout_sec,
        json_out=json_out,
    )


@app.command("submit")
def submit_cmd(
    session_id: str = typer.Argument(..., help="Session id"),
    policy: Path = typer.Option(DEFAULT_POLICY_PATH, "--policy"),
    workspace: Optional[Path] = typer.Option(None, "--workspace"),
    scope: Optional[str] = typer.Option(None, "--scope"),
    confirm: bool = typer.Option(False, "--confirm"),
    outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    _run_exec_tool(
        tool="exec.submit",
        args={"session_id": session_id},
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=outer_timeout_sec,
        json_out=json_out,
    )


@app.command("paste")
def paste_cmd(
    session_id: str = typer.Argument(..., help="Session id"),
    text: str = typer.Argument(..., help="Text payload"),
    bracketed: bool = typer.Option(True, "--bracketed/--no-bracketed"),
    policy: Path = typer.Option(DEFAULT_POLICY_PATH, "--policy"),
    workspace: Optional[Path] = typer.Option(None, "--workspace"),
    scope: Optional[str] = typer.Option(None, "--scope"),
    confirm: bool = typer.Option(False, "--confirm"),
    outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    _run_exec_tool(
        tool="exec.paste",
        args={"session_id": session_id, "text": text, "bracketed": bool(bracketed)},
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=outer_timeout_sec,
        json_out=json_out,
    )


@app.command("kill")
def kill_cmd(
    session_id: str = typer.Argument(..., help="Session id"),
    signal_name: Optional[str] = typer.Option(None, "--signal"),
    policy: Path = typer.Option(DEFAULT_POLICY_PATH, "--policy"),
    workspace: Optional[Path] = typer.Option(None, "--workspace"),
    scope: Optional[str] = typer.Option(None, "--scope"),
    confirm: bool = typer.Option(False, "--confirm"),
    outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    args: Dict[str, Any] = {"session_id": session_id}
    if signal_name:
        args["signal"] = signal_name
    _run_exec_tool(
        tool="exec.kill",
        args=args,
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=outer_timeout_sec,
        json_out=json_out,
    )


@app.command("clear")
def clear_cmd(
    session_id: str = typer.Argument(..., help="Session id"),
    policy: Path = typer.Option(DEFAULT_POLICY_PATH, "--policy"),
    workspace: Optional[Path] = typer.Option(None, "--workspace"),
    scope: Optional[str] = typer.Option(None, "--scope"),
    confirm: bool = typer.Option(False, "--confirm"),
    outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    _run_exec_tool(
        tool="exec.clear",
        args={"session_id": session_id},
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=outer_timeout_sec,
        json_out=json_out,
    )


@app.command("list")
def list_cmd(
    include_exited: bool = typer.Option(False, "--include-exited"),
    policy: Path = typer.Option(DEFAULT_POLICY_PATH, "--policy"),
    workspace: Optional[Path] = typer.Option(None, "--workspace"),
    scope: Optional[str] = typer.Option(None, "--scope"),
    confirm: bool = typer.Option(False, "--confirm"),
    outer_timeout_sec: Optional[int] = typer.Option(None, "--outer-timeout-sec"),
    json_out: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    _run_exec_tool(
        tool="exec.list",
        args={"include_exited": bool(include_exited)},
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=outer_timeout_sec,
        json_out=json_out,
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
