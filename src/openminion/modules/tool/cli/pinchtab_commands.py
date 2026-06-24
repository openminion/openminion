import json
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import typer

from openminion.tools.browser.providers.pinchtab.daemon import (
    daemon_status as pinchtab_daemon_status,
    ensure_daemon as ensure_pinchtab_daemon,
    stop_daemon as stop_pinchtab_daemon,
)


_PINCHTAB_ACTION_TOOLS: Dict[str, str] = {
    "click": "browser.pinchtab.click",
    "fill": "browser.pinchtab.fill",
    "type": "browser.pinchtab.type",
    "press": "browser.pinchtab.press",
    "hover": "browser.pinchtab.hover",
    "select": "browser.pinchtab.select",
    "scroll": "browser.pinchtab.scroll",
}


def _dispatch_pinchtab(
    *,
    tool: str,
    args: Dict[str, Any],
    policy: Path,
    workspace: Optional[Path],
    scope: Optional[str],
    confirm: bool,
    timeout_sec: Optional[int],
    json_out: bool,
    invoke_pinchtab_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> tuple[Any, int]:
    """Dispatch a PinchTab tool call and return ``(env, exit_code)``."""
    env, exit_code = invoke_pinchtab_tool(
        tool=tool,
        args=args,
        policy=policy,
        workspace=workspace,
        scope=scope,
        confirm=confirm,
        timeout_sec=timeout_sec,
    )
    return env, exit_code


def _register_pinchtab_health(
    *,
    pinchtab_app: typer.Typer,
    default_policy_path: Path,
    invoke_pinchtab_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @pinchtab_app.command("health")
    def pinchtab_health(
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        env, exit_code = _dispatch_pinchtab(
            tool="browser.pinchtab.health",
            args={},
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            timeout_sec=timeout_sec,
            json_out=json_out,
            invoke_pinchtab_tool=invoke_pinchtab_tool,
            finalize_cli_call=finalize_cli_call,
        )
        finalize_cli_call(env, exit_code, json_out)


def _register_pinchtab_instance_start(
    *,
    pinchtab_instance_app: typer.Typer,
    default_policy_path: Path,
    invoke_pinchtab_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @pinchtab_instance_app.command("start")
    def pinchtab_instance_start(
        profile_id: Optional[str] = typer.Option(None, "--profile-id"),
        mode: Optional[str] = typer.Option(None, "--mode"),
        port: Optional[int] = typer.Option(None, "--port"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        args: Dict[str, Any] = {}
        if profile_id:
            args["profile_id"] = profile_id
        if mode:
            args["mode"] = mode
        if port is not None:
            args["port"] = int(port)
        env, exit_code = _dispatch_pinchtab(
            tool="browser.pinchtab.instance_start",
            args=args,
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            timeout_sec=timeout_sec,
            json_out=json_out,
            invoke_pinchtab_tool=invoke_pinchtab_tool,
            finalize_cli_call=finalize_cli_call,
        )
        finalize_cli_call(env, exit_code, json_out)


def _register_pinchtab_instance_stop(
    *,
    pinchtab_instance_app: typer.Typer,
    default_policy_path: Path,
    invoke_pinchtab_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @pinchtab_instance_app.command("stop")
    def pinchtab_instance_stop(
        instance_id: str = typer.Option(..., "--instance-id"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        env, exit_code = _dispatch_pinchtab(
            tool="browser.pinchtab.instance_stop",
            args={"instance_id": instance_id},
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            timeout_sec=timeout_sec,
            json_out=json_out,
            invoke_pinchtab_tool=invoke_pinchtab_tool,
            finalize_cli_call=finalize_cli_call,
        )
        finalize_cli_call(env, exit_code, json_out)


def _register_pinchtab_tab_open(
    *,
    pinchtab_tab_app: typer.Typer,
    default_policy_path: Path,
    invoke_pinchtab_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @pinchtab_tab_app.command("open")
    def pinchtab_tab_open(
        instance_id: str = typer.Option(..., "--instance-id"),
        url: str = typer.Option(..., "--url"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        env, exit_code = _dispatch_pinchtab(
            tool="browser.pinchtab.tab_open",
            args={"instance_id": instance_id, "url": url},
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            timeout_sec=timeout_sec,
            json_out=json_out,
            invoke_pinchtab_tool=invoke_pinchtab_tool,
            finalize_cli_call=finalize_cli_call,
        )
        finalize_cli_call(env, exit_code, json_out)


def _register_pinchtab_tab_list(
    *,
    pinchtab_tab_app: typer.Typer,
    default_policy_path: Path,
    invoke_pinchtab_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @pinchtab_tab_app.command("list")
    def pinchtab_tab_list(
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        env, exit_code = _dispatch_pinchtab(
            tool="browser.pinchtab.tabs_list",
            args={},
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            timeout_sec=timeout_sec,
            json_out=json_out,
            invoke_pinchtab_tool=invoke_pinchtab_tool,
            finalize_cli_call=finalize_cli_call,
        )
        finalize_cli_call(env, exit_code, json_out)


def _register_pinchtab_tab_close(
    *,
    pinchtab_tab_app: typer.Typer,
    default_policy_path: Path,
    invoke_pinchtab_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @pinchtab_tab_app.command("close")
    def pinchtab_tab_close(
        tab_id: str = typer.Option(..., "--tab-id"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        env, exit_code = _dispatch_pinchtab(
            tool="browser.pinchtab.tab_close",
            args={"tab_id": tab_id},
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            timeout_sec=timeout_sec,
            json_out=json_out,
            invoke_pinchtab_tool=invoke_pinchtab_tool,
            finalize_cli_call=finalize_cli_call,
        )
        finalize_cli_call(env, exit_code, json_out)


def _register_pinchtab_tab_snapshot(
    *,
    pinchtab_tab_app: typer.Typer,
    default_policy_path: Path,
    invoke_pinchtab_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @pinchtab_tab_app.command("snapshot")
    def pinchtab_tab_snapshot(
        tab_id: str = typer.Option(..., "--tab-id"),
        out: Optional[Path] = typer.Option(None, "--out"),
        summary_limit: int = typer.Option(20, "--summary-limit"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        env, exit_code = _dispatch_pinchtab(
            tool="browser.pinchtab.snapshot",
            args={
                "tab_id": tab_id,
                "summary_limit": summary_limit,
                "include_snapshot": out is not None,
            },
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            timeout_sec=timeout_sec,
            json_out=json_out,
            invoke_pinchtab_tool=invoke_pinchtab_tool,
            finalize_cli_call=finalize_cli_call,
        )
        if out and env.ok:
            snapshot = env.data.get("snapshot")
            if snapshot is not None:
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(
                    json.dumps(snapshot, ensure_ascii=True, indent=2),
                    encoding="utf-8",
                )
                env.data["out_path"] = str(out.resolve(strict=False))
        finalize_cli_call(env, exit_code, json_out)


def _register_pinchtab_tab_text(
    *,
    pinchtab_tab_app: typer.Typer,
    default_policy_path: Path,
    invoke_pinchtab_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @pinchtab_tab_app.command("text")
    def pinchtab_tab_text(
        tab_id: str = typer.Option(..., "--tab-id"),
        mode: str = typer.Option("readability", "--mode"),
        out: Optional[Path] = typer.Option(None, "--out"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        env, exit_code = _dispatch_pinchtab(
            tool="browser.pinchtab.text",
            args={"tab_id": tab_id, "mode": mode, "include_text": out is not None},
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            timeout_sec=timeout_sec,
            json_out=json_out,
            invoke_pinchtab_tool=invoke_pinchtab_tool,
            finalize_cli_call=finalize_cli_call,
        )
        if out and env.ok:
            text_payload = env.data.get("text")
            if isinstance(text_payload, str):
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(text_payload, encoding="utf-8")
                env.data["out_path"] = str(out.resolve(strict=False))
        finalize_cli_call(env, exit_code, json_out)


def _build_pinchtab_action_args(
    *,
    normalized_kind: str,
    tab_id: str,
    ref: Optional[str],
    text: Optional[str],
    key: Optional[str],
    option: Optional[str],
    delta: Optional[int],
) -> Dict[str, Any]:
    """Build the `tab_id`+optional fields arg dict and validate kind-specific
    required fields. Raises `typer.BadParameter` with the original messages."""
    args: Dict[str, Any] = {"tab_id": tab_id}
    if ref is not None:
        args["ref"] = ref
    if text is not None:
        args["text"] = text
    if key is not None:
        args["key"] = key
    if option is not None:
        args["option"] = option
    if delta is not None:
        args["delta"] = delta
    if normalized_kind in {"click", "hover"} and not ref:
        raise typer.BadParameter(f"--ref is required for {normalized_kind}")
    if normalized_kind in {"fill", "type"} and (not ref or text is None):
        raise typer.BadParameter("--ref and --text are required for fill/type")
    if normalized_kind == "press" and not key:
        raise typer.BadParameter("--key is required for press")
    if normalized_kind == "select" and (not ref or not option):
        raise typer.BadParameter("--ref and --option are required for select")
    return args


def _register_pinchtab_tab_action(
    *,
    pinchtab_tab_app: typer.Typer,
    default_policy_path: Path,
    invoke_pinchtab_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @pinchtab_tab_app.command("action")
    def pinchtab_tab_action(
        tab_id: str = typer.Option(..., "--tab-id"),
        kind: str = typer.Option(..., "--kind"),
        ref: Optional[str] = typer.Option(None, "--ref"),
        text: Optional[str] = typer.Option(None, "--text"),
        key: Optional[str] = typer.Option(None, "--key"),
        option: Optional[str] = typer.Option(None, "--option"),
        delta: Optional[int] = typer.Option(None, "--delta"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        normalized_kind = kind.strip().lower()
        tool_name = _PINCHTAB_ACTION_TOOLS.get(normalized_kind)
        if tool_name is None:
            raise typer.BadParameter(f"Unsupported action kind: {kind}")
        args = _build_pinchtab_action_args(
            normalized_kind=normalized_kind,
            tab_id=tab_id,
            ref=ref,
            text=text,
            key=key,
            option=option,
            delta=delta,
        )
        env, exit_code = _dispatch_pinchtab(
            tool=tool_name,
            args=args,
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            timeout_sec=timeout_sec,
            json_out=json_out,
            invoke_pinchtab_tool=invoke_pinchtab_tool,
            finalize_cli_call=finalize_cli_call,
        )
        finalize_cli_call(env, exit_code, json_out)


def _register_pinchtab_tab_screenshot(
    *,
    pinchtab_tab_app: typer.Typer,
    default_policy_path: Path,
    invoke_pinchtab_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @pinchtab_tab_app.command("screenshot")
    def pinchtab_tab_screenshot(
        tab_id: str = typer.Option(..., "--tab-id"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        env, exit_code = _dispatch_pinchtab(
            tool="browser.pinchtab.screenshot",
            args={"tab_id": tab_id},
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            timeout_sec=timeout_sec,
            json_out=json_out,
            invoke_pinchtab_tool=invoke_pinchtab_tool,
            finalize_cli_call=finalize_cli_call,
        )
        finalize_cli_call(env, exit_code, json_out)


def _register_pinchtab_tab_pdf(
    *,
    pinchtab_tab_app: typer.Typer,
    default_policy_path: Path,
    invoke_pinchtab_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
) -> None:
    @pinchtab_tab_app.command("pdf")
    def pinchtab_tab_pdf(
        tab_id: str = typer.Option(..., "--tab-id"),
        policy: Path = typer.Option(default_policy_path, "--policy"),
        workspace: Optional[Path] = typer.Option(None, "--workspace"),
        scope: Optional[str] = typer.Option(None, "--scope"),
        confirm: bool = typer.Option(False, "--confirm"),
        timeout_sec: Optional[int] = typer.Option(None, "--timeout-sec"),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        env, exit_code = _dispatch_pinchtab(
            tool="browser.pinchtab.pdf",
            args={"tab_id": tab_id},
            policy=policy,
            workspace=workspace,
            scope=scope,
            confirm=confirm,
            timeout_sec=timeout_sec,
            json_out=json_out,
            invoke_pinchtab_tool=invoke_pinchtab_tool,
            finalize_cli_call=finalize_cli_call,
        )
        finalize_cli_call(env, exit_code, json_out)


def _register_pinchtab_daemon_status(
    *,
    pinchtab_daemon_app: typer.Typer,
    pinchtab_daemon_config: Callable[..., Any],
    print_obj: Callable[[dict[str, Any], bool], None],
) -> None:
    @pinchtab_daemon_app.command("status")
    def pinchtab_daemon_status_cmd(
        base_url: Optional[str] = typer.Option(
            None,
            "--base-url",
            help="PinchTab base URL.",
        ),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        cfg = pinchtab_daemon_config(base_url=base_url)
        payload = pinchtab_daemon_status(cfg)
        print_obj(payload, json_out=json_out)


def _register_pinchtab_daemon_start(
    *,
    pinchtab_daemon_app: typer.Typer,
    pinchtab_daemon_config: Callable[..., Any],
    print_obj: Callable[[dict[str, Any], bool], None],
) -> None:
    @pinchtab_daemon_app.command("start")
    def pinchtab_daemon_start_cmd(
        base_url: Optional[str] = typer.Option(
            None,
            "--base-url",
            help="PinchTab base URL.",
        ),
        launch_cmd: Optional[str] = typer.Option(
            None,
            "--launch-cmd",
            help="Launch command for PinchTab.",
        ),
        launch_env: Optional[str] = typer.Option(
            None,
            "--launch-env",
            help="Comma-separated KEY=VAL pairs for launch env.",
        ),
        launch_timeout_s: int = typer.Option(
            20,
            "--launch-timeout",
            help="Seconds to wait for PinchTab readiness.",
        ),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        cfg = pinchtab_daemon_config(
            base_url=base_url,
            launch_cmd=launch_cmd,
            launch_timeout_s=launch_timeout_s,
            launch_env=launch_env,
        )
        payload = ensure_pinchtab_daemon(cfg)
        print_obj(payload, json_out=json_out)


def _register_pinchtab_daemon_stop(
    *,
    pinchtab_daemon_app: typer.Typer,
    pinchtab_daemon_config: Callable[..., Any],
    print_obj: Callable[[dict[str, Any], bool], None],
) -> None:
    @pinchtab_daemon_app.command("stop")
    def pinchtab_daemon_stop_cmd(
        base_url: Optional[str] = typer.Option(
            None,
            "--base-url",
            help="PinchTab base URL.",
        ),
        kill: bool = typer.Option(
            False,
            "--kill",
            help="Force kill the PinchTab process.",
        ),
        timeout_s: int = typer.Option(
            3,
            "--timeout",
            help="Seconds to wait for shutdown.",
        ),
        json_out: bool = typer.Option(True, "--json/--no-json"),
    ) -> None:
        cfg = pinchtab_daemon_config(base_url=base_url)
        payload = stop_pinchtab_daemon(cfg, kill=kill, timeout_s=timeout_s)
        print_obj(payload, json_out=json_out)


def register_pinchtab_commands(
    *,
    pinchtab_app: typer.Typer,
    pinchtab_instance_app: typer.Typer,
    pinchtab_tab_app: typer.Typer,
    pinchtab_daemon_app: typer.Typer,
    default_policy_path: Path,
    invoke_pinchtab_tool: Callable[..., tuple[Any, int]],
    finalize_cli_call: Callable[[Any, int, bool], None],
    pinchtab_daemon_config: Callable[..., Any],
    print_obj: Callable[[dict[str, Any], bool], None],
) -> None:
    tool_kwargs = {
        "default_policy_path": default_policy_path,
        "invoke_pinchtab_tool": invoke_pinchtab_tool,
        "finalize_cli_call": finalize_cli_call,
    }
    _register_pinchtab_health(pinchtab_app=pinchtab_app, **tool_kwargs)
    _register_pinchtab_instance_start(
        pinchtab_instance_app=pinchtab_instance_app,
        **tool_kwargs,
    )
    _register_pinchtab_instance_stop(
        pinchtab_instance_app=pinchtab_instance_app,
        **tool_kwargs,
    )
    tab_kwargs = {"pinchtab_tab_app": pinchtab_tab_app, **tool_kwargs}
    _register_pinchtab_tab_open(**tab_kwargs)
    _register_pinchtab_tab_list(**tab_kwargs)
    _register_pinchtab_tab_close(**tab_kwargs)
    _register_pinchtab_tab_snapshot(**tab_kwargs)
    _register_pinchtab_tab_text(**tab_kwargs)
    _register_pinchtab_tab_action(**tab_kwargs)
    _register_pinchtab_tab_screenshot(**tab_kwargs)
    _register_pinchtab_tab_pdf(**tab_kwargs)
    daemon_kwargs = {
        "pinchtab_daemon_app": pinchtab_daemon_app,
        "pinchtab_daemon_config": pinchtab_daemon_config,
        "print_obj": print_obj,
    }
    _register_pinchtab_daemon_status(**daemon_kwargs)
    _register_pinchtab_daemon_start(**daemon_kwargs)
    _register_pinchtab_daemon_stop(**daemon_kwargs)
