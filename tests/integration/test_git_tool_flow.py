from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace

from openminion.modules.llm.providers.envelope_v2 import CONTRACT_VERSION_V2
from openminion.modules.tool.base import ToolExecutionContext
from openminion.modules.tool.registry import ToolRegistry
from openminion.modules.tool.runtime.registry_toolspec import execute_tool_spec_call
from openminion.services.agent.service import AgentService
from openminion.services.brain.post_execution.postprocess import (
    _attach_tool_result_metadata,
)
from openminion.tools.git import register

_GIT = shutil.which("git")


def _run(cmd: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - test harness with explicit argv
        cmd,
        cwd=str(cwd),
        check=True,
        capture_output=True,
        text=True,
    )


def _make_fixture_repo(root: Path) -> None:
    _run([_GIT, "init", "-q", "-b", "main"], cwd=root)
    _run([_GIT, "config", "user.email", "test@example.com"], cwd=root)
    _run([_GIT, "config", "user.name", "Test User"], cwd=root)
    _run([_GIT, "config", "commit.gpgsign", "false"], cwd=root)
    (root / "README.md").write_text("hello world\n", encoding="utf-8")
    _run([_GIT, "add", "README.md"], cwd=root)
    _run([_GIT, "commit", "-q", "-m", "initial commit"], cwd=root)


def _registry() -> ToolRegistry:
    registry = ToolRegistry([])
    register(registry)
    return registry


def _context(workspace: Path) -> ToolExecutionContext:
    return ToolExecutionContext(
        channel="console",
        target="tests",
        session_id="ngt07-session",
        metadata={"workspace_root": str(workspace)},
    )


def _execute(
    *,
    registry: ToolRegistry,
    workspace: Path,
    tool_name: str,
    arguments: dict[str, object],
):
    tool = registry.list()[tool_name]
    result = execute_tool_spec_call(
        tool=tool,
        arguments=arguments,
        context=_context(workspace),
    )
    assert result.ok is True, f"{tool_name} failed: {result.error}"
    return result


def _assert_v2_metadata(results: list[object]) -> None:
    service = AgentService.__new__(AgentService)
    batch = SimpleNamespace(
        results=results,
        all_verified=all(getattr(result, "verified", False) for result in results),
    )
    batch_metadata = service._tool_batch_metadata(
        batch=batch,
        tool_calls_count=len(results),
    )
    assert batch_metadata["tool_contract_version"] == CONTRACT_VERSION_V2

    metadata: dict[str, str] = {}
    _attach_tool_result_metadata(
        None,
        metadata=metadata,
        tool_results_payload=[
            {
                "tool_name": result.tool_name,
                "ok": result.ok,
                "verified": result.verified,
                "data": result.data,
                "call_id": result.call_id,
            }
            for result in results
        ],
        termination_reason="tool_final",
    )
    assert metadata["tool_contract_version"] == CONTRACT_VERSION_V2


def test_status_add_commit_log_round_trip_emits_v2_metadata() -> None:
    if _GIT is None:
        return
    workspace = Path(tempfile.mkdtemp(prefix="ngt07-status-commit-"))
    try:
        _make_fixture_repo(workspace)
        (workspace / "README.md").write_text(
            "hello world\nnative git tools\n",
            encoding="utf-8",
        )
        registry = _registry()
        results = [
            _execute(
                registry=registry,
                workspace=workspace,
                tool_name="git.status",
                arguments={"path": "."},
            ),
            _execute(
                registry=registry,
                workspace=workspace,
                tool_name="git.add",
                arguments={"paths": ["README.md"]},
            ),
            _execute(
                registry=registry,
                workspace=workspace,
                tool_name="git.commit",
                arguments={"message": "ngt07 integration commit"},
            ),
            _execute(
                registry=registry,
                workspace=workspace,
                tool_name="git.log",
                arguments={"limit": 2},
            ),
        ]
        commits = results[-1].data["parsed"]
        assert commits[0]["subject"] == "ngt07 integration commit"
        _assert_v2_metadata(results)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def test_branch_checkout_commit_checkout_main_round_trip_emits_v2_metadata() -> None:
    if _GIT is None:
        return
    workspace = Path(tempfile.mkdtemp(prefix="ngt07-branch-checkout-"))
    try:
        _make_fixture_repo(workspace)
        registry = _registry()
        results = [
            _execute(
                registry=registry,
                workspace=workspace,
                tool_name="git.branch",
                arguments={"action": "create", "name": "agent-test"},
            ),
            _execute(
                registry=registry,
                workspace=workspace,
                tool_name="git.checkout",
                arguments={"ref": "agent-test"},
            ),
        ]
        (workspace / "README.md").write_text(
            "hello world\nbranch change\n",
            encoding="utf-8",
        )
        results.extend(
            [
                _execute(
                    registry=registry,
                    workspace=workspace,
                    tool_name="git.add",
                    arguments={"paths": ["README.md"]},
                ),
                _execute(
                    registry=registry,
                    workspace=workspace,
                    tool_name="git.commit",
                    arguments={"message": "branch flow commit"},
                ),
                _execute(
                    registry=registry,
                    workspace=workspace,
                    tool_name="git.checkout",
                    arguments={"ref": "main"},
                ),
                _execute(
                    registry=registry,
                    workspace=workspace,
                    tool_name="git.branch",
                    arguments={"action": "list"},
                ),
            ]
        )
        branches = {
            entry["name"]: entry["is_current"]
            for entry in results[-1].data["parsed"]["branches"]
        }
        assert branches["main"] is True
        assert branches["agent-test"] is False
        _assert_v2_metadata(results)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
