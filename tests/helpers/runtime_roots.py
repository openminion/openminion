"""Isolated runtime roots for executable test helpers and child processes."""

from __future__ import annotations

from collections.abc import MutableMapping
import os
from pathlib import Path
import tempfile


def isolate_runtime_roots(
    environ: MutableMapping[str, str] | None = None,
    *,
    prefix: str = "openminion-test-",
) -> Path:
    """Replace ambient OpenMinion roots with one system-temporary root set."""
    target = os.environ if environ is None else environ
    home_root = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    data_root = home_root / ".openminion"
    generated_root = data_root / "runtime"
    target["OPENMINION_HOME"] = str(home_root)
    target["OPENMINION_DATA_ROOT"] = str(data_root)
    target["OPENMINION_GENERATED_ROOT"] = str(generated_root)
    return generated_root
