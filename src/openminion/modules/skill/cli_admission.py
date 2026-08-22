from argparse import Namespace
from typing import Any

from openminion.cli.identity.operator import local_operator_id

from .interfaces import SkillIngestAuthority


def add_admission_subcommands(sub: Any) -> None:
    admit = sub.add_parser("admit", help="Admit a staged skill version")
    admit.add_argument("--skill-id", required=True)
    admit.add_argument("--version-hash", required=True)
    admit.add_argument("--expected-active-version-hash", required=True)
    admit.add_argument(
        "--target-status", required=True, choices=["verified", "blessed"]
    )
    admit.add_argument("--reason", required=True)

    rollback = sub.add_parser(
        "rollback", help="Reactivate a previously admitted skill version"
    )
    rollback.add_argument("--skill-id", required=True)
    rollback.add_argument("--to-version-hash", required=True)
    rollback.add_argument("--expected-active-version-hash", required=True)
    rollback.add_argument("--reason", required=True)


def run_admission_command(ctl: Any, args: Namespace) -> dict[str, Any]:
    authority = SkillIngestAuthority.local_operator(
        surface=f"module_cli.skill.{args.cmd}",
        principal_id=local_operator_id(),
    )
    if args.cmd == "admit":
        expected = str(args.expected_active_version_hash).strip()
        return ctl.admit_skill_version(
            skill_id=args.skill_id,
            version_hash=args.version_hash,
            expected_active_version_hash=None if expected == "none" else expected,
            target_status=args.target_status,
            reason=args.reason,
            authority=authority,
        )
    return ctl.rollback_skill_version(
        skill_id=args.skill_id,
        to_version_hash=args.to_version_hash,
        expected_active_version_hash=args.expected_active_version_hash,
        reason=args.reason,
        authority=authority,
    )
