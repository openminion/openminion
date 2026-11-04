from datetime import UTC, datetime, timedelta
from pathlib import Path
import json

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.security.schedule import (
    SourceScanAdmission,
    admit_run,
    admission_fingerprint,
)
from openminion.tools.security.trusted_source import scheduled_scan
from openminion.services.runtime.cron.security import bind_request
from openminion.modules.runtime.contracts import TurnRequest
from tests.task.test_lifecycle import _manager
from tests.tools.security.test_audit_report import audit_context as audit_context


def admission(root: Path, **overrides):
    return SourceScanAdmission.model_validate(
        dict(
            schema_version="security-source-schedule/v1",
            agent_id="security-fixture",
            root=root,
            checks=["code"],
            allowed_tools=["security.scan_code", "security.publish_report"],
            config_fingerprint="a" * 64,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            max_runs=2,
            timeout_seconds=30,
            **overrides,
        )
    )


def test_budget_idempotence_and_unicode_admission(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    scope = admission(tmp_path / "审计")
    monkeypatch.setattr(
        "openminion.tools.security.schedule.clean_revision", lambda _: "a" * 40
    )
    kwargs = dict(
        name="fixture",
        instruction="audit",
        schedule={"kind": "every", "every_ms": 60000},
        agent_id=scope.agent_id,
        security_scan=scope.model_dump(mode="json"),
    )
    try:
        created = manager.schedule_user_task(**kwargs)
        kwargs["schedule"] = created["job"]["schedule"]
        assert manager.schedule_user_task(**kwargs)["deduped"]
        job = created["job"]
        assert job["max_attempts"] == 1
        assert created["record"].metadata[
            "security_scan_admission"
        ] == admission_fingerprint(scope)
        run = dict(run_id="run-1", job_id=job["job_id"], state="running")
        assert admit_run(manager, job, run, scope) == "a" * 40
        with pytest.raises(ToolRuntimeError, match="already admitted"):
            admit_run(manager, job, run, scope)
        run["run_id"] = "run-2"
        admit_run(manager, job, run, scope)
        assert not manager.get_scheduled_job(job["job_id"])["enabled"]
        run["run_id"] = "run-3"
        with pytest.raises(ToolRuntimeError, match="exhausted"):
            admit_run(manager, job, run, scope)
    finally:
        manager.close()


def test_revocation_cannot_fall_back_to_host_scanner(audit_context, monkeypatch):  # noqa: F811
    ctx, _ = audit_context
    ctx.policy.raw["context_metadata"] = {
        "security_scan_context": json.dumps({"revision": "a" * 40})
    }
    monkeypatch.setattr(
        "openminion.tools.security.trusted_source.configured_admission",
        lambda **_: None,
    )
    with pytest.raises(ToolRuntimeError, match="revoked"):
        scheduled_scan(None, ctx=ctx, target=ctx.workspace, config=None, check="code")


def test_runtime_scope_is_internal_and_exact(tmp_path):
    scope = admission(tmp_path)
    request = TurnRequest(
        trace_id="trace",
        agent_id=scope.agent_id,
        session_id="session",
        input_text="scan",
    )
    bound = bind_request(
        request, (scope, "a" * 40), {"job_id": "job"}, {"run_id": "run"}
    )
    assert request.trusted_security_scan_context is None
    context = bound.trusted_security_scan_context
    assert context["permission_mode"] == "readonly"
    assert (
        context["turn_tool_allowlist"] == "security.scan_code,security.publish_report"
    )
    assert json.loads(context["security_scan_context"])[
        "admission_fingerprint"
    ] == admission_fingerprint(scope)


def canonical_source_report(ctx, revision):
    from openminion.modules.tool import preferred_artifact_ref
    from openminion.tools.security.plugin import _h_publish_report
    from tests.tools.security.test_audit_report import _scan, _publish_args

    ctx.agent_id = "security-fixture"
    ctx.trace_id = "security-trace"
    ctx.tool_name = "security.scan_code"
    scan = _scan(check="code", revision=revision)
    ref = preferred_artifact_ref(
        ctx.write_artifact(
            "security/scan.json",
            scan.model_dump_json().encode(),
            "application/json",
            durable=True,
        )
    )
    ctx.tool_name = "security.publish_report"
    report = _h_publish_report(
        _publish_args(revision=revision, refs={"code": ref}), ctx
    )
    return report["report_ref"], ref


def test_baseline_pins_report_dependencies_and_preserves_other_owners(audit_context):  # noqa: F811
    from openminion.tools.security.schedule import pin_baseline
    from tests.artifact.test_retention import _age_all_artifacts

    ctx, revision = audit_context
    report_ref, scan_ref = canonical_source_report(ctx, revision)
    scope = admission(ctx.workspace, baseline_ref=report_ref)
    ctl = ctx.artifactctl
    for job_id in ("first", "second"):
        pin_baseline(ctl, job_id=job_id, admission=scope)
    _age_all_artifacts(ctl, 30)

    def candidates():
        return ctl.gc(
            plan_only=True, keep_days=0, delete_unreferenced_after_days=0
        ).candidates

    for ref in (report_ref, scan_ref):
        assert ref.rsplit("/", 1)[1] not in candidates()
    ctl.ref_remove_owner("collection", "security-schedule:first")
    assert scan_ref.rsplit("/", 1)[1] not in candidates()
    ctl.ref_remove_owner("collection", "security-schedule:second")
    for ref in (report_ref, scan_ref):
        assert ref.rsplit("/", 1)[1] in candidates()


@pytest.mark.parametrize("brain_status", ["done", "error", "waiting_user"])
def test_completion_requires_terminal_agent_success(audit_context, brain_status):  # noqa: F811
    from openminion.services.runtime.cron.security import complete_run

    ctx, revision = audit_context
    report_ref, _ = canonical_source_report(ctx, revision)
    request = TurnRequest(
        trace_id=ctx.trace_id,
        agent_id=ctx.agent_id,
        session_id=ctx.session_id,
        input_text="scan",
    )
    result = complete_run(
        ctl=ctx.artifactctl,
        admission=admission(ctx.workspace),
        revision=revision,
        request=request,
        job={"job_id": "job"},
        run={"run_id": "run"},
        brain_status=brain_status,
    )
    assert result["report_ref"] == report_ref
    assert result["status"] == ("completed" if brain_status == "done" else "failed")
    assert ctx.artifactctl.read_bytes(result["sarif_ref"])


def test_cancelled_claimed_run_cannot_dispatch(tmp_path, monkeypatch):
    manager = _manager(tmp_path)
    scope = admission(tmp_path)
    monkeypatch.setattr(
        "openminion.tools.security.schedule.clean_revision", lambda _: "a" * 40
    )
    try:
        created = manager.schedule_user_task(
            name="fixture",
            instruction="audit",
            schedule={"kind": "every", "every_ms": 60000},
            agent_id=scope.agent_id,
            security_scan=scope.model_dump(mode="json"),
        )
        job = created["job"]
        manager.cancel_task(created["record"].task_id)
        with pytest.raises(ToolRuntimeError, match="cancelled"):
            admit_run(
                manager,
                job,
                dict(run_id="claimed", job_id=job["job_id"], state="running"),
                scope,
            )
        assert "admitted_runs" not in manager.get_task_by_job(
            job["job_id"]
        ).metadata.get("security_scan", {})
    finally:
        manager.close()


def test_finite_budget_survives_restart_and_failure_keeps_previous_evidence(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path)
    scope = admission(tmp_path)
    monkeypatch.setattr(
        "openminion.tools.security.schedule.clean_revision", lambda _: "a" * 40
    )
    created = manager.schedule_user_task(
        name="fixture",
        instruction="audit",
        schedule={"kind": "every", "every_ms": 60000},
        agent_id=scope.agent_id,
        security_scan=scope.model_dump(mode="json"),
    )
    job = created["job"]
    run = dict(run_id="first", job_id=job["job_id"], state="running")
    admit_run(manager, job, run, scope)
    evidence = dict(
        job_id=job["job_id"],
        run_id="first",
        status="completed",
        report_ref="artifact://sha256/" + "a" * 64,
    )
    manager.record_scheduled_outcome(
        cron_job_id=job["job_id"],
        run={**run, "state": "finished", "output": {"security_scan": evidence}},
    )
    manager.close()
    manager = _manager(tmp_path)
    try:
        with pytest.raises(ToolRuntimeError, match="already admitted"):
            admit_run(manager, job, run, scope)
        run["run_id"] = "second"
        admit_run(manager, job, run, scope)
        failed = dict(
            job_id=job["job_id"],
            run_id="second",
            status="failed",
            evidence_refs=["artifact://sha256/" + "b" * 64],
        )
        record = manager.record_scheduled_outcome(
            cron_job_id=job["job_id"],
            run={**run, "state": "failed", "output": {"security_scan": failed}},
        )
        state = record.metadata["security_scan"]
        assert state["admitted_runs"] == 2 and state["status"] == "failed"
        assert state["report_ref"] == evidence["report_ref"]
        assert state["last_attempt"] == failed
        assert not manager.get_scheduled_job(job["job_id"])["enabled"]
        assert (
            manager.record_scheduled_outcome(
                cron_job_id=job["job_id"],
                run={**run, "state": "failed", "output": {"security_scan": failed}},
            ).metadata
            == record.metadata
        )
    finally:
        manager.close()


def test_dirty_source_retains_partial_report(audit_context):  # noqa: F811
    from openminion.services.runtime.cron.security import complete_run

    ctx, revision = audit_context
    report_ref, _ = canonical_source_report(ctx, revision)
    (ctx.workspace / "app.py").write_text("print('changed')\n")
    result = complete_run(
        ctl=ctx.artifactctl,
        admission=admission(ctx.workspace),
        revision=revision,
        request=TurnRequest(
            trace_id=ctx.trace_id,
            agent_id=ctx.agent_id,
            session_id=ctx.session_id,
            input_text="scan",
        ),
        job={"job_id": "job"},
        run={"run_id": "run"},
        brain_status="done",
    )
    assert result["status"] == "partial" and result["report_ref"] == report_ref
    assert ctx.artifactctl.read_bytes(result["sarif_ref"])


@pytest.mark.parametrize("failure", [None, "submit", "timeout", "completion"])
@pytest.mark.parametrize("preexisting", [False, True])
def test_scheduled_turn_exposes_exact_tools_and_releases_only_owned_activation(
    audit_context,
    monkeypatch,
    failure,
    preexisting,  # noqa: F811
):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from openminion.base.types import Message
    from openminion.modules.brain.adapters.tool import ToolAdapter
    from openminion.modules.brain.loop.entry import build_entry_tool_specs
    from openminion.modules.brain.loop.services import turn_tool_allowlist
    from openminion.modules.tool import build_default_tool_registry
    from openminion.services.brain.post_execution.context import (
        _resolve_turn_session_ids,
    )
    from openminion.services.runtime.cron import security
    from openminion.services.runtime.daemon import build_turn_request

    ctx, revision = audit_context
    scope = admission(ctx.workspace).model_copy(
        update={"expires_at": datetime.now(UTC) + timedelta(seconds=60)}
    )
    registry = build_default_tool_registry(strict=False)
    exposure = registry.exposure_service
    canonical_session = "cron:job:run::conv:run"
    unrelated = exposure.activate(
        "security_readonly", session_id="unrelated", approved=True
    )
    previous = (
        exposure.activate(
            "security_readonly", session_id=canonical_session, approved=True
        )
        if preexisting
        else None
    )
    monkeypatch.setattr(security, "prepare_run", lambda **_: (scope, revision))
    adapter = ToolAdapter(
        workspace_root=ctx.workspace,
        runtime_registry=registry,
        artifactctl=ctx.artifactctl,
    )
    runner = SimpleNamespace(tool_api=adapter)
    seen = []

    def submit(request):
        _, actual_session = _resolve_turn_session_ids(
            message=Message(
                channel="cron",
                target="cron",
                body="audit",
                metadata={"session_id": request.session_id, **request.meta},
            )
        )
        assert actual_session == canonical_session
        assert request.trusted_security_scan_context["permission_mode"] == "readonly"
        allowed = turn_tool_allowlist(request.trusted_security_scan_context)
        assert set(allowed) == set(scope.allowed_tools)
        with adapter.restrict_tools(allowed):
            for session, expected in (
                (actual_session, set(scope.allowed_tools)),
                (request.session_id, set()),
                ("another-run", set()),
            ):
                specs, _ = build_entry_tool_specs(
                    runner,
                    act_profile="general",
                    execution_target_kind="local",
                    metadata={"session_id": session},
                )
                assert {spec.name for spec in specs} == expected
            assert not adapter.is_tool_allowed("security.review_report")
            assert not adapter.is_tool_allowed("exec.run")
        active = exposure.resolve_exact_activation(
            "security_readonly", session_id=actual_session
        )
        if previous is None:
            assert active.expires_at <= scope.expires_at.timestamp() + 0.1
        seen.append(request)
        if failure == "submit":
            raise RuntimeError("submit failed")

        def result(**_):
            if failure == "timeout":
                raise TimeoutError("turn expired")
            return SimpleNamespace(metadata={"brain_status": "done"}, errors=[])

        return SimpleNamespace(result=result)

    def complete(**_):
        if failure == "completion":
            raise RuntimeError("completion failed")
        return {"status": "completed"}

    monkeypatch.setattr(security, "complete_run", complete)
    kwargs = dict(
        runtime=SimpleNamespace(
            tools=registry, runtime_manager=SimpleNamespace(submit_turn=submit)
        ),
        manager=None,
        store=None,
        job={"job_id": "job", "payload": {}, "agent_id": scope.agent_id},
        run={"run_id": "run"},
        request_builder=lambda payload, agent, *_: build_turn_request(
            payload, default_agent_id=agent
        ),
        artifactctl_factory=lambda: nullcontext(ctx.artifactctl),
        timeout_s=30,
    )
    try:
        if failure:
            with pytest.raises(TimeoutError if failure == "timeout" else RuntimeError):
                security.execute_security_run(**kwargs)
        else:
            assert (
                security.execute_security_run(**kwargs)["output"]["security_scan"][
                    "status"
                ]
                == "completed"
            )
        assert len(seen) == 1
        if previous is not None:
            assert (
                exposure.resolve_exact_activation(
                    "security_readonly", session_id=canonical_session
                )
                == previous
            )
        else:
            assert (
                exposure.decide(
                    "security.scan_code", session_id=canonical_session
                ).state
                == "hidden"
            )
        assert (
            exposure.resolve_exact_activation(
                "security_readonly", session_id="unrelated"
            )
            == unrelated
        )
    finally:
        adapter.close()


@pytest.mark.parametrize("conversation", ["run", "another-run"])
def test_completion_reads_only_actual_tool_conversation(audit_context, conversation):  # noqa: F811
    from openminion.services.runtime.cron.security import complete_run

    ctx, revision = audit_context
    ctx.session_id = "cron-runtime::conv:run"
    report_ref, _ = canonical_source_report(ctx, revision)
    request = TurnRequest(
        trace_id=ctx.trace_id,
        agent_id=ctx.agent_id,
        session_id="cron-runtime",
        input_text="scan",
        meta={"conversation_id": conversation},
    )
    result = complete_run(
        ctl=ctx.artifactctl,
        admission=admission(ctx.workspace),
        revision=revision,
        request=request,
        job={"job_id": "job"},
        run={"run_id": "run"},
        brain_status="done",
    )
    if conversation == "run":
        assert result["status"] == "completed" and result["report_ref"] == report_ref
        assert ctx.artifactctl.get(result["sarif_ref"]).session_id == ctx.session_id
    else:
        assert result["status"] == "failed"
        assert result["reason_code"] == "missing_or_ambiguous_report"
        assert result["evidence_refs"] == []
