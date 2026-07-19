from __future__ import annotations

import json

import typer

from .api import evidence_list, job_inspect, operator_state, target_inspect, target_list
from .service import local_ops_service

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command("status")
def status() -> None:
    """Print the canonical local ops operator state."""
    typer.echo(json.dumps(operator_state(local_ops_service()), sort_keys=True))


@app.command("target-list")
def target_list_command() -> None:
    typer.echo(json.dumps(target_list(local_ops_service()), sort_keys=True))


@app.command("target-inspect")
def target_inspect_command(target_id: str) -> None:
    typer.echo(
        json.dumps(
            target_inspect(local_ops_service(), target_id), sort_keys=True
        )
    )


@app.command("job-inspect")
def job_inspect_command(job_id: str) -> None:
    typer.echo(json.dumps(job_inspect(local_ops_service(), job_id), sort_keys=True))


@app.command("evidence-list")
def evidence_list_command(target_id: str = "", session_id: str = "") -> None:
    typer.echo(
        json.dumps(
            evidence_list(
                local_ops_service(),
                target_id=target_id,
                session_id=session_id,
            ),
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    app()
