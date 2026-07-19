from __future__ import annotations

import argparse
import sys
import logging
from pathlib import Path
from typing import Any, Callable

from openminion.base.config.env import resolve_environment_config
from openminion.base.errors.adapt import (
    error_dict_from_exception,
    error_dict_from_mapping,
)
from openminion.cli.constants import (
    CLI_TRUTHY_ENV_VALUES,
    OPENMINION_DISABLE_SKILL_ENV,
    OPENMINION_SKILL_LOCAL_FALLBACK_ENV,
)
from openminion.cli.parser.flags import add_json_output_flag
from openminion.cli.presentation.json_output import print_json_payload

_CLI_DEFAULT_SKILL_CONFIG_PATH = "skill.yaml"


_SKILL_IMPORT_FAILED = False
_SKILL_ERROR_MSG = None
_LOG = logging.getLogger(__name__)


def _skill_force_disabled() -> bool:
    env_config = resolve_environment_config()
    return (
        str(env_config.get(OPENMINION_DISABLE_SKILL_ENV, "")).strip().lower()
        in CLI_TRUTHY_ENV_VALUES
    )


try:
    from openminion.modules.skill import Skill
    from openminion.modules.skill.errors import SkillError
except ModuleNotFoundError:
    _SKILL_IMPORT_FAILED = True
    _SKILL_ERROR_MSG = (
        "openminion.modules.skill module not found. "
        "Install with: pip install openminion "
        "or ensure openminion/src is in your PYTHONPATH"
    )
    if (
        str(resolve_environment_config().get(OPENMINION_SKILL_LOCAL_FALLBACK_ENV, ""))
        .strip()
        .lower()
        in CLI_TRUTHY_ENV_VALUES
    ):
        _CANDIDATE_PATHS = [
            Path(__file__).resolve().parents[3],
            Path(__file__).resolve().parents[4] / "src",
        ]
        for _CANDIDATE in _CANDIDATE_PATHS:
            if _CANDIDATE.exists() and str(_CANDIDATE) not in sys.path:
                sys.path.insert(0, str(_CANDIDATE))
                try:
                    from openminion.modules.skill import Skill
                    from openminion.modules.skill.errors import SkillError

                    _SKILL_IMPORT_FAILED = False
                    _SKILL_ERROR_MSG = None
                    break
                except ModuleNotFoundError:
                    sys.path.remove(str(_CANDIDATE))


def _get_skill_error() -> str:
    if _skill_force_disabled():
        return (
            f"openminion.modules.skill disabled via {OPENMINION_DISABLE_SKILL_ENV}. "
            f"Unset {OPENMINION_DISABLE_SKILL_ENV} to enable skill commands."
        )
    return _SKILL_ERROR_MSG or (
        "openminion.modules.skill module not found. "
        "Install with: pip install openminion "
        "or ensure openminion/src is in your PYTHONPATH"
    )


def _check_skill_available() -> bool:
    if _skill_force_disabled():
        return False
    return not _SKILL_IMPORT_FAILED


def _resolve_retrieve_ctl(args: Any) -> Any | None:
    direct = getattr(args, "retrieve_ctl", None)
    if direct is not None:
        return direct
    app = getattr(args, "app", None)
    if app is not None:
        return getattr(app, "retrieve_ctl", None)
    return None


def _resolve_or_create_retrieve_ctl(args: Any) -> tuple[Any | None, bool]:
    retrieve_ctl = _resolve_retrieve_ctl(args)
    if retrieve_ctl is not None:
        return retrieve_ctl, False
    try:
        from openminion.modules.retrieve.runtime.retrieve import RetrieveCtl

        return RetrieveCtl(), True
    except Exception:
        return None, False


def _make_skill_event_callback(
    retrieve_ctl: Any | None, logger: logging.Logger
) -> Callable[[str, dict[str, Any]], None]:
    def _callback(event_type: str, payload: dict[str, Any]) -> None:
        if retrieve_ctl is None:
            return
        if str(event_type).strip().lower() != "skill.ingested":
            return
        try:
            retrieve_ctl.ingest_event(event_type, payload)
        except Exception as exc:
            logger.warning("skill retrieve ingest_event failed: %s", exc)

    return _callback


