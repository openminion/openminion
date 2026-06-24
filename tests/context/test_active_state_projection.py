from __future__ import annotations

import unittest

from openminion.modules.context.service import _project_active_state_to_prompt_view
from openminion.modules.context.schemas import (
    ActiveStatePromptView,
    IntentExecutionPromptView,
    LastResultSummary,
    PlanProgressPromptView,
)


class ActiveStateProjectionTests(unittest.TestCase):
    def test_projection_drops_stdout_stderr(self) -> None:
        active_state = {
            "task_id": "test-task",
            "status": "running",
            "last_result": {
                "command": "ls -la",
                "tool": "run_command",
                "status": "completed",
                "exit_code": 0,
                "outputs": {
                    "stdout": "total 123\ndrwxr-xr-x  5 user  staff   160 Mar  5 10:00 .\ndrwxr-xr-x  5 user  staff   160 Mar  5 10:00 ..\n-rw-r--r--  1 user  staff  1234 Mar  5 10:00 file1.txt\n"
                    * 100,  # Large output
                    "stderr": "",
                },
            },
        }

        view, metrics = _project_active_state_to_prompt_view(active_state)

        self.assertIsNotNone(view)
        self.assertIsNotNone(view.last_result)
        self.assertEqual(view.last_result.command, "ls -la")
        self.assertEqual(view.last_result.tool, "run_command")
        self.assertEqual(view.last_result.status, "completed")
        self.assertEqual(view.last_result.exit_code, 0)
        self.assertEqual(view.last_result.summary, "")
        self.assertEqual(view.last_result.artifact_refs, [])
        self.assertGreater(metrics["raw_chars"], 0)
        self.assertLess(metrics["projected_chars"], metrics["raw_chars"])
        self.assertGreater(metrics["chars_saved"], 0)

    def test_projection_keeps_semantic_signal(self) -> None:
        active_state = {
            "task_id": "test-task-123",
            "task_description": "List files in directory",
            "status": "running",
            "last_result": {
                "command": "ls -la",
                "tool": "run_command",
                "status": "completed",
                "exit_code": 0,
                "summary": "Listed 5 files",
                "artifact_refs": ["artifact-001", "artifact-002"],
            },
            "open_questions": ["question1", "question2"],
        }

        view, metrics = _project_active_state_to_prompt_view(active_state)

        self.assertIsNotNone(view)
        self.assertEqual(view.task_id, "test-task-123")
        self.assertEqual(view.task_description, "List files in directory")
        self.assertEqual(view.status, "running")

        self.assertIsNotNone(view.last_result)
        self.assertEqual(view.last_result.command, "ls -la")
        self.assertEqual(view.last_result.tool, "run_command")
        self.assertEqual(view.last_result.status, "completed")
        self.assertEqual(view.last_result.exit_code, 0)
        self.assertEqual(view.last_result.summary, "Listed 5 files")
        self.assertEqual(
            view.last_result.artifact_refs, ["artifact-001", "artifact-002"]
        )

        self.assertEqual(view.open_questions, ["question1", "question2"])

        self.assertGreater(metrics["raw_chars"], 0)
        self.assertGreater(metrics["projected_chars"], 0)

    def test_projection_handles_none(self) -> None:
        view, metrics = _project_active_state_to_prompt_view(None)
        self.assertIsNone(view)
        self.assertEqual(metrics["raw_chars"], 0)
        self.assertEqual(metrics["projected_chars"], 0)
        self.assertEqual(metrics["chars_saved"], 0)

    def test_projection_handles_empty_dict(self) -> None:
        view, metrics = _project_active_state_to_prompt_view({})
        self.assertIsNone(view)
        self.assertEqual(metrics["raw_chars"], 0)

    def test_projection_caps_open_questions(self) -> None:
        active_state = {
            "open_questions": [f"q{i}" for i in range(20)],
        }

        view, metrics = _project_active_state_to_prompt_view(active_state)

        self.assertIsNotNone(view)
        self.assertEqual(len(view.open_questions), 10)

    def test_projection_caps_last_result_summary(self) -> None:
        long_summary = (
            "This is a very long summary that exceeds two hundred characters " * 20
        )
        active_state = {
            "last_result": {
                "summary": long_summary,
            },
        }

        view, metrics = _project_active_state_to_prompt_view(active_state)

        self.assertIsNotNone(view)
        self.assertIsNotNone(view.last_result)
        self.assertLessEqual(len(view.last_result.summary), 200)

    def test_projection_caps_artifact_refs(self) -> None:
        active_state = {
            "last_result": {
                "artifact_refs": [f"artifact-{i:03d}" for i in range(10)],
            },
        }

        view, metrics = _project_active_state_to_prompt_view(active_state)

        self.assertIsNotNone(view)
        self.assertIsNotNone(view.last_result)
        self.assertEqual(len(view.last_result.artifact_refs), 5)

    def test_projection_normalizes_structured_artifact_refs_to_strings(self) -> None:
        active_state = {
            "last_result": {
                "artifact_refs": [
                    {"ref": "artifact-001", "label": "body"},
                    {"ref": "artifact-002", "meta": {"mime": "text/plain"}},
                    "artifact-003",
                    {"label": "missing-ref"},
                ],
            },
        }

        view, _metrics = _project_active_state_to_prompt_view(active_state)

        self.assertIsNotNone(view)
        self.assertIsNotNone(view.last_result)
        self.assertEqual(
            view.last_result.artifact_refs,
            ["artifact-001", "artifact-002", "artifact-003"],
        )

    def test_projection_extracts_typed_intent_and_plan_progress(self) -> None:
        active_state = {
            "status": "active",
            "plan": {
                "steps": [
                    {"title": "inspect"},
                    {"title": "test"},
                    {"title": "document"},
                ]
            },
            "cursor": 1,
            "decision_sub_intents": [
                "inspect repo",
                "add tests",
                "document API",
            ],
            "intent_execution_states": [
                {
                    "intent_id": "inspect_repo",
                    "status": "succeeded",
                    "depends_on": [],
                    "last_step_index": 0,
                    "updated_at": "2026-04-12T12:00:00Z",
                },
                {
                    "intent_id": "add_tests",
                    "status": "in_progress",
                    "depends_on": ["inspect_repo"],
                    "last_step_index": 1,
                    "updated_at": "2026-04-12T12:01:00Z",
                },
            ],
            "other_key": "kept",
        }

        view, _metrics = _project_active_state_to_prompt_view(active_state)

        self.assertIsNotNone(view)
        self.assertEqual(
            view.declared_sub_intents,
            ["inspect repo", "add tests", "document API"],
        )
        self.assertEqual(
            [item.intent_id for item in view.intent_execution_states],
            ["inspect_repo", "add_tests"],
        )
        self.assertIsNotNone(view.plan_progress)
        assert view.plan_progress is not None
        self.assertTrue(view.plan_progress.has_plan)
        self.assertEqual(view.plan_progress.step_count, 3)
        self.assertEqual(view.plan_progress.cursor, 1)
        self.assertEqual(view.metadata, {"other_key": "kept"})

    def test_projection_excludes_raw_intent_and_plan_keys_from_metadata(self) -> None:
        active_state = {
            "plan": {"steps": [{"title": "inspect"}]},
            "cursor": 0,
            "decision_sub_intents": ["inspect repo"],
            "decision_sub_intent_refs": [{"id": "inspect_repo"}],
            "intent_execution_states": [
                {
                    "intent_id": "inspect_repo",
                    "status": "pending",
                    "depends_on": [],
                }
            ],
            "task_id": "task-1",
        }

        view, _metrics = _project_active_state_to_prompt_view(active_state)

        self.assertIsNotNone(view)
        self.assertNotIn("plan", view.metadata)
        self.assertNotIn("cursor", view.metadata)
        self.assertNotIn("decision_sub_intents", view.metadata)
        self.assertNotIn("decision_sub_intent_refs", view.metadata)
        self.assertNotIn("intent_execution_states", view.metadata)


