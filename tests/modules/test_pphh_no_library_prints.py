from __future__ import annotations

import ast
from pathlib import Path


def test_pphh_target_cli_modules_do_not_call_raw_print() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    targets = [
        repo_root / "src" / "openminion" / "modules" / "context" / "cli.py",
        repo_root / "src" / "openminion" / "modules" / "llm" / "cli.py",
    ]
    offenders: list[str] = []
    for path in targets:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "print":
                    offenders.append(f"{path}:{node.lineno}")

    assert offenders == []
