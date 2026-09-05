from collections.abc import Callable
from typing import Any

from openminion.modules.skill.errors import SkillError
from openminion.modules.skill.interfaces import SkillVerificationEvidence


def skill_verification_evidence_from_args(
    args: Any,
) -> SkillVerificationEvidence | None:
    values = (
        str(getattr(args, "verification_check", "") or "").strip(),
        str(getattr(args, "verification_result", "") or "").strip(),
        str(getattr(args, "verification_evidence_ref", "") or "").strip(),
    )
    if any(values) and not all(values):
        raise SkillError(
            "INVALID_ARGUMENT",
            "verification evidence requires check, result, and evidence ref",
        )
    if values[1] and values[1] != "passed":
        raise SkillError("INVALID_ARGUMENT", "verification result must be passed")
    if not all(values):
        return None
    return SkillVerificationEvidence(
        check=values[0], result="passed", evidence_ref=values[2]
    )


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
    admit.add_argument("--verification-check")
    admit.add_argument("--verification-result", choices=["passed"])
    admit.add_argument("--verification-evidence-ref")
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
