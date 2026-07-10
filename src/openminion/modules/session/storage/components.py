from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from .store import SQLiteSessionStore


def create_snapshot(store: Any, session_id: str, seq_upto: int | None = None) -> str:
    return store._summary_store.create_snapshot(session_id, seq_upto=seq_upto)


def add_cron_job(
    store: Any,
    *,
    name: str,
    schedule: Mapping[str, Any],
    payload: Mapping[str, Any],
    description: str | None = None,
    enabled: bool = True,
    agent_id: str | None = None,
    session_target: str | None = None,
    wake_mode: str | None = None,
    delivery: Mapping[str, Any] | None = None,
    delete_after_run: bool | None = None,
    misfire_policy: str | Mapping[str, Any] | None = None,
    max_lateness_s: int = 600,
    max_concurrency: int = 1,
    job_id: str | None = None,
) -> str:
    return store._cron_store.add_cron_job(
        name=name,
        schedule=schedule,
        payload=payload,
        description=description,
        enabled=enabled,
        agent_id=agent_id,
        session_target=session_target,
        wake_mode=wake_mode,
        delivery=delivery,
        delete_after_run=delete_after_run,
        misfire_policy=misfire_policy,
        max_lateness_s=max_lateness_s,
        max_concurrency=max_concurrency,
        job_id=job_id,
    )


def get_cron_job(store: Any, job_id: str) -> dict[str, Any] | None:
    return store._cron_store.get_cron_job(job_id)


