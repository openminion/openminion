"""Shared Typer registration for module storage maintenance commands."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

import typer

StorageCommandRunner = Callable[..., None]


def _invoke_storage_command(
    run_storage_command: StorageCommandRunner,
    *,
    command: str,
    config: Path | None = None,
    db: Optional[Path],
    root: Optional[Path],
    fallback: Optional[Path],
    snapshot_root: Optional[Path] = None,
    snapshot_path: Optional[Path] = None,
    mode: Optional[str] = None,
    level: Optional[str] = None,
    out: Optional[Path] = None,
    notes: Optional[str] = None,
    storage_input: Optional[Path] = None,
    skip_checksum: bool = False,
) -> None:
    kwargs: dict[str, Any] = {
        "command": command,
        "db": db,
        "root": root,
        "fallback": fallback,
        "snapshot_root": snapshot_root,
        "snapshot_path": snapshot_path,
        "mode": mode,
        "level": level,
        "out": out,
        "notes": notes,
        "storage_input": storage_input,
        "skip_checksum": skip_checksum,
    }
    if config is not None:
        kwargs["config"] = config
    run_storage_command(**kwargs)


def register_storage_commands(
    storage_app: typer.Typer,
    *,
    run_storage_command: StorageCommandRunner,
    default_config_path: Path | None = None,
) -> None:
    """Register the shared storage command set on a module Typer app."""
    if default_config_path is None:
        _register_storage_commands_without_config(
            storage_app,
            run_storage_command=run_storage_command,
        )
        return
    _register_storage_commands_with_config(
        storage_app,
        run_storage_command=run_storage_command,
        default_config_path=default_config_path,
    )


def _register_storage_commands_with_config(
    storage_app: typer.Typer,
    *,
    run_storage_command: StorageCommandRunner,
    default_config_path: Path,
) -> None:
    @storage_app.command("status")
    def storage_status(
        config: Path = typer.Option(default_config_path, "--config"),
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="status",
            config=config,
            db=db,
            root=root,
            fallback=fallback,
        )

    @storage_app.command("plan")
    def storage_plan(
        config: Path = typer.Option(default_config_path, "--config"),
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="plan",
            config=config,
            db=db,
            root=root,
            fallback=fallback,
        )

    @storage_app.command("migrate")
    def storage_migrate(
        config: Path = typer.Option(default_config_path, "--config"),
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="migrate",
            config=config,
            db=db,
            root=root,
            fallback=fallback,
        )

    @storage_app.command("backup")
    def storage_backup(
        config: Path = typer.Option(default_config_path, "--config"),
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
        snapshot_root: Optional[Path] = typer.Option(None, "--snapshot-root"),
        mode: Optional[str] = typer.Option(None, "--mode"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="backup",
            config=config,
            db=db,
            root=root,
            fallback=fallback,
            snapshot_root=snapshot_root,
            mode=mode,
        )

    @storage_app.command("restore")
    def storage_restore(
        config: Path = typer.Option(default_config_path, "--config"),
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
        snapshot_path: Path = typer.Option(..., "--snapshot-path"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="restore",
            config=config,
            db=db,
            root=root,
            fallback=fallback,
            snapshot_path=snapshot_path,
        )

    @storage_app.command("verify")
    def storage_verify(
        config: Path = typer.Option(default_config_path, "--config"),
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
        level: Optional[str] = typer.Option("quick", "--level"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="verify",
            config=config,
            db=db,
            root=root,
            fallback=fallback,
            level=level,
        )

    @storage_app.command("export")
    def storage_export(
        config: Path = typer.Option(default_config_path, "--config"),
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
        out: Path = typer.Option(..., "--out"),
        notes: Optional[str] = typer.Option(None, "--notes"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="export",
            config=config,
            db=db,
            root=root,
            fallback=fallback,
            out=out,
            notes=notes,
        )

    @storage_app.command("import")
    def storage_import(
        config: Path = typer.Option(default_config_path, "--config"),
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
        storage_input: Path = typer.Option(..., "--input"),
        skip_checksum: bool = typer.Option(False, "--skip-checksum"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="import",
            config=config,
            db=db,
            root=root,
            fallback=fallback,
            storage_input=storage_input,
            skip_checksum=skip_checksum,
        )


def _register_storage_commands_without_config(
    storage_app: typer.Typer,
    *,
    run_storage_command: StorageCommandRunner,
) -> None:
    @storage_app.command("status")
    def storage_status(
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="status",
            db=db,
            root=root,
            fallback=fallback,
        )

    @storage_app.command("plan")
    def storage_plan(
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="plan",
            db=db,
            root=root,
            fallback=fallback,
        )

    @storage_app.command("migrate")
    def storage_migrate(
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="migrate",
            db=db,
            root=root,
            fallback=fallback,
        )

    @storage_app.command("backup")
    def storage_backup(
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
        snapshot_root: Optional[Path] = typer.Option(None, "--snapshot-root"),
        mode: Optional[str] = typer.Option(None, "--mode"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="backup",
            db=db,
            root=root,
            fallback=fallback,
            snapshot_root=snapshot_root,
            mode=mode,
        )

    @storage_app.command("restore")
    def storage_restore(
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
        snapshot_path: Path = typer.Option(..., "--snapshot-path"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="restore",
            db=db,
            root=root,
            fallback=fallback,
            snapshot_path=snapshot_path,
        )

    @storage_app.command("verify")
    def storage_verify(
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
        level: Optional[str] = typer.Option("quick", "--level"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="verify",
            db=db,
            root=root,
            fallback=fallback,
            level=level,
        )

    @storage_app.command("export")
    def storage_export(
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
        out: Path = typer.Option(..., "--out"),
        notes: Optional[str] = typer.Option(None, "--notes"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="export",
            db=db,
            root=root,
            fallback=fallback,
            out=out,
            notes=notes,
        )

    @storage_app.command("import")
    def storage_import(
        db: Optional[Path] = typer.Option(None, "--db"),
        root: Optional[Path] = typer.Option(None, "--root"),
        fallback: Optional[Path] = typer.Option(None, "--fallback"),
        storage_input: Path = typer.Option(..., "--input"),
        skip_checksum: bool = typer.Option(False, "--skip-checksum"),
    ) -> None:
        _invoke_storage_command(
            run_storage_command,
            command="import",
            db=db,
            root=root,
            fallback=fallback,
            storage_input=storage_input,
            skip_checksum=skip_checksum,
        )
