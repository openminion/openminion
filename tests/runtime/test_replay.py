from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from typing import Any, get_args

import pytest

from openminion.modules.runtime.replay import (
    DIVERGENCE_KINDS,
    REPLAY_DIVERGENCE_EVENT_TYPE,
    REPLAY_USE_CASES,
    DivergenceKind,
    ReplayBundle,
    ReplayDivergence,
    ReplayPolicy,
    ReplayResult,
    ReplayUseCase,
    default_policy_for,
    record_replay_divergence,
    replay_from_events,
)


def test_replay_use_case_literal_is_exhaustive_four_values() -> None:
    assert set(get_args(ReplayUseCase)) == {
        "debug",
        "regression_test",
        "state_recovery",
        "audit_replay",
    }
    assert len(get_args(ReplayUseCase)) == 4


def test_replay_use_cases_tuple_matches_literal() -> None:
    assert tuple(get_args(ReplayUseCase)) == REPLAY_USE_CASES


def test_divergence_kind_literal_is_exhaustive_five_values() -> None:
    assert set(get_args(DivergenceKind)) == {
        "llm_payload_mismatch",
        "tool_payload_mismatch",
        "state_mismatch",
        "event_order_mismatch",
        "missing_event",
    }
    assert len(get_args(DivergenceKind)) == 5


def test_divergence_kinds_tuple_matches_literal() -> None:
    assert tuple(get_args(DivergenceKind)) == DIVERGENCE_KINDS


def test_default_policy_for_covers_all_use_cases() -> None:
    for use_case in REPLAY_USE_CASES:
        policy = default_policy_for(use_case)
        assert policy.use_case == use_case


def test_default_policy_for_unknown_use_case_raises() -> None:
    with pytest.raises(KeyError):
        default_policy_for("auto_replay")  # type: ignore[arg-type]


def test_record_replay_divergence_rejects_unknown_kind() -> None:
    class _NullLog:
        def emit(
            self,
            event_type: str,
            payload: dict[str, Any],
            *,
            trace_id: str | None = None,
        ) -> str:
            return "id"

    bad = ReplayDivergence(
        event_id="e",
        seam_id="s",
        expected_payload={},
        actual_payload={},
        divergence_kind="auto_detected_mismatch",  # type: ignore[arg-type]
        recorded_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError):
        record_replay_divergence(bad, divergence_log=_NullLog())


def test_replay_bundle_required_fields_match_spec() -> None:
    required = {
        "use_case",
        "initial_state",
        "event_log",
        "policy",
        "bundle_id",
        "recorded_at",
    }
    fields = set(ReplayBundle.__dataclass_fields__.keys())
    assert required.issubset(fields)


def test_replay_policy_fields_match_spec() -> None:
    expected = {
        "use_case",
        "stop_on_divergence",
        "compare_llm_payloads",
        "compare_tool_payloads",
        "deterministic_time",
        "deterministic_random",
    }
    assert set(ReplayPolicy.__dataclass_fields__.keys()) == expected


def test_replay_result_fields_match_spec() -> None:
    expected = {
        "bundle_id",
        "final_state",
        "divergences",
        "events_replayed",
        "events_skipped",
        "completed_at",
    }
    assert set(ReplayResult.__dataclass_fields__.keys()) == expected


def test_replay_divergence_fields_match_spec() -> None:
    expected = {
        "event_id",
        "seam_id",
        "expected_payload",
        "actual_payload",
        "divergence_kind",
        "recorded_at",
    }
    assert set(ReplayDivergence.__dataclass_fields__.keys()) == expected