def _attach_app(args: Any, app: Any | None) -> None:
    if app is not None:
        setattr(args, "app", app)


def _error_payload(
    *,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return error_dict_from_mapping(
        {"code": code, "message": message, "details": details},
        include_details=details is not None,
        include_empty_details=bool(details),
    )


def _error_payload_from_exception(
    exc: BaseException,
    *,
    default_code: str = "INTERNAL_ERROR",
    include_empty_details: bool = False,
) -> dict[str, Any]:
    return error_dict_from_exception(
        exc,
        default_code=default_code,
        include_details=True,
        include_empty_details=include_empty_details,
    )


def _run_skill_ingest(args, app: Any | None = None) -> int:
    _attach_app(args, app)
    if not _check_skill_available():
        print_json_payload(
            {
                "ok": False,
                "error": {
                    **_error_payload(
                        code="SKILL_NOT_AVAILABLE",
                        message=_get_skill_error(),
                    )
                },
            },
            sort_keys=False,
        )
        return 1
    try:
        retrieve_ctl, owns_retrieve_ctl = _resolve_or_create_retrieve_ctl(args)
        ctl = Skill(
            args.config,
            event_callback=_make_skill_event_callback(retrieve_ctl, _LOG),
        )
        try:
            skill_id, version_hash, warnings = ctl.ingest_file(
                path=args.file,
                name=args.name,
                scope=args.scope,
                agent_id=args.agent_id,
                trust=getattr(args, "trust", None),
                promotion_path="operator",
            )
            result = {
                "ok": True,
                "skill_id": skill_id,
                "version_hash": version_hash,
                "warnings": warnings,
            }
            print_json_payload(result, sort_keys=False)
            return 0
        finally:
            ctl.close()
            if owns_retrieve_ctl:
                try:
                    retrieve_ctl.close()
                except Exception:
                    pass
    except SkillError as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, include_empty_details=True),
            },
            sort_keys=False,
        )
        return 1
    except Exception as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, default_code="UNKNOWN"),
            },
            sort_keys=False,
        )
        return 1


def _run_skill_list(args, app: Any | None = None) -> int:
    _attach_app(args, app)
    if not _check_skill_available():
        print_json_payload(
            {
                "ok": False,
                "error": {
                    **_error_payload(
                        code="SKILL_NOT_AVAILABLE",
                        message=_get_skill_error(),
                    )
                },
            },
            sort_keys=False,
        )
        return 1
    try:
        ctl = Skill(args.config)
        try:
            filters: dict[str, Any] = {}
            if args.status:
                filters["status"] = [
                    item.strip() for item in args.status.split(",") if item.strip()
                ]
            if args.scope:
                filters["scope"] = args.scope
            if args.agent_id:
                filters["agent_id"] = args.agent_id
            if args.tag:
                filters["tag"] = args.tag
            if args.tool:
                filters["tool"] = args.tool

            skills = ctl.list_skills(filters)
            if args.json:
                print_json_payload({"ok": True, "skills": skills}, sort_keys=False)
            else:
                if not skills:
                    print("No skills found.")
                else:
                    for skill in skills:
                        print(
                            f"{skill['skill_id']} | {skill.get('name', 'N/A')} | {skill.get('status', 'N/A')} | {skill.get('version_hash', 'N/A')[:12]}..."
                        )
            return 0
        finally:
            ctl.close()
    except SkillError as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, include_empty_details=True),
            },
            sort_keys=False,
        )
        return 1
    except Exception as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, default_code="UNKNOWN"),
            },
            sort_keys=False,
        )
        return 1


