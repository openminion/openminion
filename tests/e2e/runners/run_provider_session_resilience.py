#!/usr/bin/env python3.11
from __future__ import annotations

import argparse
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[3]

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.e2e.cli.focus.harness.provider_matrix import (  # noqa: E402
    load_provider_session_resilience_manifest,
    write_provider_session_resilience_report,
)
from tests.helpers.runtime_roots import isolate_runtime_roots  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    isolate_runtime_roots(prefix="openminion-provider-session-")
    parser = argparse.ArgumentParser(
        prog="run_provider_session_resilience.py",
        description="Validate provider/session resilience manifests.",
    )
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    try:
        manifest = load_provider_session_resilience_manifest(
            manifest_path,
            root=_ROOT,
        )
        json_path, markdown_path = write_provider_session_resilience_report(
            manifest,
            manifest_path=manifest_path,
            root=_ROOT,
            validation_only=args.validate_only,
        )
    except (OSError, ValueError) as exc:
        print(f"provider session resilience manifest rejected: {exc}", file=sys.stderr)
        return 2

    print(f"{manifest.run_id}: {json_path}")
    print(f"{manifest.run_id}: {markdown_path}")
    if not args.validate_only:
        print(
            "live provider/session execution remains owned by PSRC-01",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
