from __future__ import annotations

from enum import StrEnum

import pytest

from openminion.modules.brain.runtime.lifecycle import (
    CanonicalLifecyclePhase,
    LifecyclePhaseProjection,
    ResumeTransition,
    UnifiedLifecycleProjection,
    build_unified_projection,
    project_mission_status,
    project_plan_step_status,
    project_resume_channel,
    project_task_status,
    project_working_status,
    resume_transition_for,
)


@pytest.mark.parametrize(
    "value,expected_phase",
    [
        ("PENDING", "pending"),
        ("ACTIVE", "active"),
        ("WAITING", "awaiting_async"),
        ("DONE", "completed"),
        ("CANCELED", "cancelled"),
    ],
)
def test_project_task_status_is_total_over_documented_values(
    value: str, expected_phase: CanonicalLifecyclePhase
) -> None:
    projection = project_task_status(value)
    assert projection.source_vocab == "task_status"
    assert projection.source_value == value
    assert projection.phase == expected_phase


def test_project_task_status_accepts_str_enum() -> None:

    class FakeTaskStatus(StrEnum):
        PENDING = "PENDING"
        ACTIVE = "ACTIVE"

    projection = project_task_status(FakeTaskStatus.ACTIVE)
    assert projection.phase == "active"


@pytest.mark.parametrize(
    "value,expected_phase",
    [
        ("PENDING", "pending"),
        ("ACTIVE", "active"),
        ("DONE", "completed"),
        ("FAILED", "failed"),
        ("BLOCKED", "awaiting_async"),
    ],
)
def test_project_plan_step_status_is_total(
    value: str, expected_phase: CanonicalLifecyclePhase
) -> None:
    assert project_plan_step_status(value).phase == expected_phase


@pytest.mark.parametrize(
    "value,expected_phase",
    [
        ("active", "active"),
        ("paused", "paused"),
        ("awaiting_async", "awaiting_async"),
        ("completed", "completed"),
        ("cancelled", "cancelled"),
        ("halted", "failed"),
    ],
)
def test_project_mission_status_is_total(
    value: str, expected_phase: CanonicalLifecyclePhase
) -> None:
    assert project_mission_status(value).phase == expected_phase


@pytest.mark.parametrize(
    "value,expected_phase",
    [
        ("active", "active"),
        ("continue", "active"),
        ("waiting_user", "awaiting_user"),
        ("job_pending", "awaiting_async"),
        ("done", "completed"),
        ("error", "failed"),
        ("stopped", "paused"),
    ],
)
def test_project_working_status_is_total(
    value: str, expected_phase: CanonicalLifecyclePhase
) -> None:
    assert project_working_status(value).phase == expected_phase


@pytest.mark.parametrize(
    "fn",
    [
        project_task_status,
        project_plan_step_status,
        project_mission_status,
        project_working_status,
    ],
)
def test_unrecognized_status_falls_to_unknown_not_new_bucket(fn) -> None:
    assert fn("definitely_not_a_status").phase == "unknown"
    assert fn("").phase == "unknown"
    assert fn(None).phase == "unknown"


def test_resume_channel_cron_from_cron_resume_metadata() -> None:
    metadata = {"cron_resume_attempt_count": 2, "cron_resume_current_interval_s": 60}
    assert project_resume_channel(metadata) == "cron"


def test_resume_channel_cron_when_only_one_cron_key_present() -> None:
    assert project_resume_channel({"cron_resume_attempt_count": 1}) == "cron"
    assert project_resume_channel({"cron_resume_current_interval_s": 30}) == "cron"


def test_resume_channel_persistent_service_from_typed_id() -> None:
    assert (
        project_resume_channel({"persistent_service_id": "svc-1"})
        == "persistent_service"
    )


def test_resume_channel_delegated_from_typed_agent_id() -> None:
    assert project_resume_channel({"delegated_to_agent_id": "agent-2"}) == "delegated"


def test_resume_channel_direct_from_continuation_flag() -> None:
    assert project_resume_channel({"awaiting_continuation_reply": True}) == "direct"


def test_resume_channel_none_for_empty_or_missing_metadata() -> None:
    assert project_resume_channel(None) == "none"
    assert project_resume_channel({}) == "none"


def test_resume_channel_priority_cron_over_others() -> None:
    metadata = {
        "cron_resume_attempt_count": 1,
        "persistent_service_id": "svc-1",
        "awaiting_continuation_reply": True,
    }
    assert project_resume_channel(metadata) == "cron"