def _run_skill_refresh(args, app: Any | None = None) -> int:
    _attach_app(args, app)
    if not _check_skill_available():
        print_json_payload(
            {
                "ok": False,
                "error": {
                    **_error_payload(
                        code="SKILL_NOT_AVAILABLE",
                        message=_get_skill_error(),
                    )
                },
            },
            sort_keys=False,
        )
        return 1
    try:
        retrieve_ctl, owns_retrieve_ctl = _resolve_or_create_retrieve_ctl(args)
        ctl = Skill(
            args.config,
            event_callback=_make_skill_event_callback(retrieve_ctl, _LOG),
        )
        try:
            pkg = ctl.get_skill(args.skill_id, args.version)
            if not pkg.source_artifact_ref:
                print_json_payload(
                    {
                        "ok": False,
                        "error": {
                            **_error_payload(
                                code="NO_SOURCE",
                                message="Skill has no source artifact reference",
                            )
                        },
                    },
                    sort_keys=False,
                )
                return 1

            skill_id, version_hash, warnings = ctl.ingest_artifact(
                source_artifact_ref=pkg.source_artifact_ref,
                name=pkg.name,
                scope=pkg.scope,
                agent_id=pkg.agent_id,
            )
            result = {
                "ok": True,
                "skill_id": skill_id,
                "version_hash": version_hash,
                "warnings": warnings,
            }
            print_json_payload(result, sort_keys=False)
            return 0
        finally:
            ctl.close()
            if owns_retrieve_ctl:
                try:
                    retrieve_ctl.close()
                except Exception:
                    pass
    except SkillError as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, include_empty_details=True),
            },
            sort_keys=False,
        )
        return 1
    except Exception as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, default_code="UNKNOWN"),
            },
            sort_keys=False,
        )
        return 1


def _build_retrieve_text_from_skill(package: Any) -> str:
    lines: list[str] = []
    name = str(getattr(package, "name", "")).strip()
    if name:
        lines.append(f"Skill: {name}")

    summary = str(getattr(package, "summary", "")).strip()
    if summary:
        lines.append(f"Summary: {summary}")

    tools = [
        str(item).strip() for item in getattr(package, "tools", []) if str(item).strip()
    ]
    if tools:
        lines.append("Tools: " + ", ".join(tools))

    sections = getattr(package, "sections", {}) or {}
    if isinstance(sections, dict):
        for key, value in sections.items():
            section_text = str(value or "").strip()
            if not section_text:
                continue
            lines.append(f"{str(key).strip().title()}: {section_text}")

    text = "\n\n".join(item for item in lines if item).strip()
    if text:
        return text
    skill_id = str(getattr(package, "skill_id", "")).strip() or "unknown-skill"
    return f"Skill: {skill_id}"


def _reingest_skill_row(
    ctl: Any, retrieve_ctl: Any, row: dict[str, Any]
) -> dict[str, str] | None:
    skill_id = str(row.get("skill_id", "")).strip()
    version_hash = str(row.get("version_hash", "")).strip() or None
    if not skill_id:
        return None
    try:
        package = ctl.get_skill(skill_id, version_hash)
        source_ref = (
            str(getattr(package, "source_artifact_ref", "")).strip()
            or f"skill:{package.skill_id}@{package.version_hash}"
        )
        payload = {
            "scope": str(getattr(package, "scope", "global") or "global"),
            "title": str(getattr(package, "name", package.skill_id)),
            "tags": list(getattr(package, "tags", []) or []),
            "text": _build_retrieve_text_from_skill(package),
        }
        retrieve_ctl.ingest_skill(
            package.skill_id,
            package.version_hash,
            source_ref,
            meta=payload,
        )
        return None
    except Exception as exc:
        return {
            "skill_id": skill_id,
            "version_hash": str(version_hash or ""),
            "error": str(exc),
        }


def _close_retrieve_ctl_if_owned(retrieve_ctl: Any, owns_retrieve_ctl: bool) -> None:
    if not owns_retrieve_ctl or retrieve_ctl is None:
        return
    try:
        retrieve_ctl.close()
    except Exception:
        pass


