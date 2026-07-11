from __future__ import annotations

import unittest
from typing import Any

from openminion.modules.brain.tools.executor import _TOOL_OUTCOME_RECORD_TYPE
from openminion.modules.memory.runtime.extraction.records import (
    _extract_facts_todos_done,
)


# Site 1 + 2: User-message regex extractor never captures tool-failure text


class UserMessageRegexExtractorIsolationTests(unittest.TestCase):
    def test_unknown_tool_line_without_prefix_emits_no_fact(self) -> None:
        facts, has_remember, todos_add, todos_done = _extract_facts_todos_done(
            "Unknown tool: weather.search"
        )
        self.assertEqual(facts, [])
        self.assertFalse(has_remember)
        self.assertEqual(todos_add, [])
        self.assertEqual(todos_done, [])

    def test_tool_execution_failed_line_emits_no_fact(self) -> None:
        facts, _, _, _ = _extract_facts_todos_done(
            "Tool execution failed: PROVIDER_TIMEOUT after 30000ms"
        )
        self.assertEqual(facts, [])

    def test_multiline_tool_failure_noise_emits_no_fact(self) -> None:
        user_message = (
            "Unknown tool: web.search\n"
            "Tool execution failed: UNAUTHORIZED\n"
            "unknown tool 'forecast'\n"
        )
        facts, has_remember, todos_add, todos_done = _extract_facts_todos_done(
            user_message
        )
        self.assertEqual(facts, [])
        self.assertFalse(has_remember)
        self.assertEqual(todos_add, [])
        self.assertEqual(todos_done, [])

    def test_explicit_fact_prefix_still_extracts(self) -> None:
        facts, _, _, _ = _extract_facts_todos_done("fact: my name is Jay")
        self.assertEqual(facts, ["my name is Jay"])

    def test_correction_prefix_still_extracts_as_explicit_fact(self) -> None:
        facts, has_remember, todos_add, todos_done = _extract_facts_todos_done(
            "Correction: my work email is beta@example.com. Remember this instead."
        )
        self.assertEqual(
            facts,
            ["my work email is beta@example.com. Remember this instead."],
        )
        self.assertTrue(has_remember)
        self.assertEqual(todos_add, [])
        self.assertEqual(todos_done, [])

    def test_failure_text_after_valid_prefix_is_captured_verbatim(self) -> None:
        facts, _, _, _ = _extract_facts_todos_done(
            "fact: Unknown tool: weather.search is still broken"
        )
        self.assertEqual(facts, ["Unknown tool: weather.search is still broken"])


# Site 3: Tool-outcome staging always uses record_type="tool_outcome"


class ToolOutcomeStagingRecordTypeIsolationTests(unittest.TestCase):
    def test_tool_outcome_record_type_constant_is_tool_outcome(self) -> None:
        self.assertEqual(_TOOL_OUTCOME_RECORD_TYPE, "tool_outcome")
        self.assertNotEqual(_TOOL_OUTCOME_RECORD_TYPE, "fact")

    def test_stage_tool_outcome_uses_tool_outcome_record_type(self) -> None:
        from types import SimpleNamespace

        from openminion.modules.brain.tools.executor.tool import (
            _stage_tool_outcome_candidate,
        )

        captured: list[dict[str, Any]] = []

        class _FakeMemoryAPI:
            def stage_candidate(self, **kwargs: Any) -> str:
                captured.append(dict(kwargs))
                return "cand-1"

        runner = SimpleNamespace(
            memory_api=_FakeMemoryAPI(),
            profile=SimpleNamespace(agent_id="test-agent"),
            session_api=None,
        )
        state = SimpleNamespace(
            session_id="s-test",
            module_state={},
            memory_candidates=[],
        )

        result = _stage_tool_outcome_candidate(
            runner,
            state=state,
            tool_name="example.tool",
            action_result=None,
            command=None,
            forced_outcome="failure",
        )

        self.assertEqual(result, "cand-1")
        self.assertEqual(len(captured), 1)
        staged = captured[0]
        # The structural guard: record_type must be the canonical tool-outcome
        # constant, never "fact".
        self.assertEqual(staged.get("record_type"), _TOOL_OUTCOME_RECORD_TYPE)
        self.assertEqual(staged.get("record_type"), "tool_outcome")
        self.assertNotEqual(staged.get("record_type"), "fact")
        # Sanity: the captured call actually flows through the tool-outcome
        # contract (tool_name, outcome, tags including tool_outcome).
        content = staged.get("content") or {}
        self.assertEqual(content.get("tool_name"), "example.tool")
        self.assertEqual(content.get("outcome"), "failure")
        tags = list(staged.get("tags") or [])
        self.assertIn("tool_outcome", tags)
        self.assertIn("outcome:failure", tags)


# Site 4: AFE post-turn extractor takes user_message, never tool output


