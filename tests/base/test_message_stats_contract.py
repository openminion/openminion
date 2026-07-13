from openminion.base.types import Message, MessageStats
from openminion.modules.telemetry.usage import RunStats


class _NonConformingStats:
    @property
    def has_any_data(self) -> bool:
        return True


def test_run_stats_structurally_conforms_to_message_stats() -> None:
    stats = RunStats(input_tokens=3, output_tokens=2)
    message = Message(channel="console", target="cli", body="done", stats=stats)

    assert isinstance(stats, MessageStats)
    assert message.stats is stats
    assert message.stats.has_any_data
    assert message.stats.as_payload()["input_tokens"] == 3


def test_message_stats_rejects_non_conforming_shape() -> None:
    assert not isinstance(_NonConformingStats(), MessageStats)