def _run_skill_reingest_all(args, app: Any | None = None) -> int:
    _attach_app(args, app)
    if not _check_skill_available():
        print_json_payload(
            {
                "ok": False,
                "error": {
                    **_error_payload(
                        code="SKILL_NOT_AVAILABLE",
                        message=_get_skill_error(),
                    )
                },
            },
            sort_keys=False,
        )
        return 1

    retrieve_ctl, owns_retrieve_ctl = _resolve_or_create_retrieve_ctl(args)
    if retrieve_ctl is None:
        print_json_payload(
            {
                "ok": False,
                "error": {
                    **_error_payload(
                        code="RETRIEVE_NOT_AVAILABLE",
                        message="RetrieveCtl is not available for re-ingest-all",
                    )
                },
            },
            sort_keys=False,
        )
        return 1

    try:
        ctl = Skill(args.config)
        try:
            rows = ctl.list_skills({})
            failures: list[dict[str, str]] = []
            reingested = 0
            for row in rows:
                failure = _reingest_skill_row(ctl, retrieve_ctl, row)
                if failure is None:
                    if str(row.get("skill_id", "")).strip():
                        reingested += 1
                else:
                    failures.append(failure)

            result = {
                "ok": not failures,
                "total": len(rows),
                "reingested": reingested,
                "failed": failures,
            }
            print_json_payload(result, sort_keys=False)
            return 0 if not failures else 1
        finally:
            ctl.close()
            _close_retrieve_ctl_if_owned(retrieve_ctl, owns_retrieve_ctl)
    except SkillError as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, include_empty_details=True),
            },
            sort_keys=False,
        )
        return 1
    except Exception as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, default_code="UNKNOWN"),
            },
            sort_keys=False,
        )
        return 1


def _run_skill_show(args, app: Any | None = None) -> int:
    _attach_app(args, app)
    if not _check_skill_available():
        print_json_payload(
            {
                "ok": False,
                "error": {
                    **_error_payload(
                        code="SKILL_NOT_AVAILABLE",
                        message=_get_skill_error(),
                    )
                },
            },
            sort_keys=False,
        )
        return 1
    try:
        ctl = Skill(args.config)
        try:
            pkg = ctl.get_skill(args.skill_id, args.version)
            print_json_payload(
                {"ok": True, "skill": pkg.to_dict()},
                sort_keys=False,
            )
            return 0
        finally:
            ctl.close()
    except SkillError as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, include_empty_details=True),
            },
            sort_keys=False,
        )
        return 1
    except Exception as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, default_code="UNKNOWN"),
            },
            sort_keys=False,
        )
        return 1


def _run_skill_remove(args, app: Any | None = None) -> int:
    _attach_app(args, app)
    if not _check_skill_available():
        print_json_payload(
            {
                "ok": False,
                "error": {
                    **_error_payload(
                        code="SKILL_NOT_AVAILABLE",
                        message=_get_skill_error(),
                    )
                },
            },
            sort_keys=False,
        )
        return 1
    try:
        ctl = Skill(args.config)
        try:
            counts = ctl.delete_skill(skill_id=args.skill_id, version_hash=args.version)
            print_json_payload({"ok": True, "deleted": counts}, sort_keys=False)
            return 0
        finally:
            ctl.close()
    except SkillError as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, include_empty_details=True),
            },
            sort_keys=False,
        )
        return 1
    except Exception as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc),
            },
            sort_keys=False,
        )
        return 1


def _run_skill_validate(args, app: Any | None = None) -> int:
    _attach_app(args, app)
    if not _check_skill_available():
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload(
                    code="SKILL_NOT_AVAILABLE",
                    message=_get_skill_error(),
                ),
            },
            sort_keys=False,
        )
        return 1
    try:
        from openminion.modules.skill.authoring import (
            build_skill_validation_report,
        )
        from openminion.modules.skill.diagnostics.harness import run_skill_harness

        ctl = Skill(args.config)
        try:
            package = ctl.get_skill(args.skill_id, args.version)
            lint_report = ctl.lint(args.skill_id, args.version)
            harness_report = run_skill_harness(args.project_root)
            harness_result = None
            for item in harness_report.results:
                if args.skill_id and args.skill_id in str(item.skill_root):
                    harness_result = item
                    break
            report = build_skill_validation_report(
                package,
                lint_report=lint_report,
                harness_result=harness_result,
            )
            print_json_payload(
                {"ok": True, "report": report.to_dict()},
                sort_keys=False,
            )
            return 0
        finally:
            ctl.close()
    except SkillError as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, include_empty_details=True),
            },
            sort_keys=False,
        )
        return 1
    except Exception as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, default_code="UNKNOWN"),
            },
            sort_keys=False,
        )
        return 1


