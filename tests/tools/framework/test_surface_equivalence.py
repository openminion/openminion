from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from typing import Any

from openminion.modules.tool import build_default_tool_registry
from openminion.modules.tool.registry import ToolRegistry

SNAPSHOT_FILE = (
    Path(__file__).resolve().parents[4]
    / "docs"
    / "trackers"
    / "artifacts"
    / "tool-surface"
    / "tool_surface_snapshot.json"
)
UPDATE_ENV_VAR = "OPENMINION_TOOL_SURFACE_UPDATE"

# Snapshot version. Bump only on intentional schema changes to the
# snapshot's own shape (not changes to the *content* — those are
SNAPSHOT_SCHEMA_VERSION = "1"


def _canonical_tool_entry(name: str, spec: Any) -> dict[str, Any]:

    min_scope = getattr(spec, "min_scope", None)
    if min_scope is None:
        # — that's a different shape; we surface `<from-tool-class>` so the
        # snapshot makes the gap visible without faking equivalence.
        min_scope = "<from-tool-class>"

    idempotent = bool(getattr(spec, "idempotent", False))
    dangerous = bool(getattr(spec, "dangerous", False))

    raw_tags = tuple(getattr(spec, "tags", ()) or ())
    # `resolved_capabilities` falls back to tags when capabilities is None,
    # mirroring the runtime's view of the surface.
    if hasattr(spec, "resolved_capabilities"):
        raw_caps = tuple(spec.resolved_capabilities())
    else:
        raw_caps = tuple(getattr(spec, "capabilities", ()) or ())

    return {
        "name": name,
        "min_scope": str(min_scope),
        "idempotent": idempotent,
        "dangerous": dangerous,
        "tags": sorted(raw_tags),
        "capabilities": sorted(raw_caps),
    }


def _capture_surface(registry: ToolRegistry) -> dict[str, Any]:

    tool_entries: dict[str, dict[str, Any]] = {}
    for name, spec in registry.list().items():
        tool_entries[name] = _canonical_tool_entry(name, spec)

    return {
        "_meta": {
            "schema_version": SNAPSHOT_SCHEMA_VERSION,
            "generator": (
                "tests/tools/framework/test_surface_equivalence.py (SCFR-03)"
            ),
            "tool_count": len(tool_entries),
        },
        "tools": dict(sorted(tool_entries.items())),
    }


def _dump_snapshot_text(snapshot: dict[str, Any]) -> str:

    return json.dumps(snapshot, indent=2, sort_keys=True) + "\n"


def _write_snapshot(snapshot: dict[str, Any]) -> None:
    SNAPSHOT_FILE.write_text(_dump_snapshot_text(snapshot), encoding="utf-8")


def _read_snapshot() -> dict[str, Any]:
    return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))


def _is_update_requested() -> bool:
    return os.environ.get(UPDATE_ENV_VAR, "").strip().lower() in {"1", "true", "yes"}


class ToolSurfaceSnapshotTests(unittest.TestCase):
    def setUp(self) -> None:
        # The registry build is mildly expensive; cache per-test-class via
        # `setUp` instead of `setUpClass` to keep the per-test isolation
        # principle while still amortizing across this file's tests.
        self.registry = build_default_tool_registry()
        self.current = _capture_surface(self.registry)

    # Snapshot guard

    def test_tool_surface_matches_snapshot(self) -> None:
        if _is_update_requested():
            _write_snapshot(self.current)
            self.skipTest(
                f"{UPDATE_ENV_VAR}=1 refreshed snapshot at {SNAPSHOT_FILE}. "
                "Review the resulting diff and add a SCFR tracker change-log "
                "entry describing the surface change."
            )

        if not SNAPSHOT_FILE.exists():
            self.fail(
                f"Tool surface snapshot is missing at {SNAPSHOT_FILE}. "
                f"Run with {UPDATE_ENV_VAR}=1 to create it. This is expected "
                "only on the very first run after SCFR-03 lands; after that, "
                "the file is the migration-safety contract."
            )

        expected = _read_snapshot()
        if self.current == expected:
            return

        # Build a readable per-tool diff. assertEqual on nested dicts emits
        # ok diffs for small structures, but the tool count makes a custom
        # narrative more useful.
        added = sorted(set(self.current["tools"]) - set(expected["tools"]))
        removed = sorted(set(expected["tools"]) - set(self.current["tools"]))
        changed: list[str] = []
        for name in sorted(set(self.current["tools"]) & set(expected["tools"])):
            if self.current["tools"][name] != expected["tools"][name]:
                changed.append(name)

        lines: list[str] = [
            "Tool surface drifted from snapshot.",
            f"  added tools ({len(added)}): {added or '(none)'}",
            f"  removed tools ({len(removed)}): {removed or '(none)'}",
            f"  changed tools ({len(changed)}): {changed or '(none)'}",
        ]
        for name in changed:
            lines.append(f"    diff @ {name}:")
            lines.append(f"      expected: {expected['tools'][name]}")
            lines.append(f"      current : {self.current['tools'][name]}")
        meta_changed = self.current["_meta"] != expected["_meta"]
        if meta_changed:
            lines.append(
                f"  meta changed: expected={expected['_meta']} "
                f"current={self.current['_meta']}"
            )
        lines.append(
            f"If the change is intentional, refresh with "
            f"{UPDATE_ENV_VAR}=1 and add a SCFR tracker change-log entry."
        )
        self.fail("\n".join(lines))

    # Sanity checks on the snapshot generator itself

    def test_capture_emits_canonical_field_order_per_tool(self) -> None:
        # The per-tool dict must always carry the documented fields. Pin
        # this so a future framework change can't silently drop a field
        # from the snapshot.
        for name, entry in self.current["tools"].items():
            self.assertIn("name", entry, f"missing 'name' for {name}")
            self.assertIn("min_scope", entry, f"missing 'min_scope' for {name}")
            self.assertIn("idempotent", entry, f"missing 'idempotent' for {name}")
            self.assertIn("dangerous", entry, f"missing 'dangerous' for {name}")
            self.assertIn("tags", entry, f"missing 'tags' for {name}")
            self.assertIn("capabilities", entry, f"missing 'capabilities' for {name}")

    def test_tags_and_capabilities_are_sorted(self) -> None:
        for name, entry in self.current["tools"].items():
            self.assertEqual(
                entry["tags"],
                sorted(entry["tags"]),
                f"tags not sorted for {name}",
            )
            self.assertEqual(
                entry["capabilities"],
                sorted(entry["capabilities"]),
                f"capabilities not sorted for {name}",
            )

    def test_dump_snapshot_text_is_stable_across_calls(self) -> None:
        # Identical snapshot dicts must serialize to identical text — pins
        # the determinism that `sort_keys=True` is supposed to guarantee.
        text_a = _dump_snapshot_text(self.current)
        text_b = _dump_snapshot_text(self.current)
        self.assertEqual(text_a, text_b)
