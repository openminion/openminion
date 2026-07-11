import unittest
import json
import re
import tempfile
from pathlib import Path
from typer.testing import CliRunner

from openminion.modules.memory.cli import _build_app
from openminion.modules.memory.storage.sqlite.store import SQLiteMemoryStore
from openminion.modules.memory.models import (
    MemoryCandidate,
    MemoryNamespace,
    MemoryRecord,
)


class TestCLIReadCommands(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.runner = CliRunner()
        self.app = _build_app()
        # Seed some data
        store = SQLiteMemoryStore(Path(self.db_path))
        self.r1 = MemoryRecord(
            id="r1",
            scope="session:s1",
            type="fact",
            content="the sky is blue",
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
            tags=["nature"],
        )
        self.r2 = MemoryRecord(
            id="r2",
            scope="session:s1",
            type="fact",
            content="the sun is yellow",
            created_at="2024-01-02T00:00:00Z",
            updated_at="2024-01-02T00:00:00Z",
        )
        store.put(self.r1)
        store.put(self.r2)
        self.typed_namespace = MemoryNamespace(
            tenant_id="tenant-a",
            org_id="org-a",
            user_id="user-a",
            agent_id="agent-a",
            session_id="session-a",
            conversation_id="conversation-a",
            project_id="project-a",
            graph_id="graph-a",
        )
        store.put(
            MemoryRecord(
                id="typed-a",
                scope="agent:agent-a",
                type="fact",
                content="shared deployment convention",
                namespace=self.typed_namespace,
                created_at="2024-01-03T00:00:00Z",
                updated_at="2024-01-03T00:00:00Z",
            )
        )
        store.put(
            MemoryRecord(
                id="typed-b",
                scope="agent:agent-b",
                type="fact",
                content="shared deployment convention",
                namespace=MemoryNamespace(
                    user_id="user-b",
                    agent_id="agent-b",
                    project_id="project-b",
                ),
                created_at="2024-01-04T00:00:00Z",
                updated_at="2024-01-04T00:00:00Z",
            )
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_list_command(self):
        result = self.runner.invoke(
            self.app, ["list", "--scope", "session:s1", "--db", self.db_path]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("r1", result.output)
        self.assertIn("r2", result.output)

    def test_get_command(self):
        result = self.runner.invoke(self.app, ["get", "r1", "--db", self.db_path])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("r1", result.output)

    def test_get_not_found(self):
        result = self.runner.invoke(
            self.app, ["get", "missing_id", "--db", self.db_path]
        )
        self.assertNotEqual(result.exit_code, 0)

    def test_search_command(self):
        result = self.runner.invoke(
            self.app, ["search", "sky", "--scope", "session:s1", "--db", self.db_path]
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIn("r1", result.output)

    def test_typed_list_uses_one_composite_namespace(self):
        result = self.runner.invoke(
            self.app,
            [
                "list",
                "--user-id",
                "user-a",
                "--agent-id",
                "agent-a",
                "--project-id",
                "project-a",
                "--json",
                "--db",
                self.db_path,
            ],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        records = json.loads(result.output)
        self.assertEqual([record["id"] for record in records], ["typed-a"])

    def test_typed_search_isolates_same_content(self):
        result = self.runner.invoke(
            self.app,
            [
                "search",
                "deployment",
                "--user-id",
                "user-a",
                "--agent-id",
                "agent-a",
                "--json",
                "--db",
                self.db_path,
            ],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        records = json.loads(result.output)
        self.assertEqual([record["id"] for record in records], ["typed-a"])

    def test_all_namespace_dimensions_survive_json_output(self):
        args = ["list"]
        for key, value in self.typed_namespace.as_dict().items():
            args.extend([f"--{key.replace('_', '-')}", value])
        args.extend(["--json", "--db", self.db_path])

        result = self.runner.invoke(self.app, args)

        self.assertEqual(result.exit_code, 0, result.output)
        records = json.loads(result.output)
        self.assertEqual(records[0]["namespace"], self.typed_namespace.as_dict())

    def test_scope_and_typed_conflict_fails_closed(self):
        result = self.runner.invoke(
            self.app,
            [
                "list",
                "--scope",
                "agent:agent-b",
                "--agent-id",
                "agent-a",
                "--db",
                self.db_path,
            ],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("conflicting namespace agent_id", result.output)

    def test_list_requires_scope_or_namespace(self):
        result = self.runner.invoke(self.app, ["list", "--db", self.db_path])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--scope or at least one namespace id", result.output)

    def test_unfiltered_search_remains_available(self):
        result = self.runner.invoke(
            self.app,
            ["search", "deployment", "--json", "--db", self.db_path],
        )

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.output), [])

    def test_list_json_output(self):
        result = self.runner.invoke(
            self.app, ["list", "--scope", "session:s1", "--json", "--db", self.db_path]
        )
        self.assertEqual(result.exit_code, 0)
        data = json.loads(result.output)
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 2)

    def test_help_exposes_full_command_surface(self):
        result = self.runner.invoke(self.app, ["--help"])
        self.assertEqual(result.exit_code, 0, result.output)
        in_commands = False
        commands: list[str] = []
        for line in result.output.splitlines():
            if "Commands" in line:
                in_commands = True
                continue
            if in_commands and line.startswith("╰"):
                break
            if not in_commands or not line.startswith("│ "):
                continue
            match = re.match(r"^│ ([a-z0-9-]+)\s{2,}", line)
            if match is None:
                continue
            commands.append(match.group(1))
        self.assertEqual(
            commands,
            [
                "list",
                "get",
                "search",
                "candidates",
                "history",
                "stats",
                "export",
                "import",
                "inspect",
                "diagnose-tool-failures",
                "approve",
                "reject",
                "promote-approved",
                "gc",
                "provenance",
                "forget",
                "storage",
                "trace",
            ],
        )


class TestCLIWriteCommands(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.runner = CliRunner()
        self.app = _build_app()
        store = SQLiteMemoryStore(Path(self.db_path))
        c1 = MemoryCandidate(
            candidate_id="c1",
            session_id="s1",
            proposed_scope="session:s1",
            type="fact",
            content="proposed fact",
        )
        store.candidate_put(c1)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_approve_command(self):
        result = self.runner.invoke(
            self.app, ["approve", "c1", "--reviewer", "agent", "--db", self.db_path]
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Approved", result.output)

    def test_reject_command(self):
        result = self.runner.invoke(
            self.app,
            [
                "reject",
                "c1",
                "--reviewer",
                "agent",
                "--note",
                "not relevant",
                "--db",
                self.db_path,
            ],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Rejected", result.output)

    def test_gc_command(self):
        result = self.runner.invoke(self.app, ["gc", "--db", self.db_path])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("GC:", result.output)

    def test_promote_approved_command(self):
        # First approve the candidate
        self.runner.invoke(
            self.app, ["approve", "c1", "--reviewer", "agent", "--db", self.db_path]
        )
        result = self.runner.invoke(
            self.app,
            [
                "promote-approved",
                "--session-id",
                "s1",
                "--target-scope",
                "global:all",
                "--db",
                self.db_path,
            ],
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Promoted: 1", result.output)
