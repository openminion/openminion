#!/usr/bin/env python3
"""Validate `asyncio.run(...)` stays at sync/process boundaries."""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

REPO_IMPORT_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_IMPORT_ROOT))

from scripts.common.asyncio_calls import is_asyncio_run_call, load_python_module  # noqa: E402

# Files allowed to contain `asyncio.run(...)`; paths are relative to
# `openminion/src/`.
_ALLOWLISTED_FILES: frozenset[str] = frozenset(
    {
        # CLI/process boundary (keep indefinitely)
        "openminion/cli/chat/commands/context.py",
        "openminion/cli/commands/agent.py",
        "openminion/cli/commands/agent_check.py",
        "openminion/cli/commands/doctor.py",
        "openminion/cli/commands/gateway.py",
        "openminion/cli/tui/terminal/overlays.py",
        "openminion/cli/tui/terminal/shell/__init__.py",
        "openminion/modules/controlplane/adapters/client.py",
        "openminion/modules/telemetry/cli.py",
        "openminion/modules/llm/cli.py",
        # Canonical sync/async bridge.
        "openminion/modules/llm/runtime/sync.py",
        # Defensive wrappers that prove no running loop before `asyncio.run`.
        "openminion/services/brain/client.py",
        "openminion/services/runtime/ingress/__init__.py",
        "openminion/services/runtime/ingress/gateway_call.py",
        "openminion/services/runtime/ingress/timing.py",
        "openminion/modules/brain/loop/recursive/llm.py",
        "openminion/modules/controlplane/runtime/dispatcher.py",
        "openminion/modules/telemetry/module_events.py",
        "openminion/modules/telemetry/events/module.py",
    }
)


def _file_uses_asyncio_run(path: Path) -> bool:
    loaded = load_python_module(path)
    if loaded is None:
        return False
    _source, tree = loaded
    for node in ast.walk(tree):
        if is_asyncio_run_call(node):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate `asyncio.run(...)` usage stays at approved boundaries.",
    )
    parser.add_argument(
        "--list-allowlisted",
        action="store_true",
        help="Print the allowlist and exit",
    )
    args = parser.parse_args(argv)

    if args.list_allowlisted:
        for path in sorted(_ALLOWLISTED_FILES):
            print(path)
        return 0

    repo_root = Path(__file__).resolve().parents[2]
    src_root = repo_root / "src"
    if not src_root.is_dir():
        print(f"src/ not found at {src_root}", file=sys.stderr)
        return 1

    violations: list[str] = []
    for py_file in sorted(src_root.rglob("*.py")):
        if not _file_uses_asyncio_run(py_file):
            continue
        rel_path = str(py_file.relative_to(src_root))
        if rel_path.endswith("/__main__.py"):
            continue
        if rel_path in _ALLOWLISTED_FILES:
            continue
        violations.append(rel_path)

    if violations:
        print(
            "asyncio.run boundary violations "
            f"({len(violations)} file(s) outside allowlist):",
            file=sys.stderr,
        )
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        print(
            "\nRemediation:\n"
            "  1. If the file is a legitimate CLI/process boundary, add it to\n"
            "     `_ALLOWLISTED_FILES` in this script with a category comment.\n"
            "  2. Otherwise, rewrite the call site to use the canonical sync\n"
            "     bridge or a true async caller path.",
            file=sys.stderr,
        )
        return 1

    print(
        "[asyncio-run-boundary] clean — "
        f"{len(_ALLOWLISTED_FILES)} allowlisted file(s), 0 violations"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
