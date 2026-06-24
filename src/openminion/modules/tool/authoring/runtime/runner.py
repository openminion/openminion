"""Subprocess entrypoint for executing one authored tool function."""

import argparse
import json
from pathlib import Path
import sys
from typing import Any


def execute_tool_file(
    *,
    tool_file: str,
    entry_function: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    namespace: dict[str, Any] = {}
    source = Path(tool_file).read_text(encoding="utf-8")
    exec(compile(source, tool_file, "exec"), namespace)
    fn = namespace.get(entry_function)
    if not callable(fn):
        raise RuntimeError(  # allow-bare-raise: subprocess entrypoint surfaces stderr text only
            f"entry function not callable: {entry_function}"
        )
    result = fn(**arguments)
    return {"ok": True, "result": result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="authored-tool-runner")
    parser.add_argument("--tool-file", required=True)
    parser.add_argument("--entry-function", required=True)
    parser.add_argument("--args-json", default=None)
    args = parser.parse_args(argv)

    raw_args = args.args_json
    if raw_args is None:
        raw_args = input()
    payload = json.loads(str(raw_args or "{}"))
    if not isinstance(payload, dict):
        raise SystemExit(2)
    result = execute_tool_file(
        tool_file=str(args.tool_file),
        entry_function=str(args.entry_function),
        arguments=dict(payload),
    )
    sys.stdout.write(json.dumps(result, ensure_ascii=True))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
