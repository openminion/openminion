#!/usr/bin/env python3.11
"""Validate BrainRunner delegate-map keys against static callsites."""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.terminal_output import emit_json_report  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
DELEGATES_PATH = REPO_ROOT / "src/openminion/modules/brain/runner/delegates.py"
SCAN_ROOTS = (
    REPO_ROOT / "src/openminion",
    REPO_ROOT / "tests",
)


@dataclass(frozen=True)
class Callsite:
    path: str
    line: int
    kind: str

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "line": self.line, "kind": self.kind}


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def runner_delegate_keys(path: Path = DELEGATES_PATH) -> set[str]:
    tree = _parse(path)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "RUNNER_DELEGATES"
            and isinstance(node.value, ast.Dict)
        ):
            return {
                key.value
                for key in node.value.keys
                if isinstance(key, ast.Constant) and isinstance(key.value, str)
            }
    raise RuntimeError(f"RUNNER_DELEGATES mapping not found in {path}")


def _python_files(roots: tuple[Path, ...]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if root.exists():
            files.extend(sorted(root.rglob("*.py")))
    return files


def runner_delegate_calls(
    *,
    keys: set[str],
    roots: tuple[Path, ...] = SCAN_ROOTS,
    repo_root: Path = REPO_ROOT,
) -> tuple[dict[str, list[Callsite]], list[Callsite]]:
    calls: dict[str, list[Callsite]] = {}
    dynamic_calls: list[Callsite] = []
    for path in _python_files(roots):
        tree = _parse(path)
        rel_path = str(path.relative_to(repo_root))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "_runner_delegate":
                if (
                    node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    name = node.args[0].value
                    calls.setdefault(name, []).append(
                        Callsite(path=rel_path, line=node.lineno, kind="string")
                    )
                else:
                    dynamic_calls.append(
                        Callsite(path=rel_path, line=node.lineno, kind="dynamic")
                    )
                continue
            if isinstance(node.func, ast.Attribute) and node.func.attr in keys:
                calls.setdefault(node.func.attr, []).append(
                    Callsite(path=rel_path, line=node.lineno, kind="attribute")
                )
                continue
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in {"getattr", "hasattr", "setattr"}
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and node.args[1].value in keys
            ):
                calls.setdefault(node.args[1].value, []).append(
                    Callsite(path=rel_path, line=node.lineno, kind=node.func.id)
                )
                continue
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "object"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "patch"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
                and node.args[1].value in keys
            ):
                calls.setdefault(node.args[1].value, []).append(
                    Callsite(path=rel_path, line=node.lineno, kind="patch.object")
                )
    return calls, dynamic_calls


def validate(
    *,
    delegates_path: Path = DELEGATES_PATH,
    roots: tuple[Path, ...] = SCAN_ROOTS,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    keys = runner_delegate_keys(delegates_path)
    calls, dynamic_calls = runner_delegate_calls(
        keys=keys,
        roots=roots,
        repo_root=repo_root,
    )
    undefined = sorted(set(calls) - keys)
    unused = sorted(keys - set(calls))
    return {
        "validator": "validate_runner_delegates",
        "ok": not undefined and not unused and not dynamic_calls,
        "metrics": {
            "delegate_keys": len(keys),
            "used_delegate_keys": len(calls),
            "callsite_count": sum(len(value) for value in calls.values()),
        },
        "undefined": undefined,
        "unused": unused,
        "dynamic_calls": [call.as_dict() for call in dynamic_calls],
    }


def main(argv: list[str] | None = None) -> int:
    args = list(argv or [])
    if args:
        print("validate_runner_delegates: no arguments are supported.", file=sys.stderr)
        return 2
    result = validate()
    findings: list[str] = []
    findings.extend(f"undefined delegate key: {name}" for name in result["undefined"])
    findings.extend(f"unused delegate key: {name}" for name in result["unused"])
    findings.extend(
        f"dynamic delegate call: {call['path']}:{call['line']} ({call['kind']})"
        for call in result["dynamic_calls"]
    )
    metrics = result["metrics"]
    emit_json_report(
        "validate_runner_delegates",
        result,
        summary=(
            ("delegate keys", metrics["delegate_keys"]),
            ("used delegate keys", metrics["used_delegate_keys"]),
            ("callsite count", metrics["callsite_count"]),
        ),
        findings=findings,
        ok_message="runner delegate map matches static consumers.",
        report_stream=sys.stderr,
        json_stream=sys.stdout,
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
