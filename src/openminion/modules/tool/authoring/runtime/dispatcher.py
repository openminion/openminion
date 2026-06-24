"""Subprocess dispatcher for registered authored tools."""

import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from openminion.base.runtime.sandbox import ExecSpec, ExecutionSandboxSpec

from ..config import (
    TOOL_AUTHORING_TEST_ADDRESS_SPACE_BYTES,
    TOOL_AUTHORING_TEST_CPU_SECONDS,
    TOOL_AUTHORING_TEST_MAX_OUTPUT_BYTES,
    TOOL_AUTHORING_TEST_TIMEOUT_S,
)
from ..constants import (
    AUTHORED_TOOL_ERROR_FAILED,
    AUTHORED_TOOL_ERROR_LIMIT,
    AUTHORED_TOOL_ERROR_NOT_FOUND,
    AUTHORED_TOOL_ERROR_REMOVED,
)
from ..schemas import AuthoredToolRow


class AuthoredToolDispatcher:
    """Dispatch frozen authored tools through the shared sandbox runner."""

    def __init__(self, *, store: Any, sandbox_runner: Any) -> None:
        self._store = store
        self._sandbox_runner = sandbox_runner

    def invoke(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        row = self._store.get_authored_tool(tool_name)
        if row is None:
            return {
                "ok": False,
                "error": {"code": AUTHORED_TOOL_ERROR_NOT_FOUND, "message": tool_name},
            }
        if row.removed_at is not None:
            return {
                "ok": False,
                "error": {"code": AUTHORED_TOOL_ERROR_REMOVED, "message": tool_name},
            }
        return _run_authored_tool(
            row=row, args=args, sandbox_runner=self._sandbox_runner
        )


def _run_authored_tool(
    *,
    row: AuthoredToolRow,
    args: dict[str, Any],
    sandbox_runner: Any,
) -> dict[str, Any]:
    with TemporaryDirectory(prefix="aat-dispatch-") as tmp_dir:
        workspace = Path(tmp_dir)
        tool_file = workspace / "tool_impl.py"
        tool_file.write_text(row.source_code, encoding="utf-8")
        sandbox = ExecutionSandboxSpec(
            workspace_root=str(workspace),
            read_allow=[str(workspace)],
            write_allow=[str(workspace)],
            delete_allow=[str(workspace)],
            cmd_allowlist=[
                sys.executable,
                os.path.basename(sys.executable),
                "python",
                "python3",
            ],
            env_allowlist=["PYTHONDONTWRITEBYTECODE", "PYTHONHASHSEED"],
            timeout_s=TOOL_AUTHORING_TEST_TIMEOUT_S,
            max_output_bytes=TOOL_AUTHORING_TEST_MAX_OUTPUT_BYTES,
            address_space_bytes=TOOL_AUTHORING_TEST_ADDRESS_SPACE_BYTES,
            cpu_seconds=TOOL_AUTHORING_TEST_CPU_SECONDS,
        )
        exec_spec = ExecSpec(
            cmd=[
                sys.executable,
                "-m",
                "openminion.tools.tool_authoring.runner",
                "--tool-file",
                str(tool_file),
                "--entry-function",
                row.local_name,
                "--args-json",
                json.dumps(args, ensure_ascii=True),
            ],
            cwd=str(workspace),
            env={
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
            },
        )
        result = sandbox_runner.run_exec(exec_spec, sandbox)
        if bool(getattr(result, "timed_out", False)):
            return {
                "ok": False,
                "error": {
                    "code": AUTHORED_TOOL_ERROR_LIMIT,
                    "message": "execution timed out",
                },
            }
        if int(getattr(result, "returncode", 0) or 0) != 0:
            stderr = str(getattr(result, "stderr", "") or "").strip()
            stdout = str(getattr(result, "stdout", "") or "").strip()
            tail = stderr or stdout or "authored tool failed"
            return {
                "ok": False,
                "error": {"code": AUTHORED_TOOL_ERROR_FAILED, "message": tail},
            }
        payload = json.loads(str(getattr(result, "stdout", "") or "{}"))
        if isinstance(payload, dict) and ("ok" in payload or "content" in payload):
            return payload
        result_value = payload.get("result") if isinstance(payload, dict) else payload
        return {
            "ok": True,
            "content": json.dumps(result_value, ensure_ascii=True, sort_keys=True),
            "data": {"result": result_value},
        }


__all__ = ["AuthoredToolDispatcher"]
