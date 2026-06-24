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
from openminion.modules.identity.config import load_config, load_yaml_file, resolve_path
from openminion.modules.identity.constants import (
    DEFAULT_CONFIG_FILENAME,
    DEFAULT_INTEGRATED_STORAGE_SUBPATH,
)
from openminion.modules.identity.models import AgentProfile
from openminion.modules.identity.runtime.service import IdentityCtl
from openminion.modules.identity.storage import (
    InMemoryIdentityStore,
    SQLiteIdentityStore,
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
def _open_identity(config_path: Path):
    cfg = load_config(config_path, env=dict(os.environ))

    if cfg.storage.backend == "memory":
        store = InMemoryIdentityStore()
    elif cfg.storage.backend == "sqlite":
        store = SQLiteIdentityStore(resolve_path(cfg.storage.sqlite_path))
    else:
        raise ValueError(f"unsupported storage backend: {cfg.storage.backend}")

    identity = IdentityCtl(
        store=store,
        render_version=cfg.rendering.render_version,
        bullet_prefix=cfg.rendering.templates.bullet_prefix,
        section_headers=cfg.rendering.templates.section_headers,
    )
    try:
        yield identity, cfg
    finally:
        identity.close()


def _resolve_storage_db_path(config_path: Path, db: Optional[Path]) -> Path:
    if db:
        return db.expanduser().resolve(strict=False)
    env_owner = resolve_environment_config()
    home_root = resolve_home_root()
    data_root = resolve_data_root(
        home_root, data_root=env_owner.get(OPENMINION_DATA_ROOT_ENV, "")
    )
    if env_owner.get(OPENMINION_DATA_ROOT_ENV, "").strip():
        return (data_root / DEFAULT_INTEGRATED_STORAGE_SUBPATH).resolve()
    try:
        cfg = load_config(config_path, env=dict(os.environ))
    except FileNotFoundError:
        return (data_root / DEFAULT_INTEGRATED_STORAGE_SUBPATH).resolve()
    if cfg.storage.backend == "sqlite":
        return resolve_path(cfg.storage.sqlite_path)
    return (data_root / DEFAULT_INTEGRATED_STORAGE_SUBPATH).resolve()


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
        module_id="identity",
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


@app.command("ls")
def ls_cmd(
    config: Path = typer.Option(
        DEFAULT_CONFIG_PATH, "--config", help="Path to identityctl config"
    ),
) -> None:
    try:
        with _open_identity(config) as (identity, _):
            rows = identity.list_profiles()
            _print_json(
                {"ok": True, "profiles": [row.model_dump(mode="json") for row in rows]}
            )
    except Exception as exc:  # pragma: no cover - CLI guard
        _print_json({"ok": False, "error": {"message": str(exc)}})
        raise typer.Exit(code=1)


@app.command("show")
def show_cmd(
    agent_id: str,
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
) -> None:
    try:
        with _open_identity(config) as (identity, _):
            profile = identity.get_profile(agent_id)
            if profile is None:
                raise ValueError(f"profile not found: {agent_id}")
            _print_json({"ok": True, "profile": profile.model_dump(mode="json")})
    except Exception as exc:  # pragma: no cover - CLI guard
        _print_json({"ok": False, "error": {"message": str(exc)}})
        raise typer.Exit(code=1)


@app.command("load-profiles")
def load_profiles_cmd(
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
    path: Optional[Path] = typer.Option(
        None, "--path", help="Profile file or directory"
    ),
) -> None:
    try:
        with _open_identity(config) as (identity, cfg):
            target = path or Path(cfg.profiles.directory)
            loaded = identity.load_profiles_from_path(target)
            _print_json({"ok": True, "loaded": loaded, "count": len(loaded)})
    except Exception as exc:  # pragma: no cover - CLI guard
        _print_json({"ok": False, "error": {"message": str(exc)}})
        raise typer.Exit(code=1)


@app.command("upsert")
def upsert_cmd(
    file: Path = typer.Option(..., "--file", help="Path to profile yaml/json file"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
) -> None:
    try:
        payload = load_yaml_file(file.expanduser().resolve(strict=False))
        profile = AgentProfile.model_validate(payload)

        with _open_identity(config) as (identity, _):
            version = identity.upsert_profile(profile)
            _print_json(
                {"ok": True, "agent_id": profile.agent_id, "profile_version": version}
            )
    except ValidationError as exc:
        _print_json({"ok": False, "error": {"message": str(exc)}})
        raise typer.Exit(code=1)
    except Exception as exc:  # pragma: no cover - CLI guard
        _print_json({"ok": False, "error": {"message": str(exc)}})
        raise typer.Exit(code=1)


@app.command("validate")
def validate_cmd(
    agent_id: Optional[str] = typer.Option(None, "--agent-id"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
) -> None:
    try:
        with _open_identity(config) as (identity, _):
            if agent_id:
                profile = identity.get_profile(agent_id)
                if profile is None:
                    raise ValueError(f"profile not found: {agent_id}")
                result = identity.validate_profile(profile)
                _print_json(
                    {
                        "ok": result.ok,
                        "errors": result.errors,
                        "warnings": result.warnings,
                    }
                )
                return

            results: dict[str, Any] = {}
            overall_ok = True
            for row in identity.list_profiles():
                profile = identity.get_profile(row.agent_id)
                if profile is None:
                    continue
                result = identity.validate_profile(profile)
                overall_ok = overall_ok and result.ok
                results[row.agent_id] = result.model_dump(mode="json")
            _print_json({"ok": overall_ok, "results": results})
    except Exception as exc:  # pragma: no cover - CLI guard
        _print_json({"ok": False, "error": {"message": str(exc)}})
        raise typer.Exit(code=1)


register_storage_commands(
    storage_app,
    run_storage_command=_run_storage_command,
    default_config_path=DEFAULT_CONFIG_PATH,
)
app.add_typer(storage_app, name="storage")


@app.command("render")
def render_cmd(
    agent_id: str = typer.Option(..., "--agent-id"),
    purpose: str = typer.Option("act", "--purpose"),
    max_tokens: Optional[int] = typer.Option(None, "--max-tokens"),
    max_chars: Optional[int] = typer.Option(None, "--max-chars"),
    provider_pref: Optional[str] = typer.Option(None, "--provider-pref"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
) -> None:
    try:
        with _open_identity(config) as (identity, cfg):
            resolved_purpose = purpose.strip().lower()
            budget = cfg.rendering.default_budgets.get(resolved_purpose)
            max_tokens_value = max_tokens or (
                budget.max_tokens if budget is not None else 180
            )
            snippet = identity.render(
                agent_id=agent_id,
                purpose=purpose,
                max_tokens=max_tokens_value,
                max_chars=max_chars,
                provider_pref=provider_pref,
            )
            _print_json({"ok": True, "snippet": snippet.model_dump(mode="json")})
    except Exception as exc:  # pragma: no cover - CLI guard
        _print_json({"ok": False, "error": {"message": str(exc)}})
        raise typer.Exit(code=1)


@app.command("warm-cache")
def warm_cache_cmd(
    agent_id: str = typer.Option(..., "--agent-id"),
    purpose: list[str] = typer.Option(
        [], "--purpose", help="Repeat for multiple purposes"
    ),
    max_tokens: int = typer.Option(220, "--max-tokens"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
) -> None:
    try:
        with _open_identity(config) as (identity, _):
            count = identity.warm_cache(
                agent_id=agent_id,
                purposes=purpose if purpose else None,
                max_tokens=max_tokens,
            )
            _print_json({"ok": True, "warmed": count})
    except Exception as exc:  # pragma: no cover - CLI guard
        _print_json({"ok": False, "error": {"message": str(exc)}})
        raise typer.Exit(code=1)


@app.command("clear-cache")
def clear_cache_cmd(
    agent_id: Optional[str] = typer.Option(None, "--agent-id"),
    config: Path = typer.Option(DEFAULT_CONFIG_PATH, "--config"),
) -> None:
    try:
        with _open_identity(config) as (identity, _):
            identity.clear_cache(agent_id=agent_id)
            _print_json({"ok": True, "agent_id": agent_id})
    except Exception as exc:  # pragma: no cover - CLI guard
        _print_json({"ok": False, "error": {"message": str(exc)}})
        raise typer.Exit(code=1)


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        app()
        return 0
    app(args=argv, standalone_mode=False)  # pragma: no cover
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
