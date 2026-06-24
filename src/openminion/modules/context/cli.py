import argparse
import json
import sys
from pathlib import Path
from typing import Any

from openminion.base.config.env import resolve_environment_config
from .builder import BuildOptions, ContextPackBuilder
from .constants import (
    DEFAULT_INTEGRATED_IDENTITY_DB_SUBPATH,
    DEFAULT_INTEGRATED_SESSION_DB_SUBPATH,
    DEFAULT_STANDALONE_SESSION_DB_SUBPATH,
)
from .render.renderers import render_anthropic, render_openai
from openminion.base.config import resolve_data_root, resolve_home_root
from openminion.modules.cli_common import (
    add_common_module_root_args,
    apply_home_data_root_env,
    print_json_payload,
)
from openminion.modules.config import is_module_standalone_mode
from openminion.base.constants import (
    OPENMINION_DATA_ROOT_ENV,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contextctl",
        description="openminion-context standalone CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser(
        "build", help="Build ContextPack and optional provider payload"
    )
    add_common_module_root_args(build)
    build.add_argument("--sessctl-db", default=None)
    build.add_argument(
        "--identity-db", default=None, help="Optional identityctl sqlite path"
    )
    build.add_argument(
        "--identity-profiles-path",
        default=None,
        help="Optional identity profile file/directory to load before render",
    )
    build.add_argument("--identity-render-version", default="v1")
    build.add_argument("--identity-bullet-prefix", default="- ")
    build.add_argument("--identity-section-headers", action="store_true")
    build.add_argument(
        "--disable-identity-event-log",
        action="store_true",
        help="Disable sessctl audit events for identity binding and llm.request.started",
    )
    build.add_argument("--session-id", required=True)
    build.add_argument("--agent-id", default="openminion")
    build.add_argument(
        "--purpose",
        default="act",
        choices=[
            "decide",
            "plan",
            "act",
            "reflect",
            "summarize",
            "judge",
            "validate",
            "chat",
        ],
    )
    build.add_argument("--user-input", required=True)
    build.add_argument("--provider-pref", default=None)
    build.add_argument(
        "--constraints-json",
        default="{}",
        help="Optional JSON object for constraints (output_schema/style_overrides/safety_tags/procedure_id)",
    )
    build.add_argument(
        "--format",
        default="contextpack",
        choices=["contextpack", "openai", "anthropic"],
        help="Output format",
    )
    build.add_argument("--model", default="gpt-4.1-mini")

    return parser


def _build_identity_client(args: argparse.Namespace) -> tuple[Any | None, Any | None]:
    if not args.identity_db:
        return None, None

    try:
        from openminion.modules.identity.runtime.service import IdentityCtl
        from openminion.modules.identity.storage import SQLiteIdentityStore
    except ModuleNotFoundError as exc:
        raise ValueError(
            "openminion-identity is not available; add openminion-identity/src to PYTHONPATH or install the package"
        ) from exc

    store = SQLiteIdentityStore(Path(args.identity_db).expanduser().resolve())
    identity_service = IdentityCtl(
        store=store,
        render_version=args.identity_render_version,
        bullet_prefix=args.identity_bullet_prefix,
        section_headers=bool(args.identity_section_headers),
    )
    if args.identity_profiles_path:
        identity_service.load_profiles_from_path(
            Path(args.identity_profiles_path).expanduser().resolve()
        )
    return ContextPackBuilder.identity_client_from_service(
        identity_service
    ), identity_service


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "build":
            home_root = str(getattr(args, "home_root", "") or "").strip()
            data_root = str(getattr(args, "data_root", "") or "").strip()
            apply_home_data_root_env(home_root=home_root, data_root=data_root)
            env_owner = resolve_environment_config()

            sessctl_db_raw = str(getattr(args, "sessctl_db", "") or "").strip()
            if not sessctl_db_raw:
                standalone_mode = is_module_standalone_mode(env_owner)
                if standalone_mode:
                    sessctl_db_raw = str(
                        (Path.home() / DEFAULT_STANDALONE_SESSION_DB_SUBPATH).resolve()
                    )
                else:
                    home_root = resolve_home_root()
                    resolved_data_root = resolve_data_root(
                        home_root,
                        data_root=env_owner.get(OPENMINION_DATA_ROOT_ENV, ""),
                    )
                    sessctl_db_raw = str(
                        (
                            resolved_data_root / DEFAULT_INTEGRATED_SESSION_DB_SUBPATH
                        ).resolve()
                    )
            args.sessctl_db = sessctl_db_raw

            if not getattr(args, "identity_db", None):
                home_root = resolve_home_root()
                resolved_data_root = resolve_data_root(
                    home_root,
                    data_root=env_owner.get(OPENMINION_DATA_ROOT_ENV, ""),
                )
                candidate = (
                    resolved_data_root / DEFAULT_INTEGRATED_IDENTITY_DB_SUBPATH
                ).resolve()
                if candidate.exists():
                    args.identity_db = str(candidate)

            try:
                constraints = json.loads(args.constraints_json)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid constraints JSON: {exc}") from exc
            if not isinstance(constraints, dict):
                raise ValueError("constraints-json must decode to an object")

            identity_client = None
            identity_service = None
            try:
                identity_client, identity_service = _build_identity_client(args)
                builder = ContextPackBuilder(
                    args.sessctl_db,
                    identity_client=identity_client,
                    log_identity_events=not bool(args.disable_identity_event_log),
                )
                pack = builder.build(
                    BuildOptions(
                        session_id=args.session_id,
                        agent_id=args.agent_id,
                        purpose=args.purpose,
                        user_input=args.user_input,
                        provider_pref=args.provider_pref,
                        constraints=constraints,
                    )
                )
            finally:
                if identity_service is not None and hasattr(identity_service, "close"):
                    identity_service.close()
            if args.format == "contextpack":
                print_json_payload(pack)
                return 0
            if args.format == "openai":
                print_json_payload(render_openai(pack, model=args.model))
                return 0
            if args.format == "anthropic":
                print_json_payload(render_anthropic(pack, model=args.model))
                return 0
            parser.error(f"unsupported format: {args.format}")
            return 2

        parser.error(f"unsupported command: {args.command}")
        return 2
    except ValueError as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
