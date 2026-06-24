from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

import typer
from pydantic import ValidationError

from openminion.base.config.env import resolve_environment_config
from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.modules.cli_common import (
    DATA_ROOT_OPTION_HELP,
    HOME_ROOT_OPTION_HELP,
    apply_home_data_root_env,
    print_json_payload,
)
from openminion.base.constants import OPENMINION_DATA_ROOT_ENV, OPENMINION_HOME_ENV
from openminion.modules.registry.config import load_config
from openminion.modules.registry.constants import (
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_INTEGRATED_SQLITE_SUBPATH,
)
from openminion.modules.registry.errors import AgentRegError
from openminion.modules.registry.models import ResolveConstraints
from openminion.modules.registry.agents import AgentRegistry
from openminion.modules.registry.storage import (
    InMemoryRegistryStore,
    SQLiteRegistryStore,
)
from openminion.modules.storage.cli_registrar import register_storage_commands
from openminion.modules.storage.module_cli import build_storage_argv, run_storage_argv

app = typer.Typer(add_completion=False, no_args_is_help=True)
storage_app = typer.Typer(add_completion=False, no_args_is_help=True)
DEFAULT_CONFIG_PATH = Path(DEFAULT_CONFIG_FILENAME)


@app.callback()
def main_callback(
    home_root: Optional[Path] = typer.Option(
        None,
        "--home-root",
        help=HOME_ROOT_OPTION_HELP,
    ),
    data_root: Optional[Path] = typer.Option(
        None,
        "--data-root",
        help=DATA_ROOT_OPTION_HELP,
    ),
) -> None:
    apply_home_data_root_env(home_root=home_root, data_root=data_root)


def _print_json(payload: dict[str, Any]) -> None:
    print_json_payload(payload, sort_keys=False, ensure_ascii=True)


@contextmanager
def _open_registry(config_path: Path):
    cfg = load_config(config_path, env=dict(os.environ))

    if cfg.store.backend == "memory":
        store = InMemoryRegistryStore()
    elif cfg.store.backend == "sqlite":
        store = SQLiteRegistryStore(cfg.store.sqlite_path, wal=cfg.store.wal)
    else:
        raise AgentRegError(
            "INVALID_CONFIG", f"Unsupported store backend: {cfg.store.backend}"
        )

    registry = AgentRegistry(
        manifest_path=cfg.manifest_path,
        store=store,
        allow_runtime_override=cfg.allow_runtime_override,
    )
    registry.load()
    try:
        yield registry
    finally:
        registry.close()


