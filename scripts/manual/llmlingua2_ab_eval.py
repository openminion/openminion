"""Measure llmlingua2 against the extractive compression fixtures."""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Make the test module's fixture and measurement helpers importable.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "tests"))

from compress.eval.test_llmlingua2_vs_extractive_eval import (  # noqa: E402
    _FIXTURES,
    _POLICY,
    _QUERY,
    _measure_extractive,
    _measure_llmlingua2,
    _summarize,
)
from openminion.base.generated_paths import resolve_generated_root  # noqa: E402


def main() -> int:
    try:
        import llmlingua  # noqa: F401
    except ImportError as exc:
        print(f"llmlingua not installed: {exc}", file=sys.stderr)
        return 1

    eval_root = resolve_generated_root() / "compression-eval"
    eval_root.mkdir(parents=True, exist_ok=True)

    rows = []
    for name, factory in _FIXTURES.items():
        blocks = factory()
        rows.append(
            {
                "fixture": name,
                "extractive": _measure_extractive(blocks),
                "llmlingua2": _measure_llmlingua2(blocks),
            }
        )

    artifact = {
        "policy_target_ratio": _POLICY.target_ratio,
        "query": _QUERY,
        "rows": rows,
        "conclusion": _summarize(rows),
    }
    artifact_path = eval_root / "llmlingua2_vs_extractive.json"
    artifact_path.write_text(json.dumps(artifact, indent=2))
    print(f"wrote: {artifact_path}")
    print(f"conclusion: {artifact['conclusion']}")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual entrypoint
    raise SystemExit(main())
