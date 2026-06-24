from __future__ import annotations

import json
import pathlib

import pytest

from openminion.modules.brain.meta.evaluator import MetaRulesEngine
from openminion.modules.brain.meta.schemas import MetaMetrics

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "meta_events"
_FIXTURE_NAMES = ("normal", "cautious", "high_assurance", "recovery", "panic")


def _load_fixture(name: str) -> dict:
    path = _FIXTURES_DIR / f"{name}.json"
    with path.open() as f:
        return json.load(f)


def _assert_fixture(fixture_name: str) -> None:
    data = _load_fixture(fixture_name)
    metrics = MetaMetrics(**data["meta_metrics"])
    result = MetaRulesEngine().evaluate(metrics)

    expected_state = data["expected_meta_state"]
    assert result.meta_state.value == expected_state, (
        f"[{fixture_name}] state mismatch: got {result.meta_state.value!r}, "
        f"want {expected_state!r}"
    )

    directive_data = result.directive.model_dump()
    for field, expected_value in data["expected_directive"].items():
        actual_value = directive_data.get(field)
        assert actual_value == expected_value, (
            f"[{fixture_name}] directive.{field}: got {actual_value!r}, "
            f"want {expected_value!r}"
        )


@pytest.mark.parametrize("fixture_name", _FIXTURE_NAMES)
def test_fixture_expected_payloads(fixture_name: str) -> None:
    _assert_fixture(fixture_name)


def test_fixtures_are_deterministic() -> None:
    engine = MetaRulesEngine()
    for fixture_name in _FIXTURE_NAMES:
        data = _load_fixture(fixture_name)
        metrics = MetaMetrics(**data["meta_metrics"])
        results = [engine.evaluate(metrics) for _ in range(3)]
        first = results[0]
        for result in results[1:]:
            assert result.meta_state == first.meta_state, (
                f"[{fixture_name}] non-deterministic meta_state"
            )
            assert result.reasons == first.reasons, (
                f"[{fixture_name}] non-deterministic reasons"
            )


@pytest.mark.parametrize("fixture_name", _FIXTURE_NAMES)
def test_fixture_meta_metrics_payload_is_schema_valid(fixture_name: str) -> None:
    data = _load_fixture(fixture_name)
    MetaMetrics(**data["meta_metrics"])
