import json
import tempfile
import unittest
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.services.lifecycle.self_improvement import (
    ImprovementNote,
    SelfImprovementEngine,
)
from openminion.modules.tool.base import ToolExecutionResult
from tests._csc_fixtures import _csc_install_default_agent


class SelfImprovementEngineTests(unittest.TestCase):
    def test_capture_promotes_note_after_threshold_and_writes_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.storage.path = str(Path(tmp) / "state" / "openminion.db")
            config.self_improvement.notes_path = str(Path(tmp) / "notes")
            config.self_improvement.activation_threshold = 2
            engine = SelfImprovementEngine.from_config(config)

            tool_result = ToolExecutionResult(
                tool_name="weather.openmeteo.current",
                ok=False,
                verified=False,
                content="",
                error="missing city argument",
            )

            captured_first = engine.capture_tool_failures(
                agent_id="openminion",
                user_message="check weather in san francisco",
                tool_results=[tool_result],
            )
            self.assertEqual(len(captured_first), 1)
            notes_after_first = engine.list_notes(agent_id="openminion")
            self.assertEqual(len(notes_after_first), 1)
            self.assertEqual(notes_after_first[0].status, "candidate")
            self.assertEqual(notes_after_first[0].occurrence_count, 1)

            captured_second = engine.capture_tool_failures(
                agent_id="openminion",
                user_message="check weather in san francisco",
                tool_results=[tool_result],
            )
            self.assertEqual(captured_second, captured_first)
            notes_after_second = engine.list_notes(agent_id="openminion")
            self.assertEqual(notes_after_second[0].status, "active")
            self.assertEqual(notes_after_second[0].occurrence_count, 2)

            note_files = list(Path(config.self_improvement.notes_path).glob("*.md"))
            self.assertTrue(note_files)

    def test_find_notes_for_context_matches_active_notes_by_structural_tool_tag(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.storage.path = str(Path(tmp) / "state" / "openminion.db")
            config.self_improvement.notes_path = str(Path(tmp) / "notes")
            engine = SelfImprovementEngine.from_config(config)

            engine._write_index(  # noqa: SLF001
                {
                    "agent-a::note-a": ImprovementNote(
                        agent_id="agent-a",
                        signature="note-a",
                        status="active",
                        source="tool_failure",
                        context="ctx",
                        guidance="Validate weather args before retrying.",
                        trigger_tokens=("weather", "city"),
                        tags=("tool:weather-openmeteo-current", "error:missing-city"),
                        occurrence_count=2,
                        apply_count=0,
                        created_at="2026-05-08T00:00:00+00:00",
                        updated_at="2026-05-08T00:00:01+00:00",
                    ).to_dict()
                }
            )

            matched = engine.find_notes_for_context(
                agent_id="agent-a",
                tool_names=("weather.openmeteo.current",),
            )

            self.assertEqual([note.signature for note in matched], ["note-a"])

    def test_find_notes_for_context_respects_agent_scope_and_status_filter(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.storage.path = str(Path(tmp) / "state" / "openminion.db")
            config.self_improvement.notes_path = str(Path(tmp) / "notes")
            engine = SelfImprovementEngine.from_config(config)

            engine._write_index(  # noqa: SLF001
                {
                    "agent-a::active-note": ImprovementNote(
                        agent_id="agent-a",
                        signature="active-note",
                        status="active",
                        source="tool_failure",
                        context="ctx",
                        guidance="Use the current file path.",
                        trigger_tokens=("file",),
                        tags=("tool:file-read",),
                        occurrence_count=3,
                        apply_count=0,
                        created_at="2026-05-08T00:00:00+00:00",
                        updated_at="2026-05-08T00:00:02+00:00",
                    ).to_dict(),
                    "agent-a::candidate-note": ImprovementNote(
                        agent_id="agent-a",
                        signature="candidate-note",
                        status="candidate",
                        source="tool_failure",
                        context="ctx",
                        guidance="Candidate note.",
                        trigger_tokens=("file",),
                        tags=("tool:file-read",),
                        occurrence_count=1,
                        apply_count=0,
                        created_at="2026-05-08T00:00:00+00:00",
                        updated_at="2026-05-08T00:00:03+00:00",
                    ).to_dict(),
                    "agent-b::other-agent": ImprovementNote(
                        agent_id="agent-b",
                        signature="other-agent",
                        status="active",
                        source="tool_failure",
                        context="ctx",
                        guidance="Other agent note.",
                        trigger_tokens=("file",),
                        tags=("tool:file-read",),
                        occurrence_count=2,
                        apply_count=0,
                        created_at="2026-05-08T00:00:00+00:00",
                        updated_at="2026-05-08T00:00:04+00:00",
                    ).to_dict(),
                }
            )

            default_matched = engine.find_notes_for_context(
                agent_id="agent-a",
                tool_names=("file.read",),
            )
            all_status_matched = engine.find_notes_for_context(
                agent_id="agent-a",
                tool_names=("file.read",),
                status_filter=("active", "candidate"),
            )

            self.assertEqual(
                [note.signature for note in default_matched], ["active-note"]
            )
            self.assertEqual(
                [note.signature for note in all_status_matched],
                ["candidate-note", "active-note"],
            )

    def test_find_notes_for_context_ranks_by_error_match_then_updated_at_then_signature(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.storage.path = str(Path(tmp) / "state" / "openminion.db")
            config.self_improvement.notes_path = str(Path(tmp) / "notes")
            engine = SelfImprovementEngine.from_config(config)

            engine._write_index(  # noqa: SLF001
                {
                    "agent-a::note-b": ImprovementNote(
                        agent_id="agent-a",
                        signature="note-b",
                        status="active",
                        source="tool_failure",
                        context="ctx",
                        guidance="Retry with required args.",
                        trigger_tokens=("weather",),
                        tags=(
                            "tool:weather-openmeteo-current",
                            "error:missing-city-argument",
                        ),
                        occurrence_count=2,
                        apply_count=0,
                        created_at="2026-05-08T00:00:00+00:00",
                        updated_at="2026-05-08T00:00:04+00:00",
                    ).to_dict(),
                    "agent-a::note-a": ImprovementNote(
                        agent_id="agent-a",
                        signature="note-a",
                        status="active",
                        source="tool_failure",
                        context="ctx",
                        guidance="Validate args first.",
                        trigger_tokens=("weather",),
                        tags=(
                            "tool:weather-openmeteo-current",
                            "error:missing-city-argument",
                        ),
                        occurrence_count=2,
                        apply_count=0,
                        created_at="2026-05-08T00:00:00+00:00",
                        updated_at="2026-05-08T00:00:04+00:00",
                    ).to_dict(),
                    "agent-a::note-c": ImprovementNote(
                        agent_id="agent-a",
                        signature="note-c",
                        status="active",
                        source="tool_failure",
                        context="ctx",
                        guidance="General weather note.",
                        trigger_tokens=("weather",),
                        tags=("tool:weather-openmeteo-current",),
                        occurrence_count=1,
                        apply_count=0,
                        created_at="2026-05-08T00:00:00+00:00",
                        updated_at="2026-05-08T00:00:05+00:00",
                    ).to_dict(),
                }
            )

            matched = engine.find_notes_for_context(
                agent_id="agent-a",
                tool_names=("weather.openmeteo.current",),
                error_slugs=("missing city argument",),
            )

            self.assertEqual(
                [note.signature for note in matched],
                ["note-a", "note-b", "note-c"],
            )


class SRR505AtomicIndexWriteTests(unittest.TestCase):
    def test_write_index_uses_atomic_rename_no_tempfile_remains(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.storage.path = str(Path(tmp) / "state" / "openminion.db")
            config.self_improvement.notes_path = str(Path(tmp) / "notes")
            engine = SelfImprovementEngine.from_config(config)

            tool_result = ToolExecutionResult(
                tool_name="weather.openmeteo.current",
                ok=False,
                verified=False,
                content="",
                error="missing city argument",
            )
            for _ in range(3):
                engine.capture_tool_failures(
                    agent_id="openminion",
                    user_message="check weather in san francisco",
                    tool_results=[tool_result],
                )

            notes_root = Path(config.self_improvement.notes_path)
            index_path = notes_root / "notes_index.json"
            self.assertTrue(
                index_path.exists(),
                "atomic write should leave the canonical index path populated",
            )
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(payload.get("version"), 1)
            self.assertIsInstance(payload.get("notes"), list)

            tempfile_residue = list(notes_root.glob(".notes_index.*.tmp"))
            self.assertEqual(
                tempfile_residue,
                [],
                f"atomic write should clean up tempfile residue; "
                f"found leftovers: {tempfile_residue}",
            )

    def test_write_index_round_trip_preserves_payload_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = OpenMinionConfig()
            _csc_install_default_agent(config)  # type: ignore[attr-defined]
            config.storage.path = str(Path(tmp) / "state" / "openminion.db")
            config.self_improvement.notes_path = str(Path(tmp) / "notes")
            config.self_improvement.activation_threshold = 1
            engine = SelfImprovementEngine.from_config(config)

            tool_result = ToolExecutionResult(
                tool_name="weather.openmeteo.current",
                ok=False,
                verified=False,
                content="",
                error="missing city argument",
            )
            engine.capture_tool_failures(
                agent_id="openminion",
                user_message="check weather in san francisco",
                tool_results=[tool_result],
            )

            notes_root = Path(config.self_improvement.notes_path)
            index_path = notes_root / "notes_index.json"
            payload = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], 1)
            self.assertIsInstance(payload["notes"], list)
            for note in payload["notes"]:
                self.assertIn("signature", note)
                self.assertIn("agent_id", note)
                self.assertIn("updated_at", note)
