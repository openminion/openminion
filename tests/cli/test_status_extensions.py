from __future__ import annotations

import io
import json
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path

from openminion.base.config import OpenMinionConfig, save_config
from openminion.cli.commands.status import run_status
from tests._csc_fixtures import _csc_install_default_agent


def _write_config(tmp_path: Path) -> Path:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config_path = tmp_path / "config.json"
    save_config(config, str(config_path))
    return config_path


def test_status_extensions_json(tmp_path: Path) -> None:
    args = Namespace(
        config=str(_write_config(tmp_path)),
        status_command="extensions",
        json=True,
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = run_status(args)

    assert code == 0
    payload = json.loads(buf.getvalue())
    assert payload["ok"] is True
    assert "catalog" in payload
    assert "providers" in payload
    assert isinstance(payload["plugins"], list)