def _resolve_storage_db_path(config_path: Path, db: Optional[Path]) -> Path:
    if db:
        return db.expanduser().resolve(strict=False)
    env_owner = resolve_environment_config()
    home_root = resolve_home_root()
    data_root = resolve_data_root(
        home_root, data_root=env_owner.get(OPENMINION_DATA_ROOT_ENV, "")
    )
    if env_owner.get(OPENMINION_DATA_ROOT_ENV, "").strip():
        return (data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve()
    try:
        cfg = load_config(config_path, env=dict(os.environ))
    except FileNotFoundError:
        return (data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve()
    if cfg.store.backend == "sqlite":
        return Path(cfg.store.sqlite_path).expanduser().resolve(strict=False)
    return (data_root / DEFAULT_INTEGRATED_SQLITE_SUBPATH).resolve()


def _run_storage_command(
    *,
    command: str,
    config: Path,
    db: Optional[Path],
    root: Optional[Path],
    fallback: Optional[Path],
    snapshot_root: Optional[Path],
    snapshot_path: Optional[Path],
    mode: Optional[str],
    level: Optional[str],
    out: Optional[Path],
    notes: Optional[str],
    storage_input: Optional[Path],
    skip_checksum: bool,
) -> None:
    env_owner = resolve_environment_config()
    home_root = env_owner.get(OPENMINION_HOME_ENV, "").strip() or None
    data_root = env_owner.get(OPENMINION_DATA_ROOT_ENV, "").strip() or None
    db_path = _resolve_storage_db_path(config, db)
    argv = build_storage_argv(
        module_id="registry",
        db_path=db_path,
        command=command,
        home_root=home_root,
        data_root=data_root,
        root=str(root) if root else None,
        fallback=str(fallback) if fallback else None,
        snapshot_root=str(snapshot_root) if snapshot_root else None,
        snapshot_path=str(snapshot_path) if snapshot_path else None,
        mode=mode,
        level=level,
        out=str(out) if out else None,
        notes=notes,
        input_dir=str(storage_input) if storage_input else None,
        skip_checksum=skip_checksum,
    )
    run_storage_argv(argv)


def _constraints(
    require_tag: list[str],
    avoid_tag: list[str],
    min_quality_tier: Optional[str],
    max_cost_tier: Optional[str],
    prefer_transport: Optional[str],
    require_transport: Optional[str],
    allow_agent: list[str],
) -> ResolveConstraints:
    return ResolveConstraints.model_validate(
        {
            "require_tags": require_tag,
            "avoid_tags": avoid_tag,
            "min_quality_tier": min_quality_tier,
            "max_cost_tier": max_cost_tier,
            "prefer_transport": prefer_transport,
            "require_transport": require_transport,
            "agent_allowlist": allow_agent,
        }
    )


@app.command("ls")
def ls_cmd(
    config: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", help="Path to agentregctl config"
    ),
    tag: list[str] = typer.Option([], "--tag", help="Require tag (repeatable)"),
    source: Optional[str] = typer.Option(
        None, "--source", help="Filter source: manifest|runtime|builtin"
    ),
    name: Optional[str] = typer.Option(
        None, "--name", help="Substring in display name or agent_id"
    ),
) -> None:
    try:
        with _open_registry(config) as registry:
            filters: dict[str, Any] = {}
            if tag:
                filters["tags"] = tag
            if source:
                filters["source"] = source
            if name:
                filters["name"] = name
            agents = registry.list(filters=filters)
            _print_json(
                {
                    "ok": True,
                    "agents": [agent.model_dump(mode="json") for agent in agents],
                }
            )
    except AgentRegError as exc:
        _print_json({"ok": False, "error": exc.to_dict()})
        raise typer.Exit(code=1)
    except ValidationError as exc:
        _print_json(
            {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": str(exc)}}
        )
        raise typer.Exit(code=1)


@app.command("show")
def show_cmd(
    agent_id: str,
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
) -> None:
    try:
        with _open_registry(config) as registry:
            agent = registry.get(agent_id)
            if agent is None:
                raise AgentRegError("NOT_FOUND", f"Agent not found: {agent_id}")
            _print_json({"ok": True, "agent": agent.model_dump(mode="json")})
    except AgentRegError as exc:
        _print_json({"ok": False, "error": exc.to_dict()})
        raise typer.Exit(code=1)
    except ValidationError as exc:
        _print_json(
            {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": str(exc)}}
        )
        raise typer.Exit(code=1)


@app.command("find")
def find_cmd(
    method: Optional[str] = typer.Option(None, "--method", help="Method name"),
    capability: Optional[str] = typer.Option(
        None, "--capability", help="Capability name"
    ),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    tag: list[str] = typer.Option([], "--tag", help="Require tag (repeatable)"),
) -> None:
    if not method and not capability:
        raise typer.BadParameter("Provide --method and/or --capability")

    try:
        with _open_registry(config) as registry:
            filters: dict[str, Any] = {"tags": tag} if tag else {}

            if method:
                rows = registry.find_by_method(method, filters=filters)
            else:
                rows = registry.list(filters=filters)

            if capability:
                rows = [
                    row
                    for row in rows
                    if any(cap.name == capability for cap in row.capabilities)
                ]

            _print_json(
                {"ok": True, "agents": [row.model_dump(mode="json") for row in rows]}
            )
    except AgentRegError as exc:
        _print_json({"ok": False, "error": exc.to_dict()})
        raise typer.Exit(code=1)
    except ValidationError as exc:
        _print_json(
            {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": str(exc)}}
        )
        raise typer.Exit(code=1)


@app.command("resolve")
def resolve_cmd(
    method: Optional[str] = typer.Option(None, "--method"),
    agent: Optional[str] = typer.Option(None, "--agent"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    require_tag: list[str] = typer.Option([], "--require-tag"),
    avoid_tag: list[str] = typer.Option([], "--avoid-tag"),
    min_quality_tier: Optional[str] = typer.Option(None, "--min-quality-tier"),
    max_cost_tier: Optional[str] = typer.Option(None, "--max-cost-tier"),
    prefer_transport: Optional[str] = typer.Option(None, "--prefer-transport"),
    require_transport: Optional[str] = typer.Option(None, "--require-transport"),
    allow_agent: list[str] = typer.Option([], "--allow-agent"),
) -> None:
    if not method and not agent:
        raise typer.BadParameter("Provide --method or --agent")

    try:
        with _open_registry(config) as registry:
            constraints = _constraints(
                require_tag=require_tag,
                avoid_tag=avoid_tag,
                min_quality_tier=min_quality_tier,
                max_cost_tier=max_cost_tier,
                prefer_transport=prefer_transport,
                require_transport=require_transport,
                allow_agent=allow_agent,
            )

            if agent:
                route = registry.resolve_agent(
                    agent, method=method, constraints=constraints
                )
            else:
                assert method is not None
                route = registry.resolve_method(method, constraints=constraints)

            _print_json(
                {
                    "ok": True,
                    "route": None if route is None else route.model_dump(mode="json"),
                    "constraints": constraints.model_dump(mode="json"),
                }
            )
    except AgentRegError as exc:
        _print_json({"ok": False, "error": exc.to_dict()})
        raise typer.Exit(code=1)
    except ValidationError as exc:
        _print_json(
            {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": str(exc)}}
        )
        raise typer.Exit(code=1)


@app.command("status")
def status_cmd(
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    agent: Optional[str] = typer.Option(None, "--agent"),
) -> None:
    try:
        with _open_registry(config) as registry:
            if agent:
                status = registry.get_status(agent)
                _print_json({"ok": True, "status": status.model_dump(mode="json")})
                return

            agents = registry.list()
            rows = [
                registry.get_status(row.agent_id).model_dump(mode="json")
                for row in agents
            ]
            _print_json({"ok": True, "status": rows})
    except AgentRegError as exc:
        _print_json({"ok": False, "error": exc.to_dict()})
        raise typer.Exit(code=1)
    except ValidationError as exc:
        _print_json(
            {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": str(exc)}}
        )
        raise typer.Exit(code=1)


@app.command("heartbeat")
def heartbeat_cmd(
    agent_id: str,
    state: str = typer.Option("healthy", "--state"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
) -> None:
    try:
        with _open_registry(config) as registry:
            registry.heartbeat(agent_id, {"state": state})
            status = registry.get_status(agent_id)
            _print_json({"ok": True, "status": status.model_dump(mode="json")})
    except AgentRegError as exc:
        _print_json({"ok": False, "error": exc.to_dict()})
        raise typer.Exit(code=1)
    except ValidationError as exc:
        _print_json(
            {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": str(exc)}}
        )
        raise typer.Exit(code=1)


@app.command("reload")
def reload_cmd(
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
) -> None:
    try:
        with _open_registry(config) as registry:
            registry.reload()
            agents = registry.list()
            _print_json({"ok": True, "count": len(agents)})
    except AgentRegError as exc:
        _print_json({"ok": False, "error": exc.to_dict()})
        raise typer.Exit(code=1)
    except ValidationError as exc:
        _print_json(
            {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": str(exc)}}
        )
        raise typer.Exit(code=1)


@app.command("explain")
def explain_cmd(
    method: str = typer.Option(..., "--method"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    require_tag: list[str] = typer.Option([], "--require-tag"),
    avoid_tag: list[str] = typer.Option([], "--avoid-tag"),
    min_quality_tier: Optional[str] = typer.Option(None, "--min-quality-tier"),
    max_cost_tier: Optional[str] = typer.Option(None, "--max-cost-tier"),
    prefer_transport: Optional[str] = typer.Option(None, "--prefer-transport"),
    require_transport: Optional[str] = typer.Option(None, "--require-transport"),
    allow_agent: list[str] = typer.Option([], "--allow-agent"),
) -> None:
    try:
        with _open_registry(config) as registry:
            constraints = _constraints(
                require_tag=require_tag,
                avoid_tag=avoid_tag,
                min_quality_tier=min_quality_tier,
                max_cost_tier=max_cost_tier,
                prefer_transport=prefer_transport,
                require_transport=require_transport,
                allow_agent=allow_agent,
            )
            report = registry.explain_resolution(method, constraints=constraints)
            _print_json({"ok": True, "report": report})
    except AgentRegError as exc:
        _print_json({"ok": False, "error": exc.to_dict()})
        raise typer.Exit(code=1)
    except ValidationError as exc:
        _print_json(
            {"ok": False, "error": {"code": "INVALID_ARGUMENT", "message": str(exc)}}
        )
        raise typer.Exit(code=1)


register_storage_commands(
    storage_app,
    run_storage_command=_run_storage_command,
    default_config_path=DEFAULT_CONFIG_PATH,
)
app.add_typer(storage_app, name="storage")


def _exit_code(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    return 1


def main(argv: list[str] | None = None) -> int:
    try:
        app(args=argv, standalone_mode=False)
        return 0
    except typer.Exit as exc:
        return _exit_code(exc.exit_code)
    except SystemExit as exc:  # pragma: no cover - defensive compatibility
        return _exit_code(exc.code)


if __name__ == "__main__":
    raise SystemExit(main())
