from collections.abc import Callable
from typing import Any


def register_skill_authoring_subcommands(
    skill_subcommands: Any,
    *,
    validate_handler: Callable[..., int],
    test_handler: Callable[..., int],
    debug_handler: Callable[..., int],
    add_config_arg: Callable[[Any], None],
) -> None:
    validate = skill_subcommands.add_parser(
        "validate",
        help="Validate bundle conformance and verified-admission readiness",
    )
    validate.add_argument("skill_id", help="Skill ID to validate")
    validate.add_argument(
        "--version", default=None, help="Specific version to validate"
    )
    validate.add_argument(
        "--project-root",
        default=".",
        help="Project root for harness skill discovery (defaults to cwd).",
    )
    add_config_arg(validate)
    validate.set_defaults(handler=validate_handler, needs_app=False)

    test = skill_subcommands.add_parser(
        "test", help="Test filesystem and bundle conformance for a skill root"
    )
    test.add_argument("skill_root", help="Filesystem skill root containing SKILL.md")
    test.add_argument(
        "--regression-ref",
        action="append",
        default=[],
        help="Regression reference (repeatable).",
    )
    test.add_argument(
        "--require-portable",
        action="store_true",
        help="Exit nonzero when Agent Skills front matter is not portable.",
    )
    add_config_arg(test)
    test.set_defaults(handler=test_handler, needs_app=False)

    debug = skill_subcommands.add_parser(
        "debug", help="Display stored skill and runtime debug facts"
    )
    debug.add_argument("skill_id", help="Skill ID to inspect")
    debug.add_argument("--version", default=None, help="Specific version to inspect")
    add_config_arg(debug)
    debug.set_defaults(handler=debug_handler, needs_app=False)
