from datetime import timedelta
import sqlite3
import threading

import pytest

from openminion.base.time import utc_now
from openminion.tools.ops.jobs import OperationJobStore
from openminion.tools.ops.contracts import OperationRequest


def _request(operation_id: str, **overrides: object) -> OperationRequest:
    values: dict[str, object] = {
        "operation_id": operation_id,
        "target_id": "local",
        "profile_id": "host.snapshot",
        "session_id": "session",
    }
    values.update(overrides)
    return OperationRequest.model_validate(values)


def test_job_store_recovers_interrupted_work_after_restart(tmp_path) -> None:
    path = tmp_path / "jobs.db"
    store = OperationJobStore(path)
    job = store.submit(_request("interrupted"), target_revision=1)
    store.update(job.job_id, status="running")

    reopened = OperationJobStore(path)
    assert reopened.recover_running() == 1
    recovered = reopened.get(job.job_id)
    assert recovered.status == "failed"
    assert recovered.error == "operation interrupted before reconnect"


def test_legacy_command_job_loads_without_invented_authorization(tmp_path) -> None:
    path = tmp_path / "legacy-jobs.db"
    request = _request("plan-legacy", profile_id="command.run")
    connection = sqlite3.connect(path)
    connection.execute(
        """CREATE TABLE operation_jobs (
            job_id TEXT PRIMARY KEY,
            request_json TEXT NOT NULL,
            target_revision INTEGER NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            evidence_id TEXT NOT NULL,
            error TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            expires_at TEXT NOT NULL DEFAULT '',
            lease_owner TEXT NOT NULL DEFAULT ''
        )"""
    )
    connection.execute(
        "INSERT INTO operation_jobs VALUES (?, ?, ?, ?, ?, ?, '', '', '', '', '')",
        (
            "legacy-job",
            request.model_dump_json(),
            1,
            "failed",
            utc_now().isoformat(),
            utc_now().isoformat(),
        ),
    )
    connection.commit()
    connection.close()

    legacy = OperationJobStore(path).get("legacy-job")

    assert legacy.plan_id == "plan-legacy"
    assert legacy.attempt_phase == ""
    assert legacy.claim_token == ""
    assert legacy.approval_id == ""
    assert legacy.policy_grant_id == ""
    assert legacy.remote_outcome == "unknown"


def test_job_store_enforces_target_concurrency_and_idempotency() -> None:
    store = OperationJobStore(per_target_limit=1)
    request = _request("first", idempotency_key="same")
    first = store.submit(request, target_revision=1)

    assert store.submit(request, target_revision=1).job_id == first.job_id
    with pytest.raises(RuntimeError, match="concurrency limit"):
        store.submit(_request("second"), target_revision=1)


def test_job_store_scopes_cancel_and_lease_release() -> None:
    store = OperationJobStore()
    job = store.submit(_request("scoped"), target_revision=1)
    store.acquire_lease(job.job_id, owner="worker")

    with pytest.raises(PermissionError, match="another target"):
        store.request_cancel(job.job_id, target_id="other")
    with pytest.raises(PermissionError, match="another session"):
        store.request_cancel(job.job_id, session_id="other")
    with pytest.raises(PermissionError, match="another owner"):
        store.release_lease(job.job_id, owner="other")

    assert store.release_lease(job.job_id, owner="worker").lease_owner == ""
    assert (
        store.request_cancel(job.job_id, target_id="local", session_id="session").status
        == "cancelled"
    )


def test_job_store_prunes_only_terminal_expired_jobs() -> None:
    store = OperationJobStore()
    terminal = store.submit(_request("terminal"), target_revision=1)
    store.update(terminal.job_id, status="failed")
    active = store.submit(_request("active"), target_revision=1)
    expired = (utc_now() - timedelta(minutes=1)).isoformat()
    store._connection.execute(  # noqa: SLF001 - fixture controls persisted expiry
        "UPDATE operation_jobs SET expires_at = ?",
        (expired,),
    )
    store._connection.commit()  # noqa: SLF001

    assert store.prune_expired() == 1
    assert store.get(active.job_id).status == "queued"
    with pytest.raises(KeyError):
        store.get(terminal.job_id)


