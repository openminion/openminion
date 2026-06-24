from typing import Any, Mapping

from openminion.base.config.parse import _as_optional_float
from ..schemas import UsageInfo


def _hint_value(cost_hint: Any, key: str) -> float | None:
    if cost_hint is None:
        return None
    if isinstance(cost_hint, Mapping):
        return _as_optional_float(cost_hint.get(key))
    return _as_optional_float(getattr(cost_hint, key, None))


def estimate_usage_cost_usd(*, usage: UsageInfo, cost_hint: Any) -> float | None:
    input_rate = _hint_value(cost_hint, "input_per_1k")
    output_rate = _hint_value(cost_hint, "output_per_1k")
    if input_rate is None and output_rate is None:
        return None

    input_tokens, output_tokens = (
        float(usage.input_tokens or 0),
        float(usage.output_tokens or 0),
    )
    estimated = 0.0
    if input_rate is not None:
        estimated += (input_tokens / 1000.0) * input_rate
    if output_rate is not None:
        estimated += (output_tokens / 1000.0) * output_rate
    return round(estimated, 8)