class ActiveStatePromptViewSchemaTests(unittest.TestCase):
    def test_schema_has_expected_fields(self) -> None:
        view = ActiveStatePromptView(
            state_ref="ref-123",
            task_id="task-456",
            task_description="Test task",
            status="running",
            last_result=LastResultSummary(
                command="test",
                tool="test_tool",
                status="success",
                exit_code=0,
                summary="Test summary",
                artifact_refs=["art-1"],
            ),
            open_questions=["q1", "q2"],
            declared_sub_intents=["inspect repo", "add tests"],
            intent_execution_states=[
                IntentExecutionPromptView(
                    intent_id="inspect_repo",
                    status="succeeded",
                    depends_on=[],
                    last_step_index=0,
                    updated_at="2026-04-12T12:00:00Z",
                )
            ],
            plan_progress=PlanProgressPromptView(
                has_plan=True,
                step_count=2,
                cursor=1,
            ),
            metadata={"key": "value"},
        )

        self.assertEqual(view.state_ref, "ref-123")
        self.assertEqual(view.task_id, "task-456")
        self.assertEqual(view.task_description, "Test task")
        self.assertEqual(view.status, "running")
        self.assertIsNotNone(view.last_result)
        self.assertEqual(view.last_result.command, "test")
        self.assertEqual(view.open_questions, ["q1", "q2"])
        self.assertEqual(view.declared_sub_intents, ["inspect repo", "add tests"])
        self.assertEqual(len(view.intent_execution_states), 1)
        self.assertIsNotNone(view.plan_progress)
        self.assertEqual(view.metadata, {"key": "value"})


