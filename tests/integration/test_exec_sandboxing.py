from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess

import pytest

from openminion.modules.runtime.sandboxes.daytona import (
    DaytonaClient,
    DaytonaConfig,
    DaytonaRunner,
    DaytonaSdkTransport,
)
from tests.tool.authoring._helpers import FakePolicyCtl, build_service


def test_real_daytona_exec_sandboxing_smoke(tmp_path) -> None:
    endpoint = str(os.getenv("OPENMINION_TEST_DAYTONA_ENDPOINT", "")).strip()
    api_key = str(os.getenv("OPENMINION_TEST_DAYTONA_API_KEY", "")).strip()
    if not endpoint or not api_key:
        pytest.skip(
            "Set OPENMINION_TEST_DAYTONA_ENDPOINT and OPENMINION_TEST_DAYTONA_API_KEY to run the real Daytona sandbox integration smoke."
        )

    transport = DaytonaSdkTransport()
    runner = DaytonaRunner(
        client=DaytonaClient(
            config=DaytonaConfig(endpoint=endpoint, api_key=api_key),
            transport=transport,
        )
    )
    source_code = "def add(a, b):\n    return a + b\n"
    unit_tests_source = (
        "from tool_impl import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
    )
    service = build_service(
        tmp_path,
        sandbox_runner=runner,
        policy_ctl=FakePolicyCtl(),
    )
    try:
        draft = service.author_draft(
            {
                "name": "add",
                "description": "Add two integers",
                "source_code": source_code,
                "unit_tests_source": unit_tests_source,
                "args_schema": {
                    "type": "object",
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                    },
                    "required": ["a", "b"],
                },
                "returns_schema": {"type": "integer"},
                "requirements": [],
                "dependencies": [],
                "proposed_scope_tier": "POWER_USER",
            }
        )
        inspected = service.inspect_draft(
            {"draft_id": draft["draft_id"], "run_tests": True}
        )
        registered = service.register_draft(
            {"draft_id": draft["draft_id"]}, agent_id="daytona-live-smoke"
        )
        invoked = service.invoke(registered["tool_name"], {"a": 2, "b": 3})
    finally:
        service.close()
        runner.close()

    assert inspected["recommend_register"] is True
    assert inspected["test_results"] == {
        "ran": 1,
        "passed": 1,
        "failed": 0,
        "errors": [],
    }
    assert registered["ok"] is True
    assert invoked["ok"] is True
    assert invoked["data"]["result"] == 5
    assert transport._workspaces == {}  # noqa: SLF001

    receipt_path = str(os.getenv("OPENMINION_TEST_DAYTONA_RECEIPT", "")).strip()
    if receipt_path:
        repository_root = Path(__file__).resolve().parents[2]
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=repository_root,
            text=True,
        ).strip()
        assert not dirty, "live Daytona acceptance receipt requires a clean worktree"
        Path(receipt_path).write_text(
            json.dumps(
                {
                    "cleanup": True,
                    "failed": 0,
                    "image": runner._client.config.default_workspace_image,  # noqa: SLF001
                    "invoked_result": 5,
                    "passed": 1,
                    "ran": 1,
                    "source_revision": subprocess.check_output(
                        ["git", "rev-parse", "HEAD"],
                        cwd=repository_root,
                        text=True,
                    ).strip(),
                    "test": "test_real_daytona_exec_sandboxing_smoke",
                    "timestamp": datetime.now(UTC).isoformat(),
                    "tool_name": registered["tool_name"],
                    "version_hash": inspected["version_hash"],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
