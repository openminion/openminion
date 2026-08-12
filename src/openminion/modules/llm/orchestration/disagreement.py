from .schemas import CandidateResponse, UsageTotal


def aggregate_usage(candidates: list[CandidateResponse]) -> UsageTotal:
    costs = [
        item.usage.cost_estimate
        for item in candidates
        if item.usage.cost_estimate is not None
    ]
    return UsageTotal(
        latency_ms_total=sum(item.usage.latency_ms for item in candidates),
        input_tokens=sum(item.usage.input_tokens for item in candidates),
        output_tokens=sum(item.usage.output_tokens for item in candidates),
        cost_estimate=sum(costs) if costs else None,
    )