class ActiveStateHardRedactionTests(unittest.TestCase):
    def test_hard_redaction_removes_stdout_from_last_result(self) -> None:
        active_state = {
            "task_id": "test-task",
            "status": "running",
            "last_result": {
                "command": "ls",
                "tool": "run_command",
                "status": "completed",
                "exit_code": 0,
                "outputs": {
                    "stdout": "file1.txt\nfile2.txt\nfile3.txt\n" * 100,
                    "stderr": "",
                    "returncode": 0,
                },
            },
        }

        view, metrics = _project_active_state_to_prompt_view(active_state)
        view_json = view.model_dump_json()

        # stdout should NOT appear in the JSON
        self.assertNotIn("file1.txt", view_json)
        self.assertNotIn("stdout", view_json.lower())

    def test_hard_redaction_removes_stderr_from_last_result(self) -> None:
        active_state = {
            "task_id": "test-task",
            "status": "running",
            "last_result": {
                "command": "ls",
                "tool": "run_command",
                "status": "completed",
                "exit_code": 1,
                "outputs": {
                    "stdout": "",
                    "stderr": "Error: permission denied\n" * 50,
                    "returncode": 1,
                },
            },
        }

        view, metrics = _project_active_state_to_prompt_view(active_state)
        view_json = view.model_dump_json()

        # stderr should NOT appear in the JSON
        self.assertNotIn("permission denied", view_json)
        self.assertNotIn("stderr", view_json.lower())

    def test_hard_redaction_removes_body_from_outputs(self) -> None:
        active_state = {
            "task_id": "test-task",
            "status": "running",
            "last_result": {
                "command": "http_request",
                "tool": "http_request",
                "status": "completed",
                "outputs": {
                    "body": '{"large": "json", "data": "here"}' * 100,
                    "status_code": 200,
                },
            },
        }

        view, metrics = _project_active_state_to_prompt_view(active_state)
        view_json = view.model_dump_json()

        # body should NOT appear in the JSON
        self.assertNotIn("large", view_json)
        self.assertNotIn("body", view_json.lower())

    def test_hard_redaction_in_metadata(self) -> None:
        active_state = {
            "task_id": "test-task",
            "status": "running",
            "some_internal_data": {
                "stdout": "should be removed",
                "outputs": {"data": "should be removed"},
                "result": "should be removed",
                "safe_field": "should be kept",
            },
        }

        view, metrics = _project_active_state_to_prompt_view(active_state)
        view_json = view.model_dump_json()

        # Sensitive fields should NOT appear
        self.assertNotIn("should be removed", view_json)
        self.assertIn("safe_field", view_json)
