from collections.abc import Callable
from typing import Any


def register_skill_admission_subcommands(
    skill_subcommands: Any,
    *,
    handler: Callable[..., int],
    add_config_arg: Callable[[Any], None],
) -> None:
    admit = skill_subcommands.add_parser("admit", help="Admit a staged skill version")
    admit.add_argument("--skill-id", required=True)
    admit.add_argument("--version-hash", required=True)
    admit.add_argument("--expected-active-version-hash", required=True)
    admit.add_argument(
        "--target-status", required=True, choices=["verified", "blessed"]
    )
    admit.add_argument("--reason", required=True)
    add_config_arg(admit)
    admit.set_defaults(handler=handler, needs_app=False, skill_action="admit")

    rollback = skill_subcommands.add_parser(
        "rollback", help="Reactivate a previously admitted skill version"
    )
    rollback.add_argument("--skill-id", required=True)
    rollback.add_argument("--to-version-hash", required=True)
    rollback.add_argument("--expected-active-version-hash", required=True)
    rollback.add_argument("--reason", required=True)
    add_config_arg(rollback)
    rollback.set_defaults(handler=handler, needs_app=False, skill_action="rollback")