def list_cron_jobs(
    store: Any,
    *,
    enabled: bool | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return store._cron_store.list_cron_jobs(enabled=enabled, limit=limit)


def set_cron_job_enabled(store: Any, job_id: str, enabled: bool) -> None:
    store._cron_store.set_cron_job_enabled(job_id, enabled)


def replace_cron_job_payload(
    store: Any,
    job_id: str,
    payload: Mapping[str, Any],
) -> None:
    store._cron_store.replace_cron_job_payload(job_id, payload)


def delete_cron_job(store: Any, job_id: str) -> None:
    store._cron_store.delete_cron_job(job_id)


def trigger_cron_run(
    store: Any,
    job_id: str,
    *,
    due_at: str | None = None,
    lease_owner: str | None = None,
    lease_ttl_s: int = 60,
) -> str:
    return store._cron_store.trigger_cron_run(
        job_id,
        due_at=due_at,
        lease_owner=lease_owner,
        lease_ttl_s=lease_ttl_s,
    )


def list_cron_runs(
    store: Any,
    *,
    job_id: str | None = None,
    limit: int = 100,
    states: list[str] | None = None,
) -> list[dict[str, Any]]:
    return store._cron_store.list_cron_runs(
        job_id=job_id,
        limit=limit,
        states=states,
    )


def enqueue_due_cron_runs(
    store: Any,
    daemon_id: str,
    *,
    lease_ttl_s: int = 60,
    max_jobs: int = 50,
    now_iso: str | None = None,
) -> list[dict[str, Any]]:
    return store._cron_store.enqueue_due_cron_runs(
        daemon_id,
        lease_ttl_s=lease_ttl_s,
        max_jobs=max_jobs,
        now_iso=now_iso,
    )


def acquire_cron_runs(
    store: Any,
    daemon_id: str,
    *,
    lease_ttl_s: int = 60,
    limit: int = 10,
    now_iso: str | None = None,
) -> list[dict[str, Any]]:
    return store._cron_store.acquire_cron_runs(
        daemon_id,
        lease_ttl_s=lease_ttl_s,
        limit=limit,
        now_iso=now_iso,
    )


def renew_cron_run_lease(
    store: Any,
    run_id: str,
    *,
    daemon_id: str,
    lease_ttl_s: int = 60,
    now_iso: str | None = None,
) -> bool:
    return store._cron_store.renew_cron_run_lease(
        run_id,
        daemon_id=daemon_id,
        lease_ttl_s=lease_ttl_s,
        now_iso=now_iso,
    )


def finish_cron_run(
    store: Any,
    run_id: str,
    *,
    state: str,
    summary: str | None = None,
    artifact_refs: list[dict[str, Any]] | None = None,
    error: dict[str, Any] | None = None,
    isolated_session_id: str | None = None,
    now_iso: str | None = None,
) -> dict[str, Any] | None:
    return store._cron_store.finish_cron_run(
        run_id,
        state=state,
        summary=summary,
        artifact_refs=artifact_refs,
        error=error,
        isolated_session_id=isolated_session_id,
        now_iso=now_iso,
    )


def delete_old_cron_runs(store: Any, before_iso: str) -> int:
    return store._cron_store.delete_old_cron_runs(before_iso)


def mark_cron_delivery_target(store: Any, run_id: str, *, target: str) -> bool:
    return store._cron_store.mark_cron_delivery_target(run_id, target=target)


def storage_status(store: Any) -> dict[str, Any]:
    return store._hybrid_store.status()


def reindex_sidecars(store: Any, *, since_ts: str | None = None) -> dict[str, Any]:
    return store._hybrid_store.reindex(
        from_fs=True,
        since_ts=since_ts,
        namespace="sessctl",
    ).to_dict()


def create_prompt_context(
    store: Any,
    session_id: str,
    *,
    seed_bundle_id: str | None = None,
    checkpoint_id: str | None = None,
    prefix_hash: str | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    return store._context_store.create_prompt_context(
        session_id,
        seed_bundle_id=seed_bundle_id,
        checkpoint_id=checkpoint_id,
        prefix_hash=prefix_hash,
        meta=meta,
    )


def close_prompt_context(
    store: Any,
    prompt_context_id: str,
    *,
    rollover_reason: str | None = None,
) -> None:
    store._context_store.close_prompt_context(
        prompt_context_id,
        rollover_reason=rollover_reason,
    )


def get_active_prompt_context(
    store: Any,
    session_id: str,
) -> dict[str, Any] | None:
    return store._context_store.get_active_prompt_context(session_id)


def save_compression_checkpoint(
    store: Any,
    session_id: str,
    bundle_json: str,
    *,
    up_to_event_id: str | None = None,
    reason: str | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    return store._context_store.save_compression_checkpoint(
        session_id,
        bundle_json,
        up_to_event_id=up_to_event_id,
        reason=reason,
        meta=meta,
    )


def get_latest_checkpoint(store: Any, session_id: str) -> dict[str, Any] | None:
    return store._context_store.get_latest_checkpoint(session_id)


def save_seed_bundle(
    store: Any,
    session_id: str,
    source_bundle_id: str,
    sections_json: str,
    total_tokens: int,
    *,
    source_checkpoint_id: str | None = None,
    budgets_json: str = "{}",
    up_to_event_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    return store._context_store.save_seed_bundle(
        session_id,
        source_bundle_id,
        sections_json,
        total_tokens,
        source_checkpoint_id=source_checkpoint_id,
        budgets_json=budgets_json,
        up_to_event_id=up_to_event_id,
        meta=meta,
    )


def get_latest_seed_bundle(store: Any, session_id: str) -> dict[str, Any] | None:
    return store._context_store.get_latest_seed_bundle(session_id)


def create_run_record(
    store: SQLiteSessionStore,
    session_id: str,
    run_type: str = "llm",
    *,
    run_id: str | None = None,
    prompt_context_id: str | None = None,
    model_id: str | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    return store.run_store.create_run_record(
        session_id,
        run_type,
        run_id=run_id,
        prompt_context_id=prompt_context_id,
        model_id=model_id,
        meta=meta,
    )


def finish_run_record(
    store: SQLiteSessionStore,
    run_id: str,
    *,
    status: str = "completed",
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    store.run_store.finish_run_record(
        run_id,
        status=status,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def add_run_usage_delta(
    store: SQLiteSessionStore,
    run_id: str,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    store.run_store.add_run_usage_delta(
        run_id,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def get_run_record(store: SQLiteSessionStore, run_id: str) -> dict[str, Any] | None:
    return store.run_store.get_run_record(run_id)


def list_run_records(
    store: SQLiteSessionStore, session_id: str
) -> list[dict[str, Any]]:
    return store.run_store.list_run_records(session_id)


def add_message_ref(
    store: SQLiteSessionStore,
    session_id: str,
    role: str,
    *,
    run_id: str | None = None,
    event_id: str | None = None,
    content_ref: str | None = None,
    content_inline: str | None = None,
    meta: dict[str, Any] | None = None,
) -> str:
    return store.run_store.add_message_ref(
        session_id,
        role,
        run_id=run_id,
        event_id=event_id,
        content_ref=content_ref,
        content_inline=content_inline,
        meta=meta,
    )


def update_derived_views(store: Any, session_id: str) -> dict[str, Any]:
    return store._summary_store.update_derived_views(session_id)


def get_slice(
    store: Any,
    session_id: str,
    purpose: str,
    limits: Mapping[str, Any] | Any | None = None,
) -> dict[str, Any]:
    return store._slice_store.get_slice(session_id, purpose, limits)


def _list_recent_archive_ref_lines(
    store: Any,
    *,
    session_id: str,
    limit: int,
) -> list[str]:
    return store._slice_queries.list_recent_archive_ref_lines(
        session_id=session_id,
        limit=limit,
    )


def enforce_context_manifest(
    store: Any,
    session_id: str,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    return store._replay_helper.enforce_context_manifest(session_id, manifest)


def emit_canonical_event(
    store: Any,
    session_id: str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    actor_type: str = "system",
    actor_id: str | None = None,
    trace_id: str | None = None,
    task_id: str | None = None,
    importance: int = 1,
) -> str:
    return store._replay_helper.emit_canonical_event(
        session_id=session_id,
        event_type=event_type,
        payload=payload,
        actor_type=actor_type,
        actor_id=actor_id,
        trace_id=trace_id,
        task_id=task_id,
        importance=importance,
    )


def get_replay_events(
    store: Any,
    session_id: str,
    *,
    from_seq: int = 0,
    to_seq: int | None = None,
    event_types: list[str] | None = None,
) -> list[dict[str, Any]]:
    return store._replay_helper.get_replay_events(
        session_id,
        from_seq=from_seq,
        to_seq=to_seq,
        event_types=event_types,
    )


def get_resume_state(store: Any, session_id: str) -> dict[str, Any]:
    return store._replay_helper.get_resume_state(session_id)


__all__ = [
    "_list_recent_archive_ref_lines",
    "acquire_cron_runs",
    "add_cron_job",
    "add_message_ref",
    "add_run_usage_delta",
    "close_prompt_context",
    "create_prompt_context",
    "create_run_record",
    "create_snapshot",
    "delete_cron_job",
    "delete_old_cron_runs",
    "emit_canonical_event",
    "enqueue_due_cron_runs",
    "enforce_context_manifest",
    "finish_cron_run",
    "finish_run_record",
    "get_active_prompt_context",
    "get_cron_job",
    "get_latest_checkpoint",
    "get_latest_seed_bundle",
    "get_replay_events",
    "get_resume_state",
    "get_run_record",
    "get_slice",
    "list_cron_jobs",
    "list_cron_runs",
    "list_run_records",
    "mark_cron_delivery_target",
    "reindex_sidecars",
    "renew_cron_run_lease",
    "replace_cron_job_payload",
    "save_compression_checkpoint",
    "save_seed_bundle",
    "set_cron_job_enabled",
    "storage_status",
    "trigger_cron_run",
    "update_derived_views",
]
