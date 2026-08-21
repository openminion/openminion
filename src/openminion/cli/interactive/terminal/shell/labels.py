from typing import Any

from openminion.cli.presentation.header import format_runtime_label


def _runtime_label(runtime: Any) -> str:
    return format_runtime_label(runtime)
