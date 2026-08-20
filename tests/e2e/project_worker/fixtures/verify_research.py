from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    manifest = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    report = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    source_ids = {source["source_id"] for source in manifest["sources"]}
    report_sources = {source["source_id"] for source in report["source_ledger"]}
    claim = report["claim_ledger"][0]
    assert report_sources == source_ids
    assert claim["supporting_source_ids"] == ["source-alpha"]
    assert claim["contradicting_source_ids"] == ["source-beta"]
    assert claim["disposition"] == "unresolved_conflict"
    assert report["unavailable_source_ids"] == ["source-gamma"]
    assert report["as_of_date"] == manifest["corpus_date"]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
