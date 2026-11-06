"""Learning command arguments and operator criterion parsing."""

from typing import Any

from openminion.base.config.parse import split_comma_tokens
from openminion.modules.skill.errors import SkillError

from .replay import ReplayProof


def replay_proof_from_args(
    *,
    proposal_id: str,
    shape_id: str,
    proof_id: str,
    candidate_hash: str,
    evaluator_id: str,
    result_ref: str,
    status: str,
    evidence: str,
) -> ReplayProof:
    evidence_refs = split_comma_tokens(evidence)
    if result_ref not in evidence_refs:
        evidence_refs.append(result_ref)
    return ReplayProof(
        proof_id=proof_id,
        proposal_id=proposal_id,
        shape_id=shape_id,
        candidate_hash=candidate_hash,
        evaluator_id=evaluator_id,
        result_ref=result_ref,
        status=status,
        evidence_refs=evidence_refs,
    )


def parse_criterion_args(raw_values: list[str]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in raw_values or []:
        text = str(raw or "").strip()
        if not text:
            continue
        parts = text.split(":", 2)
        if len(parts) != 3:
            raise SkillError(
                "INVALID_ARGUMENT",
                "--criterion must be 'criterion_id:status:comment'",
                {"criterion": text},
            )
        criterion_id, status, comment = (
            parts[0].strip(),
            parts[1].strip(),
            parts[2].strip(),
        )
        if not criterion_id or not comment:
            raise SkillError(
                "INVALID_ARGUMENT",
                "--criterion id and comment must be non-empty",
                {"criterion": text},
            )
        if status not in {"accepted", "rejected", "deferred"}:
            raise SkillError(
                "INVALID_ARGUMENT",
                "--criterion status must be one of accepted|rejected|deferred",
                {"criterion": text},
            )
        out.append({"criterion_id": criterion_id, "status": status, "comment": comment})
    return out


def add_learning_subcommands(sub: Any) -> None:
    observation_list = sub.add_parser(
        "learning-observation-list",
        help="List retained runtime workflow observations for one agent.",
    )
    observation_list.add_argument("--agent-id", required=True)

    shape_list = sub.add_parser(
        "learning-shape-list",
        help="List derived workflow shapes for one agent.",
    )
    shape_list.add_argument("--agent-id", required=True)

    learning_scan = sub.add_parser(
        "learning-scan",
        help="Mine workflow shapes from a JSON list of evidence bundles.",
    )
    learning_scan.add_argument("--bundle-json", required=True)

    learning_inspect = sub.add_parser(
        "learning-inspect",
        help="Inspect one workflow-shape JSON file.",
    )
    learning_inspect.add_argument("--shape-json", required=True)

    learning_save = sub.add_parser(
        "learning-save-workflow",
        help="Emit an explicit save-workflow signal for one shape.",
    )
    learning_save.add_argument("--shape-json", required=True)
    learning_save.add_argument("--actor-id", required=True)
    learning_save.add_argument("--source-run-ref", default="")

    learning_propose = sub.add_parser(
        "learning-propose",
        help="Stage a workflow shape through the existing proposal queue.",
    )
    proposal_source = learning_propose.add_mutually_exclusive_group(required=True)
    proposal_source.add_argument("--shape-json", help="Legacy metadata-only shape.")
    proposal_source.add_argument("--shape-id", help="Stored shape for --agent-id.")
    learning_propose.add_argument("--agent-id")
    learning_propose.add_argument(
        "--procedure-file", help="Authored SkillProposalDraft JSON."
    )

    learning_replay = sub.add_parser(
        "learning-replay-proof",
        help="Retain an evaluator-owned replay result artifact.",
    )
    learning_replay.add_argument("--proposal-id", required=True)
    learning_replay.add_argument("--measurement-manifest-ref")
    learning_replay.add_argument("--result-ref", required=True)

    learning_apply = sub.add_parser(
        "learning-apply-proved",
        help="Persist a proved proposal as a pending-admission skill draft.",
        description="Persist a proved proposal as a pending-admission skill draft.",
    )
    learning_apply.add_argument("--proposal-id", required=True)

    learning_trust = sub.add_parser(
        "learning-trust-status",
        help="Show initial execution-trust status for a learned skill.",
    )
    learning_trust.add_argument("--skill-id", required=True)
    learning_trust.add_argument("--shape-id", required=True)
