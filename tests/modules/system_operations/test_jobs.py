from datetime import timedelta

import pytest

from openminion.base.time import utc_now
from openminion.modules.system_operations.jobs import OperationJobStore
from openminion.modules.system_operations.schemas import OperationRequest


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
        store.cancel(job.job_id, target_id="other")
    with pytest.raises(PermissionError, match="another session"):
        store.cancel(job.job_id, session_id="other")
    with pytest.raises(PermissionError, match="another owner"):
        store.release_lease(job.job_id, owner="other")

    assert store.release_lease(job.job_id, owner="worker").lease_owner == ""
    assert (
        store.cancel(job.job_id, target_id="local", session_id="session").status
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
