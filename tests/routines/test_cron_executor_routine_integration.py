from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import pytest

from openminion.modules.tool.registry import ToolRegistry
from openminion.services.runtime.cron.executor import CronTurnExecutor
from openminion.tools.github.plugin import register as register_github_tools
from openminion.tools.github.providers import (
    provider_registry,
    register_provider,
)
from openminion.tools.task.constants import WATCH_PAYLOAD_KEY
from openminion.tools.task.routine.schemas import (
    GitHubPrReviewConfigV1,
    RoutinePayloadV1,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class _FakeGithubProvider:
    provider_id = "openminion-builtin-github"

    def __init__(self) -> None:
        self.pr_table: dict[int, dict[str, Any]] = {}

    def set_prs(self, prs: list[dict[str, Any]]) -> None:
        self.pr_table = {pr["number"]: pr for pr in prs}

    def list_prs(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        del ctx
        return {
            "ok": True,
            "data": {"open_prs": list(self.pr_table.values())},
        }

    def fetch_pr(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        return {"ok": True, "data": self.pr_table.get(args["number"], {})}

    def fetch_diff(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        return {"ok": True, "data": {"diff": ""}}

    def fetch_comments(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        return {"ok": True, "data": {"comments": []}}

    def fetch_checks(self, *, args: Mapping[str, Any], ctx: Any) -> dict[str, Any]:
        return {"ok": True, "data": {"checks": []}}

    def healthcheck(self) -> bool:
        return True


class _FakeTurnResult:
    def __init__(self, *, final_text: str, metadata: dict | None = None) -> None:
        self.final_text = final_text
        self.metadata = metadata or {}


class _FakeTurnHandle:
    def __init__(self, result: _FakeTurnResult) -> None:
        self._result = result

    def result(self, *, timeout_s: float):
        del timeout_s
        return self._result


class _FakeRuntimeManager:
    def __init__(self, *, registered_agents: tuple[str, ...] = ("agent-1",)) -> None:
        self._registered = set(registered_agents)
        self.submitted_requests: list[Any] = []
        self._next_final_text = ""

    def is_agent_registered(self, agent_id: str) -> bool:
        return agent_id in self._registered

    def stage_final_text(self, text: str) -> None:
        self._next_final_text = text

    def submit_turn(self, request: Any) -> _FakeTurnHandle:
        self.submitted_requests.append(request)
        return _FakeTurnHandle(
            _FakeTurnResult(final_text=self._next_final_text, metadata={})
        )


class _FakeRequest:
    def __init__(self, *, message: str, agent_id: str, session_id: str) -> None:
        self.message = message
        self.agent_id = agent_id
        self.session_id = session_id
        self.meta: dict[str, Any] = {}


class _FakeRuntime:
    def __init__(self, *, registry: ToolRegistry, manager: _FakeRuntimeManager) -> None:
        self.tools = registry
        self.runtime_manager = manager


class _FakeCronStore:
    def __init__(self) -> None:
        self.replaced: list[tuple[str, dict]] = []

    def replace_cron_job_payload(self, job_id: str, payload: dict) -> None:
        self.replaced.append((job_id, dict(payload)))


@pytest.fixture
def registry() -> ToolRegistry:
    reg = ToolRegistry()
    register_github_tools(reg)
    return reg


@pytest.fixture
def fake_provider() -> _FakeGithubProvider:
    provider_registry().reset()
    provider = _FakeGithubProvider()
    register_provider(provider)
    yield provider
    provider_registry().reset()


def _build_executor(
    *,
    registry: ToolRegistry,
    manager: _FakeRuntimeManager,
    cron_store: _FakeCronStore,
) -> CronTurnExecutor:
    runtime = _FakeRuntime(registry=registry, manager=manager)
    return CronTurnExecutor(
        runtime=runtime,
        cron_store=cron_store,
        request_builder=lambda payload, agent_id: _FakeRequest(
            message=payload.get("message", ""),
            agent_id=agent_id,
            session_id=payload.get("session_id") or "sess-iso-1",
        ),
        timeout_s=10.0,
        max_attempts=1,
    )


def _routine_job(
    *,
    job_id: str = "job-routine-1",
    routine: RoutinePayloadV1 | None = None,
) -> dict[str, Any]:
    routine = routine or RoutinePayloadV1(
        config=GitHubPrReviewConfigV1(owner="octocat", repo="hello-world")
    )
    return {
        "job_id": job_id,
        "agent_id": "agent-1",
        "created_at": _now_iso(),
        "payload": {
            "kind": "agentTurn",
            "message": "watch check",
            "session_id": "watch:job-routine-1",
            WATCH_PAYLOAD_KEY: {
                "description": "PR routine",
                "check_instruction": "Review open PRs.",
                "alert_condition": "any change",
                "delivery": "announce",
                "interval_minutes": 5,
                "max_checks": 6,
                "checks_completed": 0,
                "ttl_minutes": 60,
                "timeout_seconds": 60,
                "max_iterations": 3,
                "allowed_tools": [],
                "turn_kind": "check",
                "write_authorized": False,
                "write_audit": [],
                "routine": routine.model_dump(mode="json"),
            },
        },
    }


def _plain_watch_job() -> dict[str, Any]:
    return {
        "job_id": "job-plain",
        "agent_id": "agent-1",
        "created_at": _now_iso(),
        "payload": {
            "kind": "agentTurn",
            "message": "watch check",
            "session_id": "watch:job-plain",
            WATCH_PAYLOAD_KEY: {
                "description": "plain watch",
                "check_instruction": "watch instr",
                "alert_condition": "any change",
                "delivery": "announce",
                "interval_minutes": 5,
                "max_checks": 6,
                "checks_completed": 0,
                "ttl_minutes": 60,
                "timeout_seconds": 60,
                "max_iterations": 3,
                "allowed_tools": [],
                "turn_kind": "check",
                "write_authorized": False,
                "write_audit": [],
            },
        },
    }


def test_cron_executor_routes_routine_watch_to_routine_path(
    registry: ToolRegistry, fake_provider: _FakeGithubProvider
) -> None:
    fake_provider.set_prs(
        [{"number": 42, "head_sha": "sha-v1", "title": "Add feature X"}]
    )
    manager = _FakeRuntimeManager()
    manager.stage_final_text(
        "<routine_outcome>"
        + json.dumps(
            {
                "reviewed_prs": [
                    {
                        "number": 42,
                        "head_sha_reviewed": "sha-v1",
                        "review_state": "needs_human_review",
                        "summary": "First review.",
                        "findings": [
                            {
                                "file": "x.py",
                                "line": 10,
                                "severity": "warn",
                                "message": "needs tests",
                            }
                        ],
                    }
                ]
            }
        )
        + "</routine_outcome>"
    )
    cron_store = _FakeCronStore()
    executor = _build_executor(
        registry=registry, manager=manager, cron_store=cron_store
    )

    job = _routine_job()
    result = executor.execute(job=job, run={"run_id": "run-1"})

    assert "routine=github_pr_review" in result["summary"]
    assert "artifact=" in result["summary"]
    assert "new_findings=1" in result["summary"]
    assert result["metadata"]["routine_ok"] is True
    assert result["metadata"]["routine_artifact_id"].startswith("artifact://routine/")
    assert result["metadata"]["routine_kept_count"] == 1
    assert result["metadata"]["routine_announced"] is True

    assert manager.submitted_requests, "no agent turn was submitted"
    sent_message = manager.submitted_requests[0].message
    assert '"number":42' in sent_message
    assert '"head_sha":"sha-v1"' in sent_message
    assert "<routine_outcome>" in sent_message
    assert "ReviewOutcomePayloadV1" in sent_message

    assert len(cron_store.replaced) == 1
    persisted_job_id, persisted_payload = cron_store.replaced[0]
    assert persisted_job_id == "job-routine-1"
    routine_after = persisted_payload[WATCH_PAYLOAD_KEY]["routine"]
    assert routine_after["cursor"]["last_review_per_pr"]["42"]["head_sha"] == "sha-v1"
    assert 42 in routine_after["cursor"]["seen_pr_numbers"]
    assert routine_after["cursor"]["consecutive_failures"] == 0


def test_cron_executor_plain_watch_does_not_use_routine_path(
    registry: ToolRegistry, fake_provider: _FakeGithubProvider
) -> None:
    manager = _FakeRuntimeManager()
    manager.stage_final_text("Plain watch model response — no routine trailer.")
    cron_store = _FakeCronStore()
    executor = _build_executor(
        registry=registry, manager=manager, cron_store=cron_store
    )

    job = _plain_watch_job()
    result = executor.execute(job=job, run={"run_id": "run-1"})

    assert "routine=github_pr_review" not in result["summary"]
    assert manager.submitted_requests, "expected an agent turn for the plain watch"
    sent_message = manager.submitted_requests[0].message
    assert '"open_prs"' not in sent_message
    assert "<routine_outcome>" not in sent_message
    for _, payload in cron_store.replaced:
        watch = payload.get(WATCH_PAYLOAD_KEY, {})
        assert "routine" not in watch


def test_cron_executor_records_trailer_missing_error_code(
    registry: ToolRegistry, fake_provider: _FakeGithubProvider
) -> None:
    fake_provider.set_prs([{"number": 1, "head_sha": "abc"}])
    manager = _FakeRuntimeManager()
    manager.stage_final_text("Pure prose. No trailer.")
    cron_store = _FakeCronStore()
    executor = _build_executor(
        registry=registry, manager=manager, cron_store=cron_store
    )

    result = executor.execute(job=_routine_job(), run={"run_id": "run-1"})

    assert result["metadata"]["routine_ok"] is False
    assert result["metadata"]["routine_reason_code"] == "trailer_missing"
    assert "error_code=trailer_missing" in result["summary"]
    assert result["metadata"]["routine_artifact_id"] == ""

    assert len(cron_store.replaced) == 1
    _job_id, payload = cron_store.replaced[0]
    cursor = payload[WATCH_PAYLOAD_KEY]["routine"]["cursor"]
    assert cursor["consecutive_failures"] == 1