def _run_skill_test(args, app: Any | None = None) -> int:
    _attach_app(args, app)
    if not _check_skill_available():
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload(
                    code="SKILL_NOT_AVAILABLE",
                    message=_get_skill_error(),
                ),
            },
            sort_keys=False,
        )
        return 1
    try:
        from openminion.modules.skill.authoring import build_skill_test_report
        from openminion.modules.skill.diagnostics.harness import run_skill_harness

        harness_report = run_skill_harness(args.skill_root)
        report = build_skill_test_report(
            args.skill_root,
            harness_report=harness_report,
            regression_refs=tuple(args.regression_ref or ()),
        )
        print_json_payload(
            {"ok": True, "report": report.to_dict()},
            sort_keys=False,
        )
        return 0 if report.outcome != "failed" else 1
    except Exception as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, default_code="UNKNOWN"),
            },
            sort_keys=False,
        )
        return 1


def _run_skill_debug(args, app: Any | None = None) -> int:
    _attach_app(args, app)
    if not _check_skill_available():
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload(
                    code="SKILL_NOT_AVAILABLE",
                    message=_get_skill_error(),
                ),
            },
            sort_keys=False,
        )
        return 1
    try:
        from openminion.modules.skill.authoring import (
            build_skill_authoring_debug_view,
        )

        ctl = Skill(args.config)
        try:
            package = ctl.get_skill(args.skill_id, args.version)
            debug_payload: dict[str, Any] = {
                "module": "openminion-skill",
                "status": "ok",
                "last_error": None,
            }
            try:
                from openminion.cli.commands.debug.providers.modules import (
                    OpenMinionSkillDebugProvider,
                )

                provider_payload = OpenMinionSkillDebugProvider()._probe()
                debug_payload = {
                    "module": provider_payload.module,
                    "status": getattr(
                        provider_payload.status, "value", str(provider_payload.status)
                    ),
                    "last_error": provider_payload.last_error,
                }
            except Exception:
                pass
            view = build_skill_authoring_debug_view(
                args.skill_id,
                package=package,
                debug_payload=debug_payload,
            )
            print_json_payload({"ok": True, "view": view.to_dict()}, sort_keys=False)
            return 0
        finally:
            ctl.close()
    except SkillError as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, include_empty_details=True),
            },
            sort_keys=False,
        )
        return 1
    except Exception as exc:
        print_json_payload(
            {
                "ok": False,
                "error": _error_payload_from_exception(exc, default_code="UNKNOWN"),
            },
            sort_keys=False,
        )
        return 1


def _add_skill_config_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config", default=_CLI_DEFAULT_SKILL_CONFIG_PATH, help="Path to skill config"
    )


def _register_skill_ingest_subcommand(skill_subcommands) -> None:
    parser = skill_subcommands.add_parser("ingest", help="Ingest a SKILL.md file")
    parser.add_argument("--file", required=True, help="Path to SKILL.md file")
    parser.add_argument("--name", default=None, help="Optional skill name override")
    parser.add_argument(
        "--scope",
        default="global",
        choices=["global", "agent", "project"],
        help=(
            "Skill scope. 'project' is currently stored as a label only and "
            "does not affect runtime catalog visibility."
        ),
    )
    parser.add_argument(
        "--agent-id", default=None, help="Agent ID for agent-scoped skills"
    )
    parser.add_argument(
        "--trust",
        default=None,
        choices=[
            "trusted_local",
            "trusted_remote",
            "untrusted_local",
            "untrusted_remote",
        ],
        help="Optional trust declaration persisted into bundle_metadata.trust",
    )
    _add_skill_config_arg(parser)
    parser.set_defaults(handler=_run_skill_ingest, needs_app=False)


def _register_skill_list_subcommand(skill_subcommands) -> None:
    parser = skill_subcommands.add_parser("list", help="List ingested skills")
    parser.add_argument(
        "--status", default=None, help="Comma-separated statuses to filter"
    )
    parser.add_argument("--scope", default=None, help="Scope filter")
    parser.add_argument("--agent-id", default=None, help="Agent ID filter")
    parser.add_argument("--tag", default=None, help="Tag filter")
    parser.add_argument("--tool", default=None, help="Tool filter")
    _add_skill_config_arg(parser)
    add_json_output_flag(parser)
    parser.set_defaults(handler=_run_skill_list, needs_app=False)


