from __future__ import annotations

from datetime import timedelta

import pytest

from openminion.services.cron import (
    compute_next_due,
    normalize_misfire_policy,
    normalize_schedule,
    parse_iso_datetime,
    to_iso_utc,
    utc_now,
)
from openminion.services.cron.scheduling import _select_due_points_for_job


def test_normalize_schedule_at_every_cron() -> None:
    now = to_iso_utc(utc_now())
    at = normalize_schedule({"kind": "at", "at": now})
    every = normalize_schedule({"kind": "every", "every_ms": 1000})
    cron = normalize_schedule({"kind": "cron", "expr": "0 * * * *", "tz": "UTC"})
    assert at["kind"] == "at"
    assert every["kind"] == "every"
    assert cron["kind"] == "cron"


def test_cron_schedule_defaults_timezone_when_missing() -> None:
    cron = normalize_schedule({"kind": "cron", "expr": "0 * * * *"})
    assert isinstance(cron["tz"], str)
    assert cron["tz"].strip()


def test_cron_top_of_hour_defaults_stagger() -> None:
    cron = normalize_schedule({"kind": "cron", "expr": "0 * * * *", "tz": "UTC"})
    assert cron["stagger_ms"] == 300000


def test_cron_non_top_of_hour_defaults_no_stagger() -> None:
    cron = normalize_schedule({"kind": "cron", "expr": "15 * * * *", "tz": "UTC"})
    assert cron["stagger_ms"] == 0


def test_cron_stagger_alias_supports_camel_case() -> None:
    cron = normalize_schedule(
        {"kind": "cron", "expr": "0 * * * *", "tz": "UTC", "staggerMs": 0}
    )
    assert cron["stagger_ms"] == 0


def test_compute_next_due_every_advances() -> None:
    start = utc_now()
    due = compute_next_due(
        schedule={"kind": "every", "every_ms": 2000},
        after=start,
        job_id="job-a",
        last_due=start,
    )
    assert due is not None
    assert due >= start + timedelta(seconds=2)


def test_compute_next_due_at_past_is_none() -> None:
    past = utc_now() - timedelta(minutes=1)
    due = compute_next_due(
        schedule={"kind": "at", "at": to_iso_utc(past)},
        after=utc_now(),
        job_id="job-b",
        last_due=None,
    )
    assert due is None


def test_normalize_misfire_policy_catch_up_form() -> None:
    policy = normalize_misfire_policy("catch_up(max=3)")
    assert policy.mode == "catch_up"
    assert policy.catch_up_max == 3


def test_invalid_timezone_raises() -> None:
    with pytest.raises(ValueError):
        normalize_schedule({"kind": "cron", "expr": "0 7 * * *", "tz": "Mars/Phobos"})


def test_compute_next_due_cron_applies_default_stagger_window() -> None:
    after = parse_iso_datetime("2026-01-01T10:10:00Z")
    due_with_default = compute_next_due(
        schedule={"kind": "cron", "expr": "0 * * * *", "tz": "UTC"},
        after=after,
        job_id="job-stagger",
        last_due=None,
    )
    due_without_stagger = compute_next_due(
        schedule={"kind": "cron", "expr": "0 * * * *", "tz": "UTC", "stagger_ms": 0},
        after=after,
        job_id="job-stagger",
        last_due=None,
    )
    assert due_with_default is not None
    assert due_without_stagger is not None
    delta_ms = int((due_with_default - due_without_stagger).total_seconds() * 1000)
    assert 0 <= delta_ms <= 300000


def test_parse_iso_datetime_assumes_utc_when_missing_tz() -> None:
    parsed = parse_iso_datetime("2026-01-01T00:00:00")
    assert parsed.tzinfo is not None


def test_select_due_points_for_job_every_schedule() -> None:
    now_dt = parse_iso_datetime("2026-03-25T12:00:00Z")
    selected, next_due = _select_due_points_for_job(
        job={
            "job_id": "job-every",
            "schedule": {"kind": "every", "every_ms": 60_000},
            "next_due_at": "2026-03-25T11:58:00Z",
            "misfire_policy": "catch_up(max=2)",
            "max_lateness_s": 600,
        },
        now_dt=now_dt,
    )
    assert [to_iso_utc(item) for item in selected] == [
        "2026-03-25T11:59:00+00:00",
        "2026-03-25T12:00:00+00:00",
    ]
    assert next_due is not None
    assert to_iso_utc(next_due) == "2026-03-25T12:01:00+00:00"


def test_select_due_points_for_job_cron_schedule() -> None:
    now_dt = parse_iso_datetime("2026-03-25T12:30:00Z")
    selected, next_due = _select_due_points_for_job(
        job={
            "job_id": "job-cron",
            "schedule": {
                "kind": "cron",
                "expr": "0 * * * *",
                "tz": "UTC",
                "stagger_ms": 0,
            },
            "next_due_at": "2026-03-25T12:00:00Z",
            "misfire_policy": "skip",
            "max_lateness_s": 3600,
        },
        now_dt=now_dt,
    )
    assert [to_iso_utc(item) for item in selected] == ["2026-03-25T12:00:00+00:00"]
    assert next_due is not None
    assert to_iso_utc(next_due) == "2026-03-25T13:00:00+00:00"


def test_select_due_points_for_job_at_schedule() -> None:
    now_dt = parse_iso_datetime("2026-03-25T12:00:00Z")
    selected, next_due = _select_due_points_for_job(
        job={
            "job_id": "job-at",
            "schedule": {"kind": "at", "at": "2026-03-25T11:55:00Z"},
            "next_due_at": "2026-03-25T11:55:00Z",
            "misfire_policy": "run_once",
            "max_lateness_s": 600,
        },
        now_dt=now_dt,
    )
    assert [to_iso_utc(item) for item in selected] == ["2026-03-25T11:55:00+00:00"]
    assert next_due is None


def test_select_due_points_for_job_stale_past_due_dates() -> None:
    now_dt = parse_iso_datetime("2026-03-25T12:00:00Z")
    selected, next_due = _select_due_points_for_job(
        job={
            "job_id": "job-stale",
            "schedule": {"kind": "every", "every_ms": 60_000},
            "next_due_at": "2026-03-25T11:30:00Z",
            "misfire_policy": "run_once",
            "max_lateness_s": 60,
        },
        now_dt=now_dt,
    )
    assert [to_iso_utc(item) for item in selected] == ["2026-03-25T12:00:00+00:00"]
    assert next_due is not None
    assert to_iso_utc(next_due) == "2026-03-25T12:01:00+00:00"