class AfePostTurnExtractorInputIsolationTests(unittest.TestCase):
    def test_afe_entrypoint_signature_takes_user_message_only(self) -> None:
        from openminion.modules.brain.execution.memory import (
            extract_user_message_candidates,
        )
        import inspect

        params = inspect.signature(extract_user_message_candidates).parameters
        # The content parameter is `user_message` — not `tool_result`
        # or a structurally generic `content` field that could accept
        # tool output.
        self.assertIn("user_message", params)
        self.assertNotIn("tool_result", params)
        self.assertNotIn("tool_outcome", params)
        self.assertNotIn("action_result", params)

    def test_afe_callsite_passes_state_user_message(self) -> None:
        from openminion.modules.brain.runner.tick import input_processing

        source_text = _module_source(input_processing)
        self.assertIn("extract_user_message_candidates(", source_text)
        afe_call_region = _extract_region(
            source_text,
            start_marker="extract_user_message_candidates(",
            end_marker=")",
            inclusive_end=True,
        )
        self.assertIn("user_message=", afe_call_region)
        # The call does NOT pass tool-output-like fields.
        self.assertNotIn("tool_result", afe_call_region)
        self.assertNotIn("action_result", afe_call_region)


# Site 5: Reflection lessons stamp self-improvement:lesson tag


class ReflectionLessonProvenanceTests(unittest.TestCase):
    def test_lesson_fix_stamps_self_improvement_lesson_tag(self) -> None:
        captured: list[dict[str, Any]] = []

        class _FakeMemoryApi:
            def put_record(
                self,
                *,
                scope: str,
                record_type: str,
                title: str,
                content: Any,
                tags: list[str] | None = None,
                evidence_refs: list[str] | None = None,
            ) -> str:
                captured.append(
                    {
                        "scope": scope,
                        "record_type": record_type,
                        "title": title,
                        "tags": list(tags or []),
                    }
                )
                return f"mem_{len(captured)}"

        runner = _make_reflection_runner(memory_api=_FakeMemoryApi())
        state = _FakeWorkingState()
        report = _FakeReflectReport(
            fixes=[
                _FakeFix(
                    kind="lesson",
                    title="Prefer curl over wget for these endpoints",
                    content={"text": "Body"},
                    tags=[],
                    evidence_refs=[],
                )
            ]
        )
        logger = _FakeLogger()

        from openminion.modules.brain.runtime.memory import (
            apply_improvements,
        )

        apply_improvements(
            runner,
            state=state,
            report=report,
            logger=logger,  # type: ignore[arg-type]
        )

        self.assertEqual(len(captured), 1)
        write = captured[0]
        # Lessons normalize to fact record_type.
        self.assertEqual(write["record_type"], "fact")
        # The structural provenance tag is present so downstream
        # context tooling can distinguish lessons from user-stated
        # facts without inspecting text.
        self.assertIn("self-improvement:lesson", write["tags"])

    def test_lesson_with_existing_self_improvement_tag_not_duplicated(self) -> None:
        captured: list[dict[str, Any]] = []

        class _FakeMemoryApi:
            def put_record(
                self, *, tags: list[str] | None = None, **kwargs: Any
            ) -> str:
                captured.append({"tags": list(tags or [])})
                return "mem_1"

        runner = _make_reflection_runner(memory_api=_FakeMemoryApi())
        state = _FakeWorkingState()
        report = _FakeReflectReport(
            fixes=[
                _FakeFix(
                    kind="lesson",
                    title="A lesson",
                    content="body",
                    tags=["self-improvement:lesson", "custom"],
                    evidence_refs=[],
                )
            ]
        )
        logger = _FakeLogger()

        from openminion.modules.brain.runtime.memory import (
            apply_improvements,
        )

        apply_improvements(
            runner,
            state=state,
            report=report,
            logger=logger,  # type: ignore[arg-type]
        )

        self.assertEqual(len(captured), 1)
        tag_count = captured[0]["tags"].count("self-improvement:lesson")
        self.assertEqual(tag_count, 1)


# Helpers


def _module_source(module: Any) -> str:
    import inspect

    return inspect.getsource(module)


def _extract_region(
    text: str,
    *,
    start_marker: str,
    end_marker: str,
    inclusive_end: bool = False,
) -> str:
    start = text.find(start_marker)
    if start < 0:
        return ""
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        end = len(text)
    if inclusive_end:
        end += len(end_marker)
    return text[start:end]


class _FakeFix:
    def __init__(
        self,
        *,
        kind: str,
        title: str,
        content: Any,
        tags: list[str],
        evidence_refs: list[Any],
    ) -> None:
        self.kind = kind
        self.title = title
        self.content = content
        self.tags = list(tags)
        self.evidence_refs = list(evidence_refs)
        self.action = ""
        self.scope_suggestion = "agent:test"
        self.target_command_id = ""


class _FakeReflectReport:
    def __init__(self, *, fixes: list[_FakeFix]) -> None:
        self.fixes = fixes


class _FakeLogger:
    def emit(self, *args: Any, **kwargs: Any) -> None:
        return None


class _FakeDefaults:
    auto_save_lessons = True
    auto_stage_policy_candidates = False


class _FakeProfile:
    agent_id = "agent:test"
    defaults = _FakeDefaults()


class _FakeRunner:
    def __init__(self, *, memory_api: Any) -> None:
        self.memory_api = memory_api
        self.profile = _FakeProfile()


def _make_reflection_runner(*, memory_api: Any) -> _FakeRunner:
    return _FakeRunner(memory_api=memory_api)


class _FakeWorkingState:
    def __init__(self) -> None:
        self.constraints: list[str] = []
        self.memory_candidates: list[str] = []
        self.trace_id = "trace-srtf-02"