def _register_skill_refresh_subcommand(skill_subcommands) -> None:
    parser = skill_subcommands.add_parser(
        "refresh", help="Refresh/reload a skill from source"
    )
    parser.add_argument("skill_id", help="Skill ID to refresh")
    parser.add_argument("--version", default=None, help="Specific version to refresh")
    _add_skill_config_arg(parser)
    parser.set_defaults(handler=_run_skill_refresh, needs_app=False)


def _register_skill_reingest_all_subcommand(skill_subcommands) -> None:
    parser = skill_subcommands.add_parser(
        "re-ingest-all",
        help="Backfill RetrieveCtl units from already-ingested skills",
    )
    _add_skill_config_arg(parser)
    parser.set_defaults(handler=_run_skill_reingest_all, needs_app=False)


def _register_skill_show_subcommand(skill_subcommands) -> None:
    parser = skill_subcommands.add_parser("show", help="Show skill metadata")
    parser.add_argument("skill_id", help="Skill ID to show")
    parser.add_argument("--version", default=None, help="Specific version to show")
    _add_skill_config_arg(parser)
    parser.set_defaults(handler=_run_skill_show, needs_app=False)


def _register_skill_remove_subcommand(skill_subcommands) -> None:
    parser = skill_subcommands.add_parser("remove", help="Remove an ingested skill")
    parser.add_argument("skill_id", help="Skill ID to remove")
    parser.add_argument("--version", default=None, help="Specific version to remove")
    _add_skill_config_arg(parser)
    parser.set_defaults(handler=_run_skill_remove, needs_app=False)


def _register_skill_validate_subcommand(skill_subcommands) -> None:
    parser = skill_subcommands.add_parser(
        "validate",
        help="Emit typed SkillValidationReport (composes typed lint + harness summary)",
    )
    parser.add_argument("skill_id", help="Skill ID to validate")
    parser.add_argument("--version", default=None, help="Specific version to validate")
    parser.add_argument(
        "--project-root",
        default=".",
        help="Project root for harness skill discovery (defaults to cwd).",
    )
    _add_skill_config_arg(parser)
    parser.set_defaults(handler=_run_skill_validate, needs_app=False)


def _register_skill_test_subcommand(skill_subcommands) -> None:
    parser = skill_subcommands.add_parser(
        "test",
        help="Emit typed SkillTestReport over the skill harness for a skill root",
    )
    parser.add_argument("skill_root", help="Filesystem skill root containing SKILL.md")
    parser.add_argument(
        "--regression-ref",
        action="append",
        default=[],
        help="Regression reference (repeatable).",
    )
    _add_skill_config_arg(parser)
    parser.set_defaults(handler=_run_skill_test, needs_app=False)


def _register_skill_debug_subcommand(skill_subcommands) -> None:
    parser = skill_subcommands.add_parser(
        "debug", help="Emit typed SkillAuthoringDebugView"
    )
    parser.add_argument("skill_id", help="Skill ID to inspect")
    parser.add_argument("--version", default=None, help="Specific version to inspect")
    _add_skill_config_arg(parser)
    parser.set_defaults(handler=_run_skill_debug, needs_app=False)


def register(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    skill = subparsers.add_parser("skill", help="Skill management operations")
    skill_subcommands = skill.add_subparsers(dest="skill_command", required=True)

    _register_skill_ingest_subcommand(skill_subcommands)
    _register_skill_list_subcommand(skill_subcommands)
    _register_skill_refresh_subcommand(skill_subcommands)
    _register_skill_reingest_all_subcommand(skill_subcommands)
    _register_skill_show_subcommand(skill_subcommands)
    _register_skill_remove_subcommand(skill_subcommands)
    _register_skill_validate_subcommand(skill_subcommands)
    _register_skill_test_subcommand(skill_subcommands)
    _register_skill_debug_subcommand(skill_subcommands)
