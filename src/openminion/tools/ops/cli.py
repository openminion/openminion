from __future__ import annotations

import json

import typer

from openminion.base.config import ConfigManager
from openminion.modules.runtime.credentials import (
    InMemoryCredentialAuditLog,
    resolve_credential_env_value,
)

from .api import evidence_list, job_inspect, operator_state, target_inspect, target_list
from .service import OpsService, configured_ops_service

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _configured_service(config_path: str | None) -> OpsService:
    manager = ConfigManager.load(config_path)
    audit = InMemoryCredentialAuditLog()
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
    config: str | None = typer.Option(None, "--config"),
) -> None:
    typer.echo(
        json.dumps(
            target_inspect(_configured_service(config), target_id), sort_keys=True
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
    session_id: str = typer.Option("", "--session"),
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


@app.command("command-run")
def command_run(
    plan_id: str,
    plan_hash: str,
    confirm: bool = typer.Option(False, "--confirm"),
    config: str | None = typer.Option(None, "--config"),
) -> None:
    if not confirm:
        raise typer.BadParameter("--confirm is required for an immutable command plan")
    job = _configured_service(config).run_plan(
        plan_id=plan_id,
        plan_hash=plan_hash,
        approval_id=f"opsctl-{plan_hash[:16]}",
    )
    typer.echo(job.model_dump_json())


if __name__ == "__main__":
    app()
