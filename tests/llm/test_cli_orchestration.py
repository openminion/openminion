from __future__ import annotations

import asyncio
import unittest
import threading

from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.orchestration import (
    AgentLLMPolicy,
    CandidateResponse,
    EnsembleResult,
    LLMOrchestrator,
    RuntimeLLMRequest,
    load_catalog_config,
    resolve_route,
)


def _policy(**kwargs) -> AgentLLMPolicy:
    return AgentLLMPolicy.model_validate(kwargs)


def _run_sync(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, object] = {}
    error: dict[str, BaseException] = {}

    def _target() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # pragma: no cover - threaded test bridge
            error["exc"] = exc

    thread = threading.Thread(target=_target, daemon=True)
    thread.start()
    thread.join()
    if "exc" in error:
        raise error["exc"]
    return result.get("value")


def _catalog_cfg(profiles: list | None = None) -> dict:
    return {
        "schema_version": 1,
        "profiles": profiles
        or [
            {
                "id": "stub-fast",
                "provider": "stub",
                "model": "stub-v1",
                "supports_json": True,
            }
        ],
        "defaults": {"default_timeout_ms": 1000, "default_max_parallel": 2},
        "limits": {"max_parallel_global": 4, "max_tokens_per_call_hard": 2048},
    }


class RouteResolveTests(unittest.TestCase):
    def test_resolves_to_purpose_route(self) -> None:
        policy = _policy(
            **{
                "default_route": {"mode": "single", "profile_id": "default-profile"},
                "by_purpose": {
                    "reflect": {"mode": "single", "profile_id": "stub-fast"}
                },
            }
        )
        route = resolve_route(policy, "reflect")
        self.assertEqual(route.profile_id, "stub-fast")

    def test_falls_back_to_default_route(self) -> None:
        policy = _policy(
            **{
                "default_route": {"mode": "single", "profile_id": "stub-fast"},
                "by_purpose": {
                    "reflect": {"mode": "single", "profile_id": "reflect-profile"}
                },
            }
        )
        route = resolve_route(policy, "plan")
        self.assertEqual(route.profile_id, "stub-fast")

    def test_raises_invalid_argument_when_no_route(self) -> None:
        policy = _policy(
            **{
                "default_route": None,
                "by_purpose": {
                    "reflect": {"mode": "single", "profile_id": "stub-fast"}
                },
            }
        )
        with self.assertRaises(LLMCtlError) as ctx:
            resolve_route(policy, "plan")
        self.assertEqual(ctx.exception.code, "INVALID_ARGUMENT")

    def test_resolves_ensemble_route(self) -> None:
        policy = _policy(
            **{
                "by_purpose": {
                    "ensemble-task": {
                        "mode": "ensemble",
                        "providers": ["stub-fast"],
                        "selection_policy": "pick_primary_if_ok",
                    }
                }
            }
        )
        route = resolve_route(policy, "ensemble-task")
        self.assertEqual(route.mode, "ensemble")


class LoadCatalogConfigTests(unittest.TestCase):
    def test_loads_catalog_with_profiles(self) -> None:
        catalog = load_catalog_config(_catalog_cfg())
        self.assertEqual(len(catalog.profiles), 1)
        self.assertEqual(catalog.profiles[0].id, "stub-fast")

    def test_catalog_exposes_limits(self) -> None:
        catalog = load_catalog_config(_catalog_cfg())
        self.assertEqual(catalog.limits.max_tokens_per_call_hard, 2048)

    def test_catalog_json_roundtrip(self) -> None:
        raw = _catalog_cfg()
        catalog = load_catalog_config(raw)
        dumped = catalog.model_dump(mode="json")
        self.assertEqual(dumped["profiles"][0]["id"], "stub-fast")


class EnsembleCallTests(unittest.TestCase):
    def _stub_llmctl(self):
        from openminion.modules.llm.runtime.client import LLMCTL

        config = {
            "version": 1,
            "llmctl": {"default_provider": "stub", "default_model": "stub-v1"},
            "providers": {},
            "agents": {
                "test-agent": {"default_provider": "stub", "default_model": "stub-v1"},
                "cli": {"default_provider": "stub", "default_model": "stub-v1"},
            },
        }
        return LLMCTL.from_config(config)

    def test_call_for_agent_single_route_success(self) -> None:
        llmctl = self._stub_llmctl()
        catalog = load_catalog_config(_catalog_cfg())
        orchestrator = LLMOrchestrator(llmctl, catalog)

        policy = _policy(
            **{"by_purpose": {"plan": {"mode": "single", "profile_id": "stub-fast"}}}
        )
        request = RuntimeLLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "test"}],
                "trace": {"session_id": "s-test"},
            }
        )

        result = _run_sync(
            orchestrator.call_for_agent("test-agent", "plan", request, policy)
        )
        self.assertIsInstance(result, (CandidateResponse, EnsembleResult))
        if isinstance(result, EnsembleResult):
            self.assertGreater(len(result.candidates), 0)

    def test_call_for_agent_raises_on_no_route(self) -> None:
        llmctl = self._stub_llmctl()
        catalog = load_catalog_config(_catalog_cfg())
        orchestrator = LLMOrchestrator(llmctl, catalog)
        policy = _policy(**{"default_route": None, "by_purpose": {}})
        request = RuntimeLLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "test"}],
                "trace": {"session_id": "s2"},
            }
        )
        with self.assertRaises(LLMCtlError) as ctx:
            _run_sync(
                orchestrator.call_for_agent(
                    "test-agent", "unknown-purpose", request, policy
                )
            )
        self.assertEqual(ctx.exception.code, "INVALID_ARGUMENT")

    def test_call_for_agent_ensemble_returns_candidates(self) -> None:
        llmctl = self._stub_llmctl()
        catalog = load_catalog_config(_catalog_cfg())
        orchestrator = LLMOrchestrator(llmctl, catalog)
        policy = _policy(
            **{
                "by_purpose": {
                    "plan": {
                        "mode": "ensemble",
                        "providers": ["stub-fast"],
                        "selection_policy": "pick_primary_if_ok",
                    }
                }
            }
        )
        request = RuntimeLLMRequest.model_validate(
            {
                "messages": [{"role": "user", "content": "run"}],
                "trace": {"session_id": "s-ens"},
            }
        )
        result = _run_sync(orchestrator.call_for_agent("cli", "plan", request, policy))
        self.assertIsInstance(result, EnsembleResult)
        self.assertGreater(len(result.candidates), 0)


class ResultPolicyTests(unittest.TestCase):
    def test_candidate_success_is_ok(self) -> None:
        candidate = CandidateResponse(
            candidate_id="c1",
            profile_id="stub-fast",
            provider="stub",
            model="stub-v1",
            status="success",
            text="hi",
        )
        self.assertEqual(candidate.status, "success")

    def test_candidate_failed_is_not_ok(self) -> None:
        from openminion.modules.llm.orchestration import ResponseError

        candidate = CandidateResponse(
            candidate_id="c1",
            profile_id="stub-fast",
            provider="stub",
            model="stub-v1",
            status="failed",
            error=ResponseError(code="PROVIDER_ERROR", message="boom"),
        )
        self.assertNotEqual(candidate.status, "success")

    def test_candidate_timeout_is_not_ok(self) -> None:
        candidate = CandidateResponse(
            candidate_id="c2",
            profile_id="stub-fast",
            provider="stub",
            model="stub-v1",
            status="timeout",
        )
        self.assertNotEqual(candidate.status, "success")
