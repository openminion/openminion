from __future__ import annotations

import json
from collections.abc import Callable

import typer

from openminion.base.config import ConfigManager
from openminion.modules.policy import PolicyCtl
from openminion.modules.policy.models import policy_config_from_action_policy
from openminion.modules.runtime.credentials import (
    InMemoryCredentialAuditLog,
    resolve_credential_env_value,
)
from openminion.modules.tool.contracts.schemas import TOOL_ERROR_CONFIRM_REQUIRED
from openminion.modules.tool.errors import ToolRuntimeError

from .api import evidence_list, job_inspect, operator_state, target_inspect, target_list
from .service import OpsService, configured_ops_service

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _configured_service(config_path: str | None) -> OpsService:
    manager = ConfigManager.load(config_path)
    audit = InMemoryCredentialAuditLog()
    action_policy = PolicyCtl.with_sqlite(
        manager.data_root / "policy" / "policy.db",
        config=policy_config_from_action_policy(manager.base_config.action_policy),
    )
    return configured_ops_service(
        manager.base_config.runtime.ops,
        data_root=manager.data_root,
        credential_reader=lambda ref: resolve_credential_env_value(
            ref,
            caller_agent_id="",
            caller_profile_id="ops",
            access_site="tools.ops.cli",
            audit_log=audit,
            env=manager.env,
        ),
        action_policy=action_policy,
    )


@app.command("status")
def status(config: str | None = typer.Option(None, "--config")) -> None:
    """Print the configured redacted operations state."""
    typer.echo(json.dumps(operator_state(_configured_service(config)), sort_keys=True))


@app.command("target-list")
def target_list_command(config: str | None = typer.Option(None, "--config")) -> None:
    typer.echo(json.dumps(target_list(_configured_service(config)), sort_keys=True))


@app.command("target-inspect")
def target_inspect_command(
    target_id: str,
    probe: bool = typer.Option(False, "--probe"),
    config: str | None = typer.Option(None, "--config"),
) -> None:
    typer.echo(
        json.dumps(
            target_inspect(_configured_service(config), target_id, probe=probe),
            sort_keys=True,
        )
    )


@app.command("job-inspect")
def job_inspect_command(
    job_id: str,
    config: str | None = typer.Option(None, "--config"),
) -> None:
    typer.echo(
        json.dumps(job_inspect(_configured_service(config), job_id), sort_keys=True)
    )


@app.command("job-mark-interrupted")
def job_mark_interrupted_command(
    job_id: str,
    reason: str = typer.Option(..., "--reason"),
    confirm: bool = typer.Option(False, "--confirm"),
    config: str | None = typer.Option(None, "--config"),
) -> None:
    if not confirm:
        raise typer.BadParameter("--confirm is required to mark a job interrupted")
    job = _configured_service(config).mark_job_interrupted(
        job_id,
        reason=reason,
        actor="local",
    )
    typer.echo(job.model_dump_json())


@app.command("evidence-list")
def evidence_list_command(
    target_id: str = "",
    session_id: str = "",
    config: str | None = typer.Option(None, "--config"),
) -> None:
    typer.echo(
        json.dumps(
            evidence_list(
                _configured_service(config),
                target_id=target_id,
                session_id=session_id,
            ),
            sort_keys=True,
        )
    )


@app.command("command-plan")
def command_plan(
    target_id: str,
    argv: list[str] = typer.Argument(...),
    cwd: str = typer.Option("", "--cwd"),
    timeout_seconds: float = typer.Option(30.0, "--timeout"),
    session_id: str = typer.Option(..., "--session"),
    config: str | None = typer.Option(None, "--config"),
) -> None:
    plan = _configured_service(config).plan_command(
        target_id=target_id,
        argv=tuple(argv),
        cwd=cwd,
        timeout_seconds=timeout_seconds,
        session_id=session_id,
    )
    typer.echo(plan.model_dump_json())


@app.command("file-read")
def file_read(
    target_id: str,
    path: str,
    max_bytes: int = typer.Option(4096, "--max-bytes", min=1, max=131072),
    timeout_seconds: float = typer.Option(30.0, "--timeout", min=0.1, max=300),
    session_id: str = typer.Option("", "--session"),
    config: str | None = typer.Option(None, "--config"),
) -> None:
    evidence = _configured_service(config).read_file(
        target_id=target_id,
        path=path,
        max_bytes=max_bytes,
        timeout_seconds=timeout_seconds,
        session_id=session_id,
    )
    typer.echo(evidence.model_dump_json())


@app.command("command-run")
def command_run(
    plan_id: str,
    plan_hash: str,
    session_id: str = typer.Option(..., "--session"),
    confirm: bool = typer.Option(False, "--confirm"),
    stream: bool = typer.Option(False, "--stream"),
    config: str | None = typer.Option(None, "--config"),
) -> None:
    if not confirm:
        raise typer.BadParameter("--confirm is required for an immutable command plan")
    service = _configured_service(config)
    output_sink: Callable[[str, str], None] | None
    if stream:

        def output_sink(_stream: str, chunk: str) -> None:
            typer.echo(chunk, err=True, nl=False)

    else:
        output_sink = None
    try:
        job = service.run_plan(
            plan_id=plan_id,
            plan_hash=plan_hash,
            session_id=session_id,
            output_sink=output_sink,
        )
    except ToolRuntimeError as exc:
        if exc.code != TOOL_ERROR_CONFIRM_REQUIRED:
            raise
        approval_id = str(exc.details.get("approval_id", ""))
        action_policy = service.action_policy
        assert action_policy is not None
        action_policy.resolve_confirmation(approval_id, "allow_once")
        job = service.run_plan(
            plan_id=plan_id,
            plan_hash=plan_hash,
            session_id=session_id,
            output_sink=output_sink,
        )
    typer.echo(job.model_dump_json())


if __name__ == "__main__":
    app()