@pytest.mark.parametrize(
    "channel,from_phase,expected_to",
    [
        ("cron", "awaiting_async", "active"),
        ("persistent_service", "awaiting_async", "active"),
        ("delegated", "delegated_waiting", "active"),
        ("direct", "awaiting_user", "active"),
        ("direct", "paused", "active"),
    ],
)
def test_transition_resume_channels_wake_to_active(
    channel: str, from_phase: str, expected_to: str
) -> None:
    transition = resume_transition_for(channel=channel, from_phase=from_phase)  # type: ignore[arg-type]
    assert transition.to_phase == expected_to


@pytest.mark.parametrize(
    "channel",
    ["cron", "persistent_service", "delegated", "direct", "none", "unknown"],
)
@pytest.mark.parametrize("terminal", ["completed", "cancelled", "failed"])
def test_transition_terminal_phases_never_transition(
    channel: str, terminal: str
) -> None:
    transition = resume_transition_for(channel=channel, from_phase=terminal)  # type: ignore[arg-type]
    assert transition.to_phase == terminal
    assert transition.from_phase == terminal


def test_transition_none_channel_is_a_noop() -> None:
    transition = resume_transition_for(channel="none", from_phase="awaiting_async")
    assert transition.to_phase == "awaiting_async"


def test_transition_unknown_channel_is_a_noop() -> None:
    transition = resume_transition_for(channel="unknown", from_phase="awaiting_async")
    assert transition.to_phase == "awaiting_async"


def test_build_unified_projection_carries_phase_and_refs() -> None:
    source = project_mission_status("awaiting_async")
    projection = build_unified_projection(
        source_projection=source,
        resume_channel="cron",
        checkpoint_present=True,
        task_id="task-7",
        mission_id="mission-1",
        source_refs={"trace_id": "trace-abc"},
    )
    assert projection.phase == "awaiting_async"
    assert projection.resume_channel == "cron"
    assert projection.checkpoint_present is True
    assert projection.task_id == "task-7"
    assert projection.mission_id == "mission-1"
    assert projection.source_refs == {"trace_id": "trace-abc"}
    assert projection.source_projection.source_vocab == "mission_status"


def test_build_unified_projection_defaults_are_safe() -> None:
    source = project_task_status("ACTIVE")
    projection = build_unified_projection(source_projection=source)
    assert projection.resume_channel == "unknown"
    assert projection.checkpoint_present is False
    assert projection.task_id == ""
    assert projection.mission_id == ""


def test_unified_projection_is_deterministic() -> None:
    source_a = project_mission_status("paused")
    source_b = project_mission_status("paused")
    first = build_unified_projection(source_projection=source_a, resume_channel="cron")
    second = build_unified_projection(source_projection=source_b, resume_channel="cron")
    assert first.model_dump() == second.model_dump()


def test_schemas_do_not_expose_interpretive_fields() -> None:
    forbidden_substrings = (
        "looks_",
        "probably_",
        "seems_",
        "narrative",
        "summary",
        "description",
    )
    schema_fields = (
        set(LifecyclePhaseProjection.model_fields.keys())
        | set(ResumeTransition.model_fields.keys())
        | set(UnifiedLifecycleProjection.model_fields.keys())
    )
    for field_name in schema_fields:
        for forbidden in forbidden_substrings:
            assert forbidden not in field_name, (
                f"UALU discipline violation: schema field {field_name!r} "
                f"contains forbidden substring {forbidden!r}."
            )


def test_canonical_phase_set_is_closed() -> None:
    expected = {
        "pending",
        "active",
        "awaiting_user",
        "awaiting_async",
        "delegated_waiting",
        "paused",
        "completed",
        "cancelled",
        "failed",
        "unknown",
    }
    observed = set()
    for fn, values in (
        (project_task_status, ("PENDING", "ACTIVE", "WAITING", "DONE", "CANCELED")),
        (
            project_plan_step_status,
            ("PENDING", "ACTIVE", "DONE", "FAILED", "BLOCKED"),
        ),
        (
            project_mission_status,
            ("active", "paused", "awaiting_async", "completed", "cancelled", "halted"),
        ),
        (
            project_working_status,
            (
                "active",
                "continue",
                "waiting_user",
                "job_pending",
                "done",
                "error",
                "stopped",
            ),
        ),
    ):
        for v in values:
            observed.add(fn(v).phase)
    assert observed <= expected, f"unexpected phase(s): {observed - expected}"
