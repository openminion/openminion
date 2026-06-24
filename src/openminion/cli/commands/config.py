from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import yaml

from openminion.base.config import (
    AgentProfileConfig,
    OpenMinionConfig,
    resolve_config_path,
    save_config,
)
from openminion.cli.config import load_cli_config, resolve_cli_roots
from openminion.cli.presentation.json_output import print_json_payload


def config_init(args) -> int:
    roots = resolve_cli_roots(
        config_path=args.config or None,
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    home_root = roots.home_root
    data_root = roots.data_root
    path = resolve_config_path(args.config, home_root=home_root)
    if path.exists() and not args.force:
        raise RuntimeError(
            f"Config already exists at {path}. Use --force to overwrite."
        )

    config = OpenMinionConfig()
    config.agents = {
        "openminion": AgentProfileConfig(
            name="openminion",
            provider=args.provider,
            default_channel="console",
        )
    }
    config.runtime.demo_mode = args.provider == "echo"
    explicit_storage_path = str(getattr(args, "storage_path", "") or "").strip()
    storage_location = (
        str(getattr(args, "storage_location", "config") or "config").strip().lower()
    )
    if explicit_storage_path:
        config.storage.path = str(Path(explicit_storage_path).expanduser().resolve())
    elif storage_location == "home":
        config.storage.path = str(
            (Path.home() / ".openminion" / "state" / "openminion.db").resolve()
        )
    else:
        config.storage.path = str((data_root / "state" / "openminion.db").resolve())
    save_config(config, args.config)
    print(f"Initialized config at {path} (storage: {config.storage.path})")
    return 0


def config_show(args) -> int:
    config = load_cli_config(args.config)
    print_json_payload(config.to_dict())
    return 0


def _is_sensitive_env_key(name: str) -> bool:
    token = str(name or "").strip().upper()
    if not token:
        return False
    return any(part in token for part in ("KEY", "TOKEN", "SECRET", "PASSWORD"))


def _sanitized_config_payload(
    config: OpenMinionConfig, *, include_secrets: bool = False
) -> dict[str, object]:
    payload = config.to_dict()
    if include_secrets:
        return payload
    comments: list[str] = []
    _strip_secrets_in_place(payload, comments=comments, path=())
    payload["_portable_export_comments"] = comments
    return payload


def _strip_secrets_in_place(
    payload: dict[str, object],
    *,
    comments: list[str],
    path: tuple[str, ...],
) -> None:
    keys = list(payload.keys())
    for key in keys:
        value = payload.get(key)
        current_path = path + (str(key),)
        if isinstance(value, dict):
            _strip_secrets_in_place(value, comments=comments, path=current_path)
            continue
        if not _is_sensitive_env_key(str(key)):
            continue
        env_name = _secret_env_name_for_path(payload, key=str(key), path=current_path)
        comments.append(f"# {'.'.join(current_path)}: <stripped — set {env_name}>")
        payload.pop(key, None)


def _secret_env_name_for_path(
    payload: dict[str, object],
    *,
    key: str,
    path: tuple[str, ...],
) -> str:
    if key.isupper() and "_" in key:
        return key
    sibling_env = str(payload.get(f"{key}_env", "") or "").strip()
    if sibling_env:
        return sibling_env
    if path and path[-1] == "api_key":
        provider_name = path[-2] if len(path) >= 2 else "PROVIDER"
        return f"{provider_name.upper()}_API_KEY"
    return key.upper()


def _portable_export_document(payload: dict[str, object]) -> str:
    export_payload = dict(payload)
    comments = [
        str(item).rstrip()
        for item in export_payload.pop("_portable_export_comments", [])
        if str(item).strip()
    ]
    body = yaml.safe_dump(
        export_payload,
        sort_keys=False,
        default_flow_style=False,
    )
    if not comments:
        return body
    return "\n".join(comments) + "\n" + body


def _load_portable_payload(input_path: Path) -> dict[str, Any]:
    parsed = yaml.safe_load(input_path.read_text(encoding="utf-8")) or {}
    if not isinstance(parsed, dict):
        raise RuntimeError(
            f"Config import file at {input_path} must be a YAML or JSON mapping."
        )
    return parsed


def _deep_merge_dicts(
    base: dict[str, Any],
    incoming: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(base)
    for key, value in incoming.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dicts(current, value)
        else:
            merged[key] = value
    return merged


def config_export(args) -> int:
    roots = resolve_cli_roots(
        config_path=args.config or None,
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    config = load_cli_config(
        args.config,
        home_root=roots.home_root,
        data_root=roots.data_root,
    )
    payload = _sanitized_config_payload(
        config,
        include_secrets=bool(getattr(args, "include_secrets", False)),
    )
    rendered = (
        yaml.safe_dump(payload, sort_keys=False, default_flow_style=False)
        if bool(getattr(args, "include_secrets", False))
        else _portable_export_document(payload)
    )
    output_path_raw = str(
        getattr(args, "out", "") or getattr(args, "output", "") or ""
    ).strip()
    if output_path_raw:
        output_path = Path(output_path_raw).expanduser()
        if not output_path.is_absolute():
            output_path = roots.home_root / output_path
        output_path = output_path.resolve(strict=False)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
        if bool(getattr(args, "include_secrets", False)):
            print(f"Exported config to {output_path} (including secrets).")
        else:
            print(
                f"Exported config to {output_path} without embedded secrets; env vars remain authoritative."
            )
        return 0
    print(rendered, end="")
    return 0


def config_import(args) -> int:
    roots = resolve_cli_roots(
        config_path=args.config or None,
        home_root=getattr(args, "home_root", None),
        data_root=getattr(args, "data_root", None),
    )
    input_value = str(
        getattr(args, "input", "") or getattr(args, "input_flag", "") or ""
    ).strip()
    if not input_value:
        raise RuntimeError("Config import requires a file path.")
    input_path = Path(input_value).expanduser()
    if not input_path.is_absolute():
        input_path = (roots.home_root / input_path).resolve(strict=False)
    if not input_path.exists():
        raise RuntimeError(f"Import file not found at {input_path}.")

    target_path = resolve_config_path(
        getattr(args, "config", None),
        home_root=roots.home_root,
    )

    payload = _load_portable_payload(input_path)
    existing_payload: dict[str, Any] = {}
    if target_path.exists():
        existing_payload = load_cli_config(
            str(target_path),
            home_root=roots.home_root,
            data_root=roots.data_root,
        ).to_dict()
    merged_payload = _deep_merge_dicts(existing_payload, payload)
    config = OpenMinionConfig.from_dict(merged_payload)
    save_config(config, str(target_path), home_root=roots.home_root)
    print(
        f"Imported config from {input_path} to {target_path}. Provider env vars still override stored values."
    )
    return 0


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    config = subparsers.add_parser("config", help="Config operations")
    config_subcommands = config.add_subparsers(dest="config_command")

    config_init_cmd = config_subcommands.add_parser(
        "init", help="Create a default config file"
    )
    config_init_cmd.add_argument(
        "--force", action="store_true", help="Overwrite config if it exists"
    )
    config_init_cmd.add_argument(
        "--provider",
        choices=[
            "echo",
            "openai",
            "anthropic",
            "claude",
            "openrouter",
            "cerebras",
            "groq",
            "ollama",
            "cortensor",
        ],
        default="echo",
        help="Default agent provider for new config",
    )
    config_init_cmd.add_argument(
        "--storage-location",
        choices=["config", "home"],
        default="config",
        help=(
            "Storage path strategy when creating config: "
            "`config` uses <data-root>/state/openminion.db (default: <OPENMINION_HOME>/.openminion), "
            "`home` uses ~/.openminion/state/openminion.db"
        ),
    )
    config_init_cmd.add_argument(
        "--storage-path",
        default=None,
        help="Explicit storage DB path override (takes precedence over --storage-location)",
    )
    config_init_cmd.set_defaults(handler=config_init, needs_app=False)

    config_show_cmd = config_subcommands.add_parser(
        "show", help="Print effective config"
    )
    config_show_cmd.set_defaults(handler=config_show, needs_app=False)

    config_export_cmd = config_subcommands.add_parser(
        "export",
        help="Export config for reuse without secrets by default",
    )
    config_export_cmd.add_argument(
        "--out",
        "--output",
        dest="output",
        default="",
        help="Output file path (default: stdout)",
    )
    config_export_cmd.add_argument(
        "--include-secrets",
        action="store_true",
        help="Include config-stored secrets in the exported file",
    )
    config_export_cmd.set_defaults(handler=config_export, needs_app=False)

    config_import_cmd = config_subcommands.add_parser(
        "import",
        help="Import config from a portable YAML file",
    )
    config_import_cmd.add_argument(
        "input",
        nargs="?",
        default="",
        help="Path to the YAML or JSON config file to import",
    )
    config_import_cmd.add_argument(
        "--input",
        dest="input_flag",
        default="",
        help=argparse.SUPPRESS,
    )
    config_import_cmd.add_argument(
        "--force",
        action="store_true",
        help="Overwrite config if it exists",
    )
    config_import_cmd.set_defaults(handler=config_import, needs_app=False)
