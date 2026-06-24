"""Compatibility runner module for authored-tool subprocess execution."""

from openminion.modules.tool.authoring.runtime.runner import (
    execute_tool_file,
    main,
)

__all__ = ["execute_tool_file", "main"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