def test_replay_schemas_are_frozen() -> None:
    policy = default_policy_for("debug")
    bundle = ReplayBundle(
        use_case="debug",
        initial_state={"x": 1},
        event_log=(),
        policy=policy,
        bundle_id="b-1",
        recorded_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    with pytest.raises(FrozenInstanceError):
        bundle.use_case = "regression_test"  # type: ignore[misc]


def _ts(seq: int) -> datetime:
    return datetime(2026, 5, 13, 10, 0, seq, tzinfo=timezone.utc)


def _make_bundle(
    *,
    use_case: ReplayUseCase = "regression_test",
    events: tuple[dict[str, Any], ...] = (),
    expected_event_payloads: dict[str, dict[str, Any]] | None = None,
    expected_state: dict[str, Any] | None = None,
    policy: ReplayPolicy | None = None,
) -> ReplayBundle:
    return ReplayBundle(
        use_case=use_case,
        initial_state={"counter": 0},
        event_log=tuple(events),
        policy=policy or default_policy_for(use_case),
        bundle_id="bundle-1",
        recorded_at=_ts(0),
        expected_state=expected_state,
        expected_event_payloads=expected_event_payloads or {},
    )


def test_replay_from_events_is_pure_same_bundle_same_result() -> None:
    bundle = _make_bundle(
        events=(
            {
                "event_id": "e1",
                "seq": 1,
                "event_type": "tool.completed",
                "payload": {"ok": True},
                "timestamp": _ts(1),
            },
            {
                "event_id": "e2",
                "seq": 2,
                "event_type": "llm.call.completed",
                "payload": {"model": "x"},
                "timestamp": _ts(2),
            },
        ),
    )
    a = replay_from_events(bundle)
    b = replay_from_events(bundle)
    assert a == b
    assert a.bundle_id == "bundle-1"
    assert a.events_replayed == 2
    assert a.events_skipped == 0
    assert a.divergences == ()


def test_replay_from_events_does_not_mutate_bundle() -> None:
    bundle = _make_bundle(
        events=(
            {
                "event_id": "e1",
                "seq": 1,
                "event_type": "tool.completed",
                "payload": {"ok": True},
                "timestamp": _ts(1),
            },
        ),
    )
    before_log = bundle.event_log
    before_state = dict(bundle.initial_state)
    replay_from_events(bundle)
    assert bundle.event_log is before_log
    assert dict(bundle.initial_state) == before_state


def test_llm_payload_mismatch_emitted_on_recorded_baseline_mismatch() -> None:
    bundle = _make_bundle(
        events=(
            {
                "event_id": "e1",
                "seq": 1,
                "event_type": "llm.call.completed",
                "payload": {"model": "actual"},
                "timestamp": _ts(1),
            },
        ),
        expected_event_payloads={"e1": {"model": "expected"}},
    )
    result = replay_from_events(bundle)
    assert len(result.divergences) == 1
    div = result.divergences[0]
    assert div.divergence_kind == "llm_payload_mismatch"
    assert div.seam_id == "llm.call.completed"
    assert div.event_id == "e1"


def test_tool_payload_mismatch_emitted_on_recorded_baseline_mismatch() -> None:
    bundle = _make_bundle(
        events=(
            {
                "event_id": "e1",
                "seq": 1,
                "event_type": "tool.completed",
                "payload": {"status": "ok"},
                "timestamp": _ts(1),
            },
        ),
        expected_event_payloads={"e1": {"status": "error"}},
    )
    result = replay_from_events(bundle)
    assert len(result.divergences) == 1
    assert result.divergences[0].divergence_kind == "tool_payload_mismatch"


def test_event_order_mismatch_emitted_on_descending_seq() -> None:
    bundle = _make_bundle(
        events=(
            {
                "event_id": "e1",
                "seq": 5,
                "event_type": "tool.completed",
                "payload": {},
                "timestamp": _ts(1),
            },
            {
                "event_id": "e2",
                "seq": 3,
                "event_type": "tool.completed",
                "payload": {},
                "timestamp": _ts(2),
            },
        ),
    )
    result = replay_from_events(bundle)
    assert any(d.divergence_kind == "event_order_mismatch" for d in result.divergences)


def test_missing_event_emitted_when_event_tagged_missing() -> None:
    bundle = _make_bundle(
        events=(
            {
                "event_id": "e1",
                "seq": 1,
                "event_type": "tool.completed",
                "payload": {},
                "missing": True,
                "timestamp": _ts(1),
            },
        ),
    )
    result = replay_from_events(bundle)
    assert len(result.divergences) == 1
    assert result.divergences[0].divergence_kind == "missing_event"


def test_state_mismatch_emitted_on_final_state_baseline_mismatch() -> None:
    bundle = _make_bundle(
        events=(
            {
                "event_id": "e1",
                "seq": 1,
                "event_type": "tool.completed",
                "payload": {},
                "timestamp": _ts(1),
            },
        ),
        expected_state={"counter": 99},
    )
    result = replay_from_events(bundle)
    assert any(d.divergence_kind == "state_mismatch" for d in result.divergences)


def test_every_divergence_kind_has_a_regression() -> None:
    bundle = _make_bundle(
        events=(
            {
                "event_id": "e1",
                "seq": 1,
                "event_type": "llm.call.completed",
                "payload": {"v": "a"},
                "timestamp": _ts(1),
            },
            {
                "event_id": "e2",
                "seq": 2,
                "event_type": "tool.completed",
                "payload": {"v": "a"},
                "timestamp": _ts(2),
            },
            {
                "event_id": "e3",
                "seq": 3,
                "event_type": "tool.completed",
                "payload": {},
                "missing": True,
                "timestamp": _ts(3),
            },
            {
                "event_id": "e4",
                "seq": 1,
                "event_type": "tool.completed",
                "payload": {},
                "timestamp": _ts(4),
            },
        ),
        expected_event_payloads={
            "e1": {"v": "b"},
            "e2": {"v": "b"},
        },
        expected_state={"counter": 999},
    )
    bundle = ReplayBundle(
        use_case=bundle.use_case,
        initial_state=bundle.initial_state,
        event_log=bundle.event_log,
        policy=ReplayPolicy(use_case="debug"),
        bundle_id=bundle.bundle_id,
        recorded_at=bundle.recorded_at,
        expected_state=bundle.expected_state,
        expected_event_payloads=bundle.expected_event_payloads,
    )
    result = replay_from_events(bundle)
    kinds = {d.divergence_kind for d in result.divergences}
    assert kinds == set(DIVERGENCE_KINDS)


def test_fifo_event_order_preserved_in_replay_counters() -> None:
    bundle = _make_bundle(
        events=(
            {
                "event_id": "e1",
                "seq": 1,
                "event_type": "tool.completed",
                "payload": {},
                "timestamp": _ts(1),
            },
            {
                "event_id": "e2",
                "seq": 2,
                "event_type": "tool.completed",
                "payload": {},
                "timestamp": _ts(2),
            },
            {
                "event_id": "e3",
                "seq": 3,
                "event_type": "tool.completed",
                "payload": {},
                "timestamp": _ts(3),
            },
        ),
    )
    result = replay_from_events(bundle)
    assert result.events_replayed == 3
    assert result.events_skipped == 0
    assert result.divergences == ()


def test_fifo_divergence_emission_order_matches_event_order() -> None:
    bundle = _make_bundle(
        policy=ReplayPolicy(use_case="debug"),
        events=(
            {
                "event_id": "e1",
                "seq": 1,
                "event_type": "tool.completed",
                "payload": {"v": "a"},
                "timestamp": _ts(1),
            },
            {
                "event_id": "e2",
                "seq": 2,
                "event_type": "tool.completed",
                "payload": {"v": "a"},
                "timestamp": _ts(2),
            },
            {
                "event_id": "e3",
                "seq": 3,
                "event_type": "tool.completed",
                "payload": {"v": "a"},
                "timestamp": _ts(3),
            },
        ),
        expected_event_payloads={
            "e1": {"v": "b"},
            "e2": {"v": "b"},
            "e3": {"v": "b"},
        },
    )
    result = replay_from_events(bundle)
    assert tuple(d.event_id for d in result.divergences) == ("e1", "e2", "e3")


def test_stop_on_divergence_halts_replay_and_skips_remaining_events() -> None:
    bundle = _make_bundle(
        policy=ReplayPolicy(use_case="regression_test", stop_on_divergence=True),
        events=(
            {
                "event_id": "e1",
                "seq": 1,
                "event_type": "tool.completed",
                "payload": {"v": "a"},
                "timestamp": _ts(1),
            },
            {
                "event_id": "e2",
                "seq": 2,
                "event_type": "tool.completed",
                "payload": {"v": "a"},
                "timestamp": _ts(2),
            },
            {
                "event_id": "e3",
                "seq": 3,
                "event_type": "tool.completed",
                "payload": {"v": "a"},
                "timestamp": _ts(3),
            },
        ),
        expected_event_payloads={
            "e1": {"v": "b"},
        },
    )
    result = replay_from_events(bundle)
    assert len(result.divergences) == 1
    assert result.events_replayed == 1
    assert result.events_skipped == 2


def test_compare_llm_payloads_false_suppresses_only_llm_kind() -> None:
    bundle = _make_bundle(
        policy=ReplayPolicy(
            use_case="state_recovery",
            compare_llm_payloads=False,
            compare_tool_payloads=False,
        ),
        events=(
            {
                "event_id": "e1",
                "seq": 1,
                "event_type": "llm.call.completed",
                "payload": {"v": "a"},
                "timestamp": _ts(1),
            },
            {
                "event_id": "e2",
                "seq": 2,
                "event_type": "tool.completed",
                "payload": {},
                "missing": True,
                "timestamp": _ts(2),
            },
        ),
        expected_event_payloads={"e1": {"v": "b"}},
    )
    result = replay_from_events(bundle)
    kinds = {d.divergence_kind for d in result.divergences}
    assert "llm_payload_mismatch" not in kinds
    assert "tool_payload_mismatch" not in kinds
    assert "missing_event" in kinds


def test_state_mismatch_and_order_mismatch_never_suppressed_by_policy() -> None:
    bundle = _make_bundle(
        policy=ReplayPolicy(
            use_case="state_recovery",
            compare_llm_payloads=False,
            compare_tool_payloads=False,
        ),
        events=(
            {
                "event_id": "e1",
                "seq": 5,
                "event_type": "tool.completed",
                "payload": {},
                "timestamp": _ts(1),
            },
            {
                "event_id": "e2",
                "seq": 3,
                "event_type": "tool.completed",
                "payload": {},
                "timestamp": _ts(2),
            },
        ),
        expected_state={"counter": 999},
    )
    result = replay_from_events(bundle)
    kinds = {d.divergence_kind for d in result.divergences}
    assert "event_order_mismatch" in kinds
    assert "state_mismatch" in kinds


def test_replay_use_case_values_are_structural_not_prose() -> None:
    forbidden = {
        "looks_reasonable",
        "model_confident",
        "auto_classified",
        "prose_replay",
    }
    assert set(REPLAY_USE_CASES).isdisjoint(forbidden)


def test_divergence_kind_values_are_structural_not_prose() -> None:
    forbidden = {
        "looks_different",
        "model_thinks_diverged",
        "auto_diverged",
        "prose_mismatch",
    }
    assert set(DIVERGENCE_KINDS).isdisjoint(forbidden)


def test_replay_divergence_field_names_are_structural() -> None:
    fields = set(ReplayDivergence.__dataclass_fields__.keys())
    forbidden = {
        "llm_verdict",
        "model_judgement",
        "auto_detected",
        "prose_summary",
        "looks_diverged",
    }
    assert fields.isdisjoint(forbidden)


def test_replay_seam_has_no_model_or_tool_reinvocation_hooks() -> None:
    import openminion.modules.runtime.replay as mod

    forbidden = {
        "invoke_llm",
        "invoke_tool",
        "call_model",
        "rerun_tool",
        "ask_model",
    }
    public_names = {n for n in dir(mod) if not n.startswith("_")}
    assert public_names.isdisjoint(forbidden)


class _RecordingDivergenceLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any], str | None]] = []
        self._next = 0

    def emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        trace_id: str | None = None,
    ) -> str:
        self._next += 1
        event_id = f"evt-{self._next}"
        self.events.append((event_type, dict(payload), trace_id))
        return event_id


