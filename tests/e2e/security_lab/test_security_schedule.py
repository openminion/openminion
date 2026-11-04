"""Real offline workers across two persisted cron ticks; model dispatch is scripted."""

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from openminion.base.config.env import EnvironmentConfig
from openminion.modules.tool import build_default_tool_registry
from openminion.modules.telemetry.usage import usage_event_session_id
from openminion.services.runtime.cron.security import execute_security_run
from openminion.services.runtime.daemon import build_turn_request
from openminion.tools.security.plugin import _h_scan_code, _h_publish_report
from openminion.tools.security.schedule import (
    SourceScanAdmission,
    admission_fingerprint,
)
from tests.task.test_lifecycle import _manager
from tests.tools.security.test_audit_report import (
    audit_context as audit_context,
    _git,
    _publish_args,
)
from tests.e2e.security_lab.test_offline_scan import offline_runner as offline_runner

pytestmark = pytest.mark.e2e


def test_two_real_worker_ticks_survive_restart(audit_context, offline_runner, tmp_path):  # noqa: F811
    ctx, _ = audit_context
    rules = Path(os.environ["OPENMINION_TEST_SECURITY_SCAN_RULES"])
    (ctx.workspace / "rule.yml").write_bytes(rules.read_bytes())
    (ctx.workspace / "app.py").write_text("eval(input())\n")
    _git("add", ".", cwd=ctx.workspace)
    _git("commit", "-qm", "vulnerable fixture", cwd=ctx.workspace)
    config = tmp_path / "offline.json"
    config.write_text(
        json.dumps({"docker": offline_runner.config.model_dump(), "targets": {}})
    )
    scope = SourceScanAdmission(
        schema_version="security-source-schedule/v1",
        agent_id="security-fixture",
        root=ctx.workspace,
        checks=["code"],
        allowed_tools=["security.scan_code", "security.publish_report"],
        config_fingerprint=sha256(config.read_bytes() + rules.read_bytes()).hexdigest(),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        max_runs=2,
        timeout_seconds=120,
    )
    admission_path = tmp_path / "admissions.json"
    admission_path.write_text(
        json.dumps({scope.agent_id: scope.model_dump(mode="json")})
    )
    env = {
        "OPENMINION_SECURITY_SCAN_CONFIG": str(config),
        "OPENMINION_SECURITY_SEMGREP_CONFIG": str(rules),
        "OPENMINION_SECURITY_SCHEDULE_CONFIG": str(admission_path),
    }
    ctx.env = EnvironmentConfig(values=env)
    revisions, scan_refs = [], []

    def submit(request):
        ctx.agent_id, ctx.session_id, ctx.trace_id = (
            request.agent_id,
            usage_event_session_id(
                {"session_id": request.session_id, "metadata": request.meta}
            ),
            request.trace_id,
        )
        ctx.policy.raw["context_metadata"] = request.trusted_security_scan_context
        admitted = json.loads(
            request.trusted_security_scan_context["security_scan_context"]
        )
        assert admitted["admission_fingerprint"] == admission_fingerprint(scope)
        assert request.trace_id == admitted["run_id"]
        revisions.append(admitted["revision"])
        ctx.tool_name = "security.scan_code"
        result = _h_scan_code(
            {
                "target": ".",
                "expected_target_revision": admitted["revision"],
                "include_evidence_artifact": True,
            },
            ctx,
        )
        assert result["status"] == "completed"
        assert result["provenance"]["cleanup_completed"]
        scan_refs.append(result["evidence_refs"][0])
        assert bool(result["findings"]) is (len(revisions) == 1)
        ctx.tool_name = "security.publish_report"
        _h_publish_report(
            _publish_args(revision=admitted["revision"], refs={"code": scan_refs[-1]}),
            ctx,
        )
        return SimpleNamespace(
            result=lambda **_: SimpleNamespace(
                metadata={"brain_status": "done"}, errors=[]
            )
        )

    runtime = SimpleNamespace(
        tools=build_default_tool_registry(strict=False),
        config=SimpleNamespace(runtime=SimpleNamespace(env=env)),
        runtime_manager=SimpleNamespace(submit_turn=submit),
    )
    manager = _manager(tmp_path)
    start = datetime.now(UTC)
    created = manager.schedule_user_task(
        name="security fixture",
        instruction="audit",
        schedule={"kind": "every", "every_ms": 60000, "anchor_at": start.isoformat()},
        agent_id=scope.agent_id,
        security_scan=scope.model_dump(mode="json"),
    )
    job_id = created["job"]["job_id"]
    receipts = []
    try:
        for tick in range(2):
            store = manager._cron_repository
            now = (start + timedelta(seconds=61 * (tick + 1))).isoformat()
            store.enqueue_due_cron_runs("fixture", now_iso=now)
            runs = store.acquire_cron_runs("fixture", now_iso=now)
            assert len(runs) == 1
            run = runs[0]
            result = execute_security_run(
                runtime=runtime,
                manager=manager,
                store=store,
                job=manager.get_scheduled_job(job_id),
                run=run,
                request_builder=lambda payload, agent, consolidation, watch: (
                    build_turn_request(payload, default_agent_id=agent)
                ),
                artifactctl_factory=lambda: nullcontext(ctx.artifactctl),
                timeout_s=10,
            )
            assert not result["error"]
            receipt = result["output"]["security_scan"]
            assert receipt["status"] == "completed"
            assert ctx.artifactctl.read_bytes(receipt["sarif_ref"])
            receipts.append(receipt)
            finished = store.finish_cron_run(
                run["run_id"],
                state="finished",
                output=result["output"],
                daemon_id="fixture",
                expected_attempts=run["attempts"],
                now_iso=now,
            )
            manager.record_scheduled_outcome(cron_job_id=job_id, run=finished)
            if tick == 0:
                manager.close()
                manager = _manager(tmp_path)
                (ctx.workspace / "app.py").write_text("print(input())\n")
                _git("add", ".", cwd=ctx.workspace)
                _git("commit", "-qm", "corrected fixture", cwd=ctx.workspace)
        assert revisions[0] != revisions[1]
        assert not manager.get_scheduled_job(job_id)["enabled"]
        assert (
            manager.get_task_by_job(job_id).metadata["security_scan"]["admitted_runs"]
            == 2
        )
        assert all(
            ref.rsplit("/", 1)[1] in ctx.artifactctl.index.active_reference_shas()
            for ref in scan_refs
        )
        (tmp_path / "receipts.json").write_text(json.dumps(receipts, indent=2))
    finally:
        manager.close()
