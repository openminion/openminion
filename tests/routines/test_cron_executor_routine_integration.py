from __future__ import annotations

import json
import hashlib
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

import pytest

from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.services.runtime.cron.executor import CronTurnExecutor
from openminion.tools.github.plugin import register as register_github_tools
from openminion.tools.github.providers import (
    provider_registry,
    register_provider,
)
from openminion.tools.fetch.schemas import FetchGetArgs
from openminion.tools.task.constants import WATCH_PAYLOAD_KEY
from openminion.tools.task.routine.schemas import (
    GitHubPrReviewConfigV1,
    RoutinePayloadV1,
)
from openminion.tools.task.routine.social import (
    RssAtomSourceV1,
    SocialSignalConfigV1,
    SocialSignalCursorV1,
)


_ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>tag:example.com,2026:item-1</id>
    <title>OpenMinion release</title>
    <link href="https://example.com/posts/1" />
    <published>2026-09-05T12:00:00Z</published>
    <summary>Stable release.</summary>
  </entry>
</feed>"""


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
    def __init__(
        self,
        *,
        message: str,
        agent_id: str,
        session_id: str,
        meta: dict[str, Any] | None = None,
    ) -> None:
        self.message = message
        self.agent_id = agent_id
        self.session_id = session_id
        self.meta = dict(meta or {})


class _FakeRuntime:
    def __init__(self, *, registry: ToolRegistry, manager: _FakeRuntimeManager) -> None:
        self.tools = registry
        self.runtime_manager = manager


class _FakeCronStore:
    def __init__(self) -> None:
        self.replaced: list[tuple[str, dict]] = []

    def replace_cron_job_payload(self, job_id: str, payload: dict) -> None:
        self.replaced.append((job_id, dict(payload)))


class _FailingCronStore(_FakeCronStore):
    def replace_cron_job_payload(self, job_id: str, payload: dict) -> None:
        del job_id, payload
        raise OSError("storage unavailable")


class _FakeArtifactCtl:
    def __init__(self) -> None:
        self.ingested: list[dict[str, Any]] = []
        self.references: list[tuple[str, str, str]] = []
        self.closed = False

    def ingest_bytes(self, data: bytes, **kwargs: Any) -> Any:
        self.ingested.append({"data": data, **kwargs})
        return type("ArtifactRef", (), {"ref": "artifact://sha256/" + "a" * 64})()

    def ref_add(self, owner_type: str, owner_id: str, ref: str) -> None:
        self.references.append((owner_type, owner_id, ref))

    def close(self) -> None:
        self.closed = True


class _FailingArtifactCtl(_FakeArtifactCtl):
    def ingest_bytes(self, data: bytes, **kwargs: Any) -> Any:
        del data, kwargs
        raise OSError("artifact storage unavailable")


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
    artifactctl: _FakeArtifactCtl | None = None,
) -> CronTurnExecutor:
    runtime = _FakeRuntime(registry=registry, manager=manager)
    return CronTurnExecutor(
        runtime=runtime,
        cron_store=cron_store,
        request_builder=lambda payload, agent_id: _FakeRequest(
            message=payload.get("message", ""),
            agent_id=agent_id,
            session_id=payload.get("session_id") or "sess-iso-1",
            meta=payload.get("meta"),
        ),
        timeout_s=10.0,
        max_attempts=1,
        artifactctl_factory=lambda: artifactctl or _FakeArtifactCtl(),
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


def _social_routine_job() -> dict[str, Any]:
    routine = RoutinePayloadV1(
        routine_kind="social_signal",
        config=SocialSignalConfigV1(
            sources=[
                RssAtomSourceV1(
                    source_id="openminion",
                    label="OpenMinion releases",
                    url="https://example.com/releases.atom",
                    allowed_final_origins=["example.com"],
                )
            ],
            topics=["OpenMinion release activity"],
        ),
        cursor=SocialSignalCursorV1(),
    )
    job = _routine_job(job_id="job-social-1", routine=routine)
    watch = job["payload"][WATCH_PAYLOAD_KEY]
    watch.update(
        {
            "description": "Release watch",
            "check_instruction": "Alert for a significant release.",
            "interval_minutes": 15,
            "stop_on_condition": False,
            "deliver_resolution": True,
        }
    )
    return job


def _register_feed_tool(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name="fetch.get",
            args_model=FetchGetArgs,
            min_scope="READ_ONLY",
            handler=lambda args, ctx: {
                "ok": True,
                "data": {
                    "status_code": 200,
                    "final_url": args["url"],
                    "text_preview": _ATOM,
                    "validators": {"etag": '"v1"'},
                },
            },
        )
    )


def _stage_social_match(manager: _FakeRuntimeManager) -> None:
    evidence_id = (
        "openminion:" + hashlib.sha256(b"tag:example.com,2026:item-1").hexdigest()[:16]
    )
    manager.stage_final_text(
        "<routine_outcome>"
        + json.dumps(
            {
                "schema_version": 1,
                "condition_met": True,
                "summary": "A significant release was published.",
                "evidence_ids": [evidence_id],
                "resolution_evidence_ids": [],
                "limitations": [],
                "conflicts": [],
            }
        )
        + "</routine_outcome>"
    )


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
    artifactctl = _FakeArtifactCtl()
    executor = _build_executor(
        registry=registry,
        manager=manager,
        cron_store=cron_store,
        artifactctl=artifactctl,
    )

    job = _routine_job()
    result = executor.execute(job=job, run={"run_id": "run-1"})

    assert result["summary"].startswith("PR review run for octocat/hello-world")
    assert result["metadata"]["routine_ok"] is True
    assert result["metadata"]["routine_artifact_id"].startswith("artifact://sha256/")
    assert result["artifact_refs"] == [
        {"ref": result["metadata"]["routine_artifact_id"], "role": "output"}
    ]
    assert result["metadata"]["kept_count"] == 1
    assert result["metadata"]["new_findings_count"] == 1
    assert result["output"]["watch_delivery_requested"] is True
    assert artifactctl.closed is True
    assert artifactctl.references[0][:2] == ("session", "watch:job-routine-1")

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


def test_social_routine_uses_shared_finalizer_and_commits_artifact_before_state(
    registry: ToolRegistry,
) -> None:
    _register_feed_tool(registry)
    manager = _FakeRuntimeManager()
    _stage_social_match(manager)
    cron_store = _FakeCronStore()
    artifactctl = _FakeArtifactCtl()
    executor = _build_executor(
        registry=registry,
        manager=manager,
        cron_store=cron_store,
        artifactctl=artifactctl,
    )

    result = executor.execute(job=_social_routine_job(), run={"run_id": "run-1"})

    assert result["metadata"]["routine_kind"] == "social_signal"
    assert result["metadata"]["routine_ok"] is True
    assert result["output"]["watch_delivery_requested"] is True
    assert result["output"]["watch_terminal"] is False
    assert artifactctl.ingested
    assert len(cron_store.replaced) == 1
    persisted = cron_store.replaced[0][1][WATCH_PAYLOAD_KEY]
    assert persisted["checks_completed"] == 1
    assert persisted["last_condition_met"] is True
    assert persisted["routine"]["cursor"]["sources"]["openminion"]["etag"] == '"v1"'
    assert manager.submitted_requests[0].meta["watch_allowed_tools"] == ""


def test_social_routine_does_not_advance_state_when_artifact_write_fails(
    registry: ToolRegistry,
) -> None:
    _register_feed_tool(registry)
    manager = _FakeRuntimeManager()
    _stage_social_match(manager)
    cron_store = _FakeCronStore()
    artifactctl = _FailingArtifactCtl()
    executor = _build_executor(
        registry=registry,
        manager=manager,
        cron_store=cron_store,
        artifactctl=artifactctl,
    )

    result = executor.execute(job=_social_routine_job(), run={"run_id": "run-1"})

    assert result["error"] is True
    assert (
        result["summary"]
        == "routine artifact write failed: artifact storage unavailable"
    )
    assert cron_store.replaced == []
    assert artifactctl.closed is True


def test_social_routine_suppresses_delivery_when_state_write_fails(
    registry: ToolRegistry,
) -> None:
    _register_feed_tool(registry)
    manager = _FakeRuntimeManager()
    _stage_social_match(manager)
    artifactctl = _FakeArtifactCtl()
    executor = _build_executor(
        registry=registry,
        manager=manager,
        cron_store=_FailingCronStore(),
        artifactctl=artifactctl,
    )

    result = executor.execute(job=_social_routine_job(), run={"run_id": "run-1"})

    assert result["error"] is True
    assert result["output"]["watch_delivery_requested"] is False
    assert "watch progress was not persisted" in result["summary"]
    assert artifactctl.ingested


def test_expired_social_routine_does_not_fetch_or_run_model(
    registry: ToolRegistry,
) -> None:
    manager = _FakeRuntimeManager()
    cron_store = _FakeCronStore()
    executor = _build_executor(
        registry=registry, manager=manager, cron_store=cron_store
    )
    job = _social_routine_job()
    job["created_at"] = "2020-01-01T00:00:00Z"

    result = executor.execute(job=job, run={"run_id": "run-1"})

    assert result["output"]["watch_terminal_reason"] == "ttl_expired"
    assert manager.submitted_requests == []
    assert cron_store.replaced == []


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


def test_cron_executor_refuses_unknown_routine_without_running_generic_watch(
    registry: ToolRegistry,
) -> None:
    manager = _FakeRuntimeManager()
    cron_store = _FakeCronStore()
    executor = _build_executor(
        registry=registry, manager=manager, cron_store=cron_store
    )
    job = _routine_job()
    job["payload"][WATCH_PAYLOAD_KEY]["routine"] = {
        "routine_kind": "unknown",
        "config": {},
        "cursor": {},
    }

    result = executor.execute(job=job, run={"run_id": "run-1"})

    assert result == {
        "summary": "routine payload is invalid or unsupported",
        "error": True,
    }
    assert manager.submitted_requests == []
