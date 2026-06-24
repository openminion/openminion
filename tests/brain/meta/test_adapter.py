from __future__ import annotations

import pytest
from openminion.modules.brain.meta.adapter import CheckpointAdapter
from openminion.modules.brain.meta.evaluator import MetaRulesEngine
from openminion.modules.brain.meta.schemas import MetaMetrics, MetaResult, MetaState


_CHECKPOINT_NAMES = (
    "after_interpret",
    "before_plan",
    "before_act",
    "after_observe",
    "before_respond",
)


def _adapter() -> CheckpointAdapter:
    return CheckpointAdapter(MetaRulesEngine())


def _assert_result(result: MetaResult, checkpoint_name: str) -> None:
    assert isinstance(result, MetaResult)
    assert f"checkpoint:{checkpoint_name}" in result.reasons


@pytest.mark.parametrize("checkpoint_name", _CHECKPOINT_NAMES)
def test_checkpoint_methods_return_tagged_results(checkpoint_name: str) -> None:
    adapter = _adapter()
    result = getattr(adapter, checkpoint_name)(MetaMetrics())
    _assert_result(result, checkpoint_name)


def test_tags_are_distinct() -> None:
    adapter = _adapter()
    metrics = MetaMetrics()
    results = {name: getattr(adapter, name)(metrics) for name in _CHECKPOINT_NAMES}
    for name, result in results.items():
        assert f"checkpoint:{name}" in result.reasons
        for other_name in results:
            if other_name != name:
                assert f"checkpoint:{other_name}" not in result.reasons


def test_original_reasons_preserved() -> None:
    adapter = _adapter()
    result = adapter.before_act(MetaMetrics(user_kill_requested=True))
    assert result.meta_state == MetaState.PANIC
    assert "PANIC_USER_KILL" in result.reasons
    assert "checkpoint:before_act" in result.reasons


def test_before_act_is_deterministic() -> None:
    adapter = _adapter()
    metrics = MetaMetrics(risk_class="high")
    results = [adapter.before_act(metrics) for _ in range(5)]
    first = results[0]
    for result in results[1:]:
        assert result.meta_state == first.meta_state
        assert result.reasons == first.reasons


@pytest.mark.parametrize("checkpoint_name", _CHECKPOINT_NAMES)
def test_all_checkpoints_produce_valid_results(checkpoint_name: str) -> None:
    adapter = _adapter()
    result = getattr(adapter, checkpoint_name)(MetaMetrics(risk_class="medium"))
    MetaResult.model_validate(result.model_dump())