def test_plan_attempt_is_atomic_across_store_connections(tmp_path) -> None:
    path = tmp_path / "jobs.db"
    first_store = OperationJobStore(path)
    second_store = OperationJobStore(path)
    request = _request(
        "plan-1",
        profile_id="command.run",
        idempotency_key="caller-a",
    )
    results: list[tuple[str, str]] = []
    barrier = threading.Barrier(2)

    def claim(store: OperationJobStore) -> None:
        barrier.wait()
        job, token = store.claim_plan_attempt(
            request,
            plan_id="plan-1",
            target_revision=1,
        )
        results.append((job.job_id, token))

    threads = [
        threading.Thread(target=claim, args=(store,))
        for store in (first_store, second_store)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert len(results) == 2
    assert len({job_id for job_id, _ in results}) == 1
    assert sum(bool(token) for _, token in results) == 1


def test_pending_plan_can_be_reclaimed_but_active_claim_cannot() -> None:
    store = OperationJobStore()
    request = _request("plan-1", profile_id="command.run")
    job, first_token = store.claim_plan_attempt(
        request,
        plan_id="plan-1",
        target_revision=1,
    )

    duplicate, duplicate_token = store.claim_plan_attempt(
        request.model_copy(update={"idempotency_key": "different"}),
        plan_id="plan-1",
        target_revision=1,
    )
    assert duplicate.job_id == job.job_id
    assert duplicate_token == ""

    pending = store.await_approval(
        job.job_id,
        claim_token=first_token,
        approval_id="approval-1",
    )
    assert pending.status == "queued"
    assert pending.attempt_phase == "awaiting_approval"
    assert pending.remote_outcome == "not_dispatched"

    resumed, resumed_token = store.claim_plan_attempt(
        request,
        plan_id="plan-1",
        target_revision=1,
    )
    assert resumed.job_id == job.job_id
    assert resumed.attempt_phase == "claimed"
    assert resumed_token and resumed_token != first_token


def test_plan_capacity_and_dispatch_intent_are_atomic() -> None:
    store = OperationJobStore(per_target_limit=1)
    first, token = store.claim_plan_attempt(
        _request("plan-1", profile_id="command.run"),
        plan_id="plan-1",
        target_revision=1,
    )
    reserved = store.reserve_plan_capacity(
        first.job_id,
        claim_token=token,
        target_limit=1,
    )
    assert reserved.status == "running"
    assert reserved.attempt_phase == "claimed"

    second, second_token = store.claim_plan_attempt(
        _request("plan-2", profile_id="command.run"),
        plan_id="plan-2",
        target_revision=1,
    )
    with pytest.raises(RuntimeError, match="concurrency limit"):
        store.reserve_plan_capacity(
            second.job_id,
            claim_token=second_token,
            target_limit=1,
        )

    intent = store.record_dispatch_intent(
        first.job_id,
        claim_token=token,
        approval_id="approval-1",
        policy_grant_id="grant-1",
        policy_invocation_hash="a" * 64,
    )
    assert intent.attempt_phase == "dispatch_intent"
    assert intent.approval_id == "approval-1"
    assert intent.policy_grant_id == "grant-1"

    terminal = store.finish_plan_attempt(
        first.job_id,
        claim_token=token,
        status="succeeded",
        evidence_id="evidence-1",
        remote_outcome="exit_observed",
    )
    assert terminal.status == "succeeded"
    assert terminal.attempt_phase == "terminal"
    assert terminal.remote_outcome == "exit_observed"

    assert (
        store.reserve_plan_capacity(
            second.job_id,
            claim_token=second_token,
            target_limit=1,
        ).status
        == "running"
    )


@pytest.mark.parametrize("phase", ["claimed", "awaiting_approval", "reserved"])
def test_cancel_before_dispatch_is_terminal_and_not_replayable(phase: str) -> None:
    store = OperationJobStore()
    request = _request("plan-1", profile_id="command.run")
    job, token = store.claim_plan_attempt(
        request,
        plan_id="plan-1",
        target_revision=1,
    )
    if phase == "awaiting_approval":
        store.await_approval(
            job.job_id,
            claim_token=token,
            approval_id="approval-1",
        )
    elif phase == "reserved":
        store.reserve_plan_capacity(job.job_id, claim_token=token, target_limit=1)

    cancelled = store.request_cancel(job.job_id)
    repeated, repeated_token = store.claim_plan_attempt(
        request,
        plan_id="plan-1",
        target_revision=1,
    )

    assert cancelled.status == "cancelled"
    assert cancelled.attempt_phase == "terminal"
    assert cancelled.cancel_requested is True
    assert cancelled.remote_outcome == "not_dispatched"
    assert repeated.job_id == job.job_id
    assert repeated_token == ""


def test_cancel_after_dispatch_intent_records_delivery_without_terminal_claim() -> None:
    store = OperationJobStore()
    job, token = store.claim_plan_attempt(
        _request("plan-1", profile_id="command.run"),
        plan_id="plan-1",
        target_revision=1,
    )
    store.reserve_plan_capacity(job.job_id, claim_token=token, target_limit=1)
    store.record_dispatch_intent(
        job.job_id,
        claim_token=token,
        approval_id="approval-1",
        policy_grant_id="grant-1",
        policy_invocation_hash="a" * 64,
    )

    requested = store.request_cancel(job.job_id)
    not_delivered = store.record_cancel_delivery(job.job_id, delivered=False)

    assert requested.status == "running"
    assert requested.cancel_requested is True
    assert not_delivered.status == "running"
    assert not_delivered.cancel_status == "cancel_not_delivered"
    assert not_delivered.remote_outcome == "unknown"


def test_interruption_mark_and_late_completion_preserve_terminal_disposition() -> None:
    store = OperationJobStore()
    job, token = store.claim_plan_attempt(
        _request("plan-1", profile_id="command.run"),
        plan_id="plan-1",
        target_revision=1,
    )
    store.reserve_plan_capacity(job.job_id, claim_token=token, target_limit=1)

    interrupted, changed, prior_status, prior_phase = store.mark_interrupted(
        job.job_id,
        reason="operator verified remote state",
        actor="local",
    )
    late = store.finish_plan_attempt(
        job.job_id,
        claim_token=token,
        status="succeeded",
        evidence_id="late-evidence",
        remote_outcome="exit_observed",
    )

    assert changed is True
    assert (prior_status, prior_phase) == ("running", "claimed")
    assert interrupted.status == "failed"
    assert interrupted.remote_outcome == "unknown"
    assert interrupted.interruption_reason == "operator verified remote state"
    assert late.status == "failed"
    assert late.remote_outcome == "unknown"
    assert late.evidence_id == "late-evidence"
