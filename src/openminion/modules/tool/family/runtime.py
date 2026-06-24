from collections.abc import Callable, Sequence
from typing import Any

from .events import emit_family_event


class _StopChain(Exception):
    """Signal from invoke_fn to halt the provider chain immediately."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = result
        super().__init__()


# Re-export so callers only need to import from this module.
StopChain = _StopChain


def run_provider_chain(
    ctx: Any,
    *,
    chain: Sequence[str],
    attempt_event: str,
    attempt_payload_fn: Callable[[str, int, int], dict[str, Any]],
    invoke_fn: Callable[[str, int], Any],
    fallback_result_fn: Callable[
        [list[str], list[tuple[str, Exception]]], dict[str, Any]
    ],
) -> dict[str, Any]:
    """Run a provider/backend chain with shared attempt-event emission."""
    total = len(chain)
    failures: list[tuple[str, Exception]] = []

    for idx, provider_name in enumerate(chain):
        attempt_index = idx + 1
        emit_family_event(
            ctx,
            event=attempt_event,
            payload=attempt_payload_fn(provider_name, attempt_index, total),
        )
        try:
            return invoke_fn(provider_name, attempt_index)
        except _StopChain as exc:
            return exc.result
        except Exception as exc:
            failures.append((provider_name, exc))

    return fallback_result_fn(list(chain), failures)
