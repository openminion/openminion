"""Isolated runtime roots for executable test helpers and child processes."""

from __future__ import annotations

from collections.abc import MutableMapping
import os
from pathlib import Path
import tempfile


def configure_runtime_roots(
    home_root: Path,
    environ: MutableMapping[str, str] | None = None,
) -> Path:
    """Set one OpenMinion runtime-root family under ``home_root``."""
    target = os.environ if environ is None else environ
    resolved_home = home_root.resolve()
    data_root = resolved_home / ".openminion"
    generated_root = data_root / "runtime"
    target["OPENMINION_HOME"] = str(resolved_home)
    target["OPENMINION_DATA_ROOT"] = str(data_root)
    target["OPENMINION_GENERATED_ROOT"] = str(generated_root)
    return generated_root


def isolate_runtime_roots(
    environ: MutableMapping[str, str] | None = None,
    *,
    prefix: str = "openminion-test-",
) -> Path:
    """Replace ambient OpenMinion roots with one system-temporary root set."""
    home_root = Path(tempfile.mkdtemp(prefix=prefix)).resolve()
    return configure_runtime_roots(home_root, environ)
