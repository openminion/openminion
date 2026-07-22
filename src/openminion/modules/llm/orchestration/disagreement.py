from difflib import SequenceMatcher
from ..constants import LLM_CANDIDATE_STATUS_SUCCESS
from .coercion import _normalize_text
from .schemas import (
    CandidateResponse,
    DisagreementCluster,
    DisagreementConfig,
    DisagreementReport,
    UsageTotal,
)


def compute_disagreement(
    candidates: list[CandidateResponse],
    config: DisagreementConfig | None,
) -> DisagreementReport | None:
    if config is None or not config.enabled:
        return None
    successful = [
        item
        for item in candidates
        if item.status == LLM_CANDIDATE_STATUS_SUCCESS and item.text
    ]
    if len(successful) < 2:
        return None
    threshold = max(0.0, min(1.0, float(config.threshold)))
    norm_texts = {
        item.candidate_id: _normalize_text(item.text or "") for item in successful
    }

    clusters: list[list[str]] = []
    for candidate in successful:
        added = False
        for cluster in clusters:
            anchor = norm_texts[cluster[0]]
            ratio = SequenceMatcher(
                None, anchor, norm_texts[candidate.candidate_id]
            ).ratio()
            if ratio >= threshold:
                cluster.append(candidate.candidate_id)
                added = True
                break
        if not added:
            clusters.append([candidate.candidate_id])

    if len(clusters) <= 1:
        return None

    rendered_clusters = []
    for cluster in clusters:
        first_id = cluster[0]
        excerpt = (norm_texts.get(first_id, "") or "")[
            : max(1, int(config.max_excerpt_chars))
        ]
        rendered_clusters.append(
            DisagreementCluster(candidate_ids=list(cluster), excerpt=excerpt)
        )

    return DisagreementReport(
        summary=f"Detected disagreement across {len(clusters)} clusters",
        clusters=rendered_clusters,
        json_diffs=None,
        risk_flags=["disagreement_detected"],
    )


def aggregate_usage(candidates: list[CandidateResponse]) -> UsageTotal:
    latency_total = 0
    input_tokens = 0
    output_tokens = 0
    cost_total = 0.0
    has_cost = False
    for item in candidates:
        latency_total += int(item.usage.latency_ms)
        input_tokens += int(item.usage.input_tokens)
        output_tokens += int(item.usage.output_tokens)
        if item.usage.cost_estimate is not None:
            has_cost = True
            cost_total += float(item.usage.cost_estimate)
    return UsageTotal(
        latency_ms_total=latency_total,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_estimate=cost_total if has_cost else None,
    )