def test_record_replay_divergence_emits_typed_event() -> None:
    log = _RecordingDivergenceLog()
    divergence = ReplayDivergence(
        event_id="e1",
        seam_id="tool.completed",
        expected_payload={"status": "ok"},
        actual_payload={"status": "error"},
        divergence_kind="tool_payload_mismatch",
        recorded_at=datetime(2026, 5, 13, tzinfo=timezone.utc),
    )
    event_id = record_replay_divergence(
        divergence, divergence_log=log, trace_id="trace-99"
    )
    assert event_id == "evt-1"
    assert len(log.events) == 1
    event_type, payload, trace = log.events[0]
    assert event_type == REPLAY_DIVERGENCE_EVENT_TYPE
    assert payload["divergence_kind"] == "tool_payload_mismatch"
    assert payload["event_id"] == "e1"
    assert payload["seam_id"] == "tool.completed"
    assert trace == "trace-99"


def test_divergence_count_parity_with_emitted_events() -> None:
    log = _RecordingDivergenceLog()
    bundle = _make_bundle(
        policy=ReplayPolicy(use_case="debug"),
        events=(
            {
                "event_id": "e1",
                "seq": 1,
                "event_type": "tool.completed",
                "payload": {"v": "a"},
                "timestamp": _ts(1),
            },
            {
                "event_id": "e2",
                "seq": 2,
                "event_type": "tool.completed",
                "payload": {"v": "a"},
                "timestamp": _ts(2),
            },
        ),
        expected_event_payloads={
            "e1": {"v": "b"},
            "e2": {"v": "b"},
        },
    )
    result = replay_from_events(bundle)
    for divergence in result.divergences:
        record_replay_divergence(divergence, divergence_log=log)
    assert len(log.events) == len(result.divergences) == 2


def test_audit_replay_is_event_only_no_snapshot_path() -> None:
    import openminion.modules.runtime.replay as mod

    forbidden = {
        "get_latest_working_state",
        "load_snapshot",
        "snapshot_fallback",
        "read_working_state_snapshot",
    }
    public = {n for n in dir(mod) if not n.startswith("_")}
    assert public.isdisjoint(forbidden)


def test_audit_replay_bundle_runs_without_snapshot_inputs() -> None:
    bundle = _make_bundle(
        use_case="audit_replay",
        events=(
            {
                "event_id": "e1",
                "seq": 1,
                "event_type": "tool.completed",
                "payload": {},
                "timestamp": _ts(1),
            },
        ),
    )
    result = replay_from_events(bundle)
    assert result.events_replayed == 1
    assert result.divergences == ()
