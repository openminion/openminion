from __future__ import annotations

import ast
import unittest
from pathlib import Path
from types import SimpleNamespace

from openminion.base.types import Message
from openminion.modules.llm.providers.envelope_v2 import CONTRACT_VERSION_V2
from openminion.services.agent.execution.flow import _unavailable_response
from openminion.services.agent.service import AgentService
from openminion.services.brain.post_execution.postprocess import (
    _attach_tool_result_metadata,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

GUARDED_FILES: tuple[Path, ...] = (
    REPO_ROOT / "src" / "openminion" / "services" / "agent" / "service.py",
    REPO_ROOT / "src" / "openminion" / "services" / "agent" / "execution" / "flow.py",
    REPO_ROOT
    / "src"
    / "openminion"
    / "services"
    / "brain"
    / "post_execution"
    / "postprocess.py",
)

EXPECTED_IMPORT_MODULE = "openminion.modules.llm.providers.envelope_v2"
EXPECTED_IMPORT_NAME = "CONTRACT_VERSION_V2"
METADATA_KEY = "tool_contract_version"


def _find_inline_constant_emissions(tree: ast.AST) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key_node, value_node in zip(node.keys, node.values):
                if (
                    isinstance(key_node, ast.Constant)
                    and key_node.value == METADATA_KEY
                    and isinstance(value_node, ast.Constant)
                    and isinstance(value_node.value, str)
                ):
                    findings.append(
                        (
                            value_node.lineno,
                            f"dict literal {METADATA_KEY!r}: {value_node.value!r}",
                        )
                    )

        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == METADATA_KEY
                    and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                ):
                    findings.append(
                        (
                            node.lineno,
                            f"subscript assignment {METADATA_KEY!r} = "
                            f"{node.value.value!r}",
                        )
                    )

    return findings


def _has_canonical_import(tree: ast.AST) -> bool:
    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module != EXPECTED_IMPORT_MODULE:
            continue
        for alias in node.names:
            if alias.name == EXPECTED_IMPORT_NAME:
                return True
    return False


class CanonicalConstantValueTests(unittest.TestCase):
    def test_canonical_constant_value_is_v2(self) -> None:
        self.assertEqual(CONTRACT_VERSION_V2, "v2")


class SourceCentralizationGuardTests(unittest.TestCase):
    def test_all_guarded_files_exist(self) -> None:
        for path in GUARDED_FILES:
            self.assertTrue(
                path.exists(),
                f"Guarded emission site missing: {path}. "
                f"If the file was renamed, update GUARDED_FILES in this test.",
            )

    def test_each_guarded_file_imports_canonical_constant(self) -> None:
        for path in GUARDED_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            self.assertTrue(
                _has_canonical_import(tree),
                f"{path.relative_to(REPO_ROOT)} emits "
                f"{METADATA_KEY!r} but does not import "
                f"{EXPECTED_IMPORT_NAME} from {EXPECTED_IMPORT_MODULE}.",
            )

    def test_no_guarded_file_uses_inline_v2_literal_for_metadata_key(self) -> None:
        for path in GUARDED_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            findings = _find_inline_constant_emissions(tree)
            self.assertEqual(
                findings,
                [],
                f"{path.relative_to(REPO_ROOT)} contains inline literal(s) for "
                f"the {METADATA_KEY!r} metadata key. Replace with "
                f"{EXPECTED_IMPORT_NAME} from {EXPECTED_IMPORT_MODULE}. "
                f"Findings: {findings}",
            )


class CrossFileConstantIdentityTests(unittest.TestCase):
    def test_consumers_resolve_to_same_constant_value(self) -> None:
        from openminion.services.agent.execution.flow import (
            CONTRACT_VERSION_V2 as base_constant,
        )
        from openminion.services.agent.service import (
            CONTRACT_VERSION_V2 as service_constant,
        )
        from openminion.services.brain.post_execution.postprocess import (
            CONTRACT_VERSION_V2 as postprocess_constant,
        )

        for consumer_constant in (
            base_constant,
            service_constant,
            postprocess_constant,
        ):
            self.assertEqual(consumer_constant, CONTRACT_VERSION_V2)


class BehavioralEmissionTests(unittest.TestCase):
    def test_tool_turn_metadata_builders_emit_shared_contract_version(self) -> None:
        service = AgentService.__new__(AgentService)
        batch = SimpleNamespace(
            results=[
                SimpleNamespace(
                    tool_name="weather.openmeteo.current",
                    ok=True,
                    verified=True,
                    content="ok",
                    error="",
                    data={},
                    call_id="call-1",
                    source="native",
                )
            ],
            all_verified=True,
        )
        batch_metadata = service._tool_batch_metadata(batch=batch, tool_calls_count=1)
        self.assertEqual(batch_metadata["tool_contract_version"], CONTRACT_VERSION_V2)

        unavailable = _unavailable_response(
            SimpleNamespace(
                _empty_tool_resolution_metadata=lambda: {},
                _identity_metadata=lambda: {},
            ),
            inbound=Message(channel="console", target="me", body="hello"),
            text="tool unavailable",
            intent_category="weather",
            requested_forced_tools=["weather.openmeteo.current"],
            termination_reason="tool_unavailable",
        )
        self.assertEqual(
            unavailable.metadata["tool_contract_version"], CONTRACT_VERSION_V2
        )

        metadata: dict[str, str] = {}
        _attach_tool_result_metadata(
            None,
            metadata=metadata,
            tool_results_payload=[
                {
                    "tool_name": "weather.openmeteo.current",
                    "ok": True,
                    "verified": True,
                    "data": {},
                    "call_id": "call-1",
                }
            ],
            termination_reason="tool_final",
        )
        self.assertEqual(metadata["tool_contract_version"], CONTRACT_VERSION_V2)
