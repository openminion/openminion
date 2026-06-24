from __future__ import annotations

import json

from tests.brain.loop.asc_closeout_smoke import run_closeout


def test_asc_closeout_smoke_writes_passing_artifact() -> None:
    path = run_closeout()
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["decision"] == "promote_to_qa"
    assert payload["event_count"] >= 1
    assert all(bool(value) for value in payload["checks"].values())
