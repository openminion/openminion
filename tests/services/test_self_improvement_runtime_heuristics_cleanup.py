from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from openminion.base.config import OpenMinionConfig
from openminion.modules.tool.base import ToolExecutionResult
from openminion.modules.brain.runtime.improvement import (
    notes as self_improvement_module,
)
from openminion.services.agent.execution import flow as agent_execution_flow
from openminion.services.brain.post_execution import (
    context as brain_post_execution_context,
)
from openminion.services.lifecycle.self_improvement import SelfImprovementEngine
from tests._csc_fixtures import _csc_install_default_agent


def _engine_with_captured_note(tmp: str) -> SelfImprovementEngine:
    config = OpenMinionConfig()
    _csc_install_default_agent(config)  # type: ignore[attr-defined]
    config.storage.path = str(Path(tmp) / "state" / "openminion.db")
    config.self_improvement.notes_path = str(Path(tmp) / "notes")
    config.self_improvement.activation_threshold = 1
    engine = SelfImprovementEngine.from_config(config)
    engine.capture_tool_failures(
        agent_id="sirh-agent",
        user_message="weather in san francisco and tokyo",
        tool_results=[
            ToolExecutionResult(
                tool_name="weather.openmeteo.current",
                ok=False,
                verified=False,
                content="",
                error="invalid city",
            )
        ],
    )
    return engine


def test_service_self_improvement_surface_is_canonical_brain_owner() -> None:
    from openminion.modules.brain.runtime.improvement.notes import (
        SelfImprovementEngine as canonical,
    )
    from openminion.services.lifecycle.self_improvement import (
        SelfImprovementEngine as compatibility,
    )

    assert compatibility is canonical


_FORBIDDEN_CALL_PATTERNS = (
    "self._self_improvement.build_guardrail_block(",
    ".build_guardrail_block(",
    "._record_applied(",
)


_FORBIDDEN_PREAMBLE = "Self-improvement guardrails (learned mistakes to avoid):"


class SIRH03HardenedInvariants(unittest.TestCase):
    def test_sirh03_guardrail_builder_and_record_applied_are_removed(
        self,
    ) -> None:
        self.assertFalse(
            hasattr(SelfImprovementEngine, "build_guardrail_block"),
            "SIRH-03: `SelfImprovementEngine.build_guardrail_block` was "
            "removed as a runtime-owned anti-LLM seam (token-overlap "
            "selection + prompt-preamble injection). It must not come "
            "back — design a typed LLM-selected note-recall surface "
            "through a new lane instead.",
        )
        self.assertFalse(
            hasattr(SelfImprovementEngine, "_record_applied"),
            "SIRH-03: `SelfImprovementEngine._record_applied` was removed "
            "with its sole caller `build_guardrail_block`. No production "
            "path may revive the prompt-time apply-count bump.",
        )

    def test_sirh03_constructor_rejects_removed_selection_params(
        self,
    ) -> None:
        notes_root = Path(tempfile.gettempdir()) / "sirh-harden-ctor"
        with self.assertRaises(TypeError):
            SelfImprovementEngine(
                enabled=False,
                notes_root=notes_root,
                min_token_overlap=1,
            )
        with self.assertRaises(TypeError):
            SelfImprovementEngine(
                enabled=False,
                notes_root=notes_root,
                max_applied_notes=3,
            )

    def test_sirh03_self_improvement_source_has_no_forbidden_preamble_call(
        self,
    ) -> None:
        source_path = Path(self_improvement_module.__file__)
        source = source_path.read_text(encoding="utf-8")
        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.lstrip()
            is_comment_or_docstring_prose = (
                stripped.startswith("#") or '"""' in line or "'''" in line
            )
            if _FORBIDDEN_PREAMBLE in line and not is_comment_or_docstring_prose:
                self.fail(
                    "SIRH-03: `self_improvement.py:{lineno}` reintroduced "
                    "the retired preamble literal {preamble!r} outside a "
                    "comment.".format(lineno=lineno, preamble=_FORBIDDEN_PREAMBLE)
                )
            if "._record_applied(" in line and not is_comment_or_docstring_prose:
                self.fail(
                    "SIRH-03: `self_improvement.py:{lineno}` reintroduced "
                    "the retired `_record_applied(` call pattern.".format(lineno=lineno)
                )

    def test_sirh03_call_site_modules_have_no_forbidden_call_patterns(
        self,
    ) -> None:
        for module in (agent_execution_flow, brain_post_execution_context):
            path = Path(module.__file__)
            source = path.read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), start=1):
                stripped = line.lstrip()
                is_comment = stripped.startswith("#")
                if is_comment:
                    continue
                for pattern in _FORBIDDEN_CALL_PATTERNS:
                    if pattern in line:
                        self.fail(
                            "SIRH-03: {path}:{lineno} reintroduced the "
                            "retired call pattern {pattern!r}.".format(
                                path=path.name,
                                lineno=lineno,
                                pattern=pattern,
                            )
                        )

    def test_sirh03_call_site_assembly_pattern_never_produces_preamble(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = _engine_with_captured_note(tmp)
            base_system_prompt = "You are a helpful assistant."
            self.assertFalse(
                hasattr(engine, "build_guardrail_block"),
                "SIRH-03: engine must not expose a guardrail-build method.",
            )
            assembled = base_system_prompt
            self.assertNotIn(_FORBIDDEN_PREAMBLE, assembled)
            self.assertNotIn("learned mistakes to avoid", assembled)
