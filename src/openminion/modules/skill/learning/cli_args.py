from openminion.base.config.parse import split_comma_tokens
from openminion.modules.skill.errors import SkillError

from .replay import ReplayProof


def replay_proof_from_args(
    *,
    proposal_id: str,
    shape_id: str,
    proof_id: str,
    status: str,
    evidence: str,
) -> ReplayProof:
    return ReplayProof(
        proof_id=proof_id,
        proposal_id=proposal_id,
        shape_id=shape_id,
        status=status,
        evidence_refs=split_comma_tokens(evidence),
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
