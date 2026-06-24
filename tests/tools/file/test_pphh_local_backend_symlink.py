from __future__ import annotations

import os

import pytest

from openminion.modules.tool.errors import ToolRuntimeError
from openminion.tools.file.backends.local import LocalStorageBackend


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
def test_local_backend_read_refuses_symlink_leaf(tmp_path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link = workspace / "safe.txt"
    link.symlink_to(outside)

    backend = LocalStorageBackend(workspace)

    with pytest.raises(ToolRuntimeError) as excinfo:
        backend.read(str(link))

    assert excinfo.value.code == "POLICY_DENIED"


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
def test_local_backend_write_refuses_symlink_leaf(tmp_path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link = workspace / "safe.txt"
    link.symlink_to(outside)

    backend = LocalStorageBackend(workspace)

    with pytest.raises(ToolRuntimeError) as excinfo:
        backend.write(str(link), "overwrite")

    assert excinfo.value.code == "POLICY_DENIED"
    assert outside.read_text(encoding="utf-8") == "secret"
