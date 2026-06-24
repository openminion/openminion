"""Held-out pytest runner for authored tools."""

import base64
import re
import sys
from dataclasses import dataclass
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
from ..constants import AUTHORED_TOOL_ERROR_COLLECTION, AUTHORED_TOOL_ERROR_LIMIT

_COUNTS_RE = re.compile(r"(?P<count>\d+)\s+(?P<label>passed|failed|error|errors)")


@dataclass(frozen=True)
class ToolTestRunResult:
    ran: int
    passed: int
    failed: int
    errors: list[dict[str, str]]
    timed_out: bool = False


def run_tool_tests(
    *,
    source_code: str,
    unit_tests_source: str,
    entry_function: str,
    sandbox_runner: Any,
    python_executable: str | None = None,
) -> ToolTestRunResult:
    python_bin = str(python_executable or sys.executable)
    with TemporaryDirectory(prefix="aat-tests-") as tmp_dir:
        workspace = Path(tmp_dir)
        sandbox = ExecutionSandboxSpec(
            workspace_root=str(workspace),
            read_allow=[str(workspace)],
            write_allow=[str(workspace)],
            delete_allow=[str(workspace)],
            cmd_allowlist=[python_bin, Path(python_bin).name, "python", "python3"],
            env_allowlist=["PYTHONDONTWRITEBYTECODE", "PYTHONHASHSEED"],
            timeout_s=TOOL_AUTHORING_TEST_TIMEOUT_S,
            max_output_bytes=TOOL_AUTHORING_TEST_MAX_OUTPUT_BYTES,
            address_space_bytes=TOOL_AUTHORING_TEST_ADDRESS_SPACE_BYTES,
            cpu_seconds=TOOL_AUTHORING_TEST_CPU_SECONDS,
        )
        exec_spec = ExecSpec(
            cmd=[
                python_bin,
                "-c",
                _PYTEST_BOOTSTRAP,
                _b64(source_code),
                _b64(unit_tests_source),
            ],
            cwd=str(workspace),
            env={
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONHASHSEED": "0",
            },
        )
        result = sandbox_runner.run_exec(exec_spec, sandbox)
        return _parse_result(result)


def _parse_result(result: Any) -> ToolTestRunResult:
    if bool(getattr(result, "timed_out", False)):
        return ToolTestRunResult(
            ran=0,
            passed=0,
            failed=0,
            errors=[{"test": "pytest", "message": AUTHORED_TOOL_ERROR_LIMIT}],
            timed_out=True,
        )

    stdout = str(getattr(result, "stdout", "") or "")
    stderr = str(getattr(result, "stderr", "") or "")
    returncode = int(getattr(result, "returncode", 0) or 0)
    combined = "\n".join(part for part in (stdout, stderr) if part)
    counts = {"passed": 0, "failed": 0, "errors": 0}
    for match in _COUNTS_RE.finditer(combined):
        label = str(match.group("label"))
        count = int(match.group("count"))
        if label == "passed":
            counts["passed"] = count
        elif label == "failed":
            counts["failed"] = count
        else:
            counts["errors"] = max(counts["errors"], count)
    if (
        "ERROR collecting" in combined
        or "ImportError" in combined
        or "SyntaxError" in combined
    ):
        counts["errors"] = max(counts["errors"], 1)
        error_message = AUTHORED_TOOL_ERROR_COLLECTION
    else:
        error_message = ""
    ran = counts["passed"] + counts["failed"] + counts["errors"]
    errors: list[dict[str, str]] = []
    if (
        returncode != 0
        and not error_message
        and not counts["failed"]
        and not counts["errors"]
    ):
        error_message = stderr.strip() or stdout.strip() or "pytest failed"
    if error_message:
        errors.append({"test": "pytest", "message": error_message})
    if counts["failed"]:
        errors.append({"test": "pytest", "message": stderr.strip() or stdout.strip()})
    return ToolTestRunResult(
        ran=ran,
        passed=counts["passed"],
        failed=counts["failed"],
        errors=errors,
        timed_out=False,
    )


def _b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


_PYTEST_BOOTSTRAP = """
import base64
import sys
from pathlib import Path
import pytest

work = Path.cwd()
tool_path = work / "tool_impl.py"
tests_path = work / "test_tool_impl.py"
tool_path.write_text(base64.b64decode(sys.argv[1]).decode("utf-8"), encoding="utf-8")
tests_path.write_text(base64.b64decode(sys.argv[2]).decode("utf-8"), encoding="utf-8")
raise SystemExit(pytest.main([str(tests_path), "-q", "--tb=short", "--timeout=10"]))
""".strip()


__all__ = ["ToolTestRunResult", "run_tool_tests"]
