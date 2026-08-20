from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    handoff = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    assert handoff["schema_version"] == "project-handoff.v1"
    assert handoff["title"]
    assert handoff["owners"] == ["engineering", "operations"]
    assert {item["status"] for item in handoff["items"]} == {"ready", "blocked"}
    assert all(item["source_ref"] for item in handoff["items"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
