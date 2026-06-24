import asyncio
import inspect
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Optional

import openminion.modules.llm.orchestration as orchestration_mod
from openminion.modules.llm import LLMCTL, Message
from openminion.modules.llm.errors import LLMCtlError
from openminion.modules.llm.orchestration import (
    AgentLLMPolicy,
    CandidateResponse,
    EnsembleResult,
    EnsembleTemplate,
    LLMOrchestrator,
    ProfileCapabilities,
    ProviderProfile,
    RuntimeLLMRequest,
    load_catalog_config,
    resolve_route,
)
from openminion.modules.llm.orchestration.selection import heuristic_selection
from openminion.modules.llm.providers.cost import estimate_usage_cost_usd
from openminion.modules.llm.schemas import (
    LLMRequest,
    LLMResponse,
    ResponseError,
    UsageInfo,
)


class _StaticProvider:
    def __init__(
        self,
        name: str,
        *,
        text: str = "",
        delay_s: float = 0.0,
        error_code: Optional[str] = None,
    ) -> None:
        self.name = name
        self.contract_version = "v1"
        self._text = text
        self._delay_s = delay_s
        self._error_code = error_code
        self.seen_requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest, config: Dict[str, Any]) -> LLMResponse:
        del config
        self.seen_requests.append(request)
        if self._delay_s > 0:
            time.sleep(self._delay_s)
        if self._error_code:
            return LLMResponse(
                ok=False,
                provider=self.name,
                model=request.model or f"{self.name}-model",
                output_text="",
                assistant_messages=[],
                tool_calls=[],
                usage=UsageInfo(input_tokens=1, output_tokens=0, total_tokens=1),
                latency_ms=0,
                provider_raw={"provider": self.name},
                error=ResponseError(
                    code=self._error_code, message=f"{self.name} failed", details={}
                ),
            )
        return LLMResponse(
            ok=True,
            provider=self.name,
            model=request.model or f"{self.name}-model",
            output_text=self._text,
            assistant_messages=[Message(role="assistant", content=self._text)],
            tool_calls=[],
            usage=UsageInfo(input_tokens=2, output_tokens=4, total_tokens=6),
            latency_ms=0,
            provider_raw={"provider": self.name, "text": self._text},
            error=None,
        )

    def list_models(self, config: Dict[str, Any]) -> list[str]:
        del config
        return [f"{self.name}-model"]

    def healthcheck(self, config: Dict[str, Any]) -> Dict[str, Any]:
        del config
        return {"ok": True}


class _FlakyProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.contract_version = "v1"
        self.calls = 0

    def complete(self, request: LLMRequest, config: Dict[str, Any]) -> LLMResponse:
        del config
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                ok=False,
                provider=self.name,
                model=request.model or f"{self.name}-model",
                output_text="",
                assistant_messages=[],
                tool_calls=[],
                usage=UsageInfo(input_tokens=1, output_tokens=0, total_tokens=1),
                latency_ms=0,
                provider_raw={"provider": self.name, "attempt": self.calls},
                error=ResponseError(
                    code="RATE_LIMITED", message="retry me", details={}
                ),
            )
        return LLMResponse(
            ok=True,
            provider=self.name,
            model=request.model or f"{self.name}-model",
            output_text="recovered",
            assistant_messages=[Message(role="assistant", content="recovered")],
            tool_calls=[],
            usage=UsageInfo(input_tokens=1, output_tokens=2, total_tokens=3),
            latency_ms=0,
            provider_raw={"provider": self.name, "attempt": self.calls},
            error=None,
        )

    def list_models(self, config: Dict[str, Any]) -> list[str]:
        del config
        return [f"{self.name}-model"]

    def healthcheck(self, config: Dict[str, Any]) -> Dict[str, Any]:
        del config
        return {"ok": True}


class _AdapterDictProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.contract_version = "v1"

    def complete(self, request: LLMRequest, config: Dict[str, Any]) -> Dict[str, Any]:
        del config
        return {
            "provider": self.name,
            "model": request.model or f"{self.name}-model",
            "output_text": "dict-normalized",
            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            "latency_ms": 0,
            "provider_raw": {"provider": self.name},
        }

    def list_models(self, config: Dict[str, Any]) -> list[str]:
        del config
        return [f"{self.name}-model"]

    def healthcheck(self, config: Dict[str, Any]) -> Dict[str, Any]:
        del config
        return {"ok": True}


class _CostlyProvider:
    def __init__(self, name: str) -> None:
        self.name = name
        self.contract_version = "v1"

    def complete(self, request: LLMRequest, config: Dict[str, Any]) -> LLMResponse:
        del config
        return LLMResponse(
            ok=True,
            provider=self.name,
            model=request.model or f"{self.name}-model",
            output_text="costly",
            assistant_messages=[Message(role="assistant", content="costly")],
            tool_calls=[],
            usage=UsageInfo(input_tokens=10, output_tokens=20, total_tokens=30),
            latency_ms=0,
            cost_usd=2.5,
            provider_raw={"provider": self.name},
            error=None,
        )

    def list_models(self, config: Dict[str, Any]) -> list[str]:
        del config
        return [f"{self.name}-model"]

    def healthcheck(self, config: Dict[str, Any]) -> Dict[str, Any]:
        del config
        return {"ok": True}


class OrchestrationTests(unittest.TestCase):
    def test_provider_profile_syncs_prompt_cache_capability(self) -> None:
        profile = ProviderProfile(
            id="anthropic-sonnet",
            provider="anthropic",
            model="claude-3-5-sonnet-latest",
            capabilities=ProfileCapabilities(supports_prompt_caching=True),
        )

        self.assertTrue(profile.supports_prompt_caching)

    def test_orchestration_facade_is_package_init_owner(self) -> None:
        facade_path = Path(orchestration_mod.__file__).resolve()
        self.assertEqual(facade_path.name, "__init__.py")
        self.assertEqual(facade_path.parent.name, "orchestration")
        llm_root = facade_path.parent.parent
        self.assertFalse((llm_root / "orchestration.py").exists())
        for legacy_name in (
            "_orchestration_models.py",
            "_orchestration_helpers.py",
            "_orchestration_config.py",
            "_orchestration_disagreement.py",
            "_orchestration_selection.py",
            "_orchestration_core.py",
        ):
            self.assertFalse((llm_root / legacy_name).exists(), msg=legacy_name)

    def test_orchestration_facade_preserves_public_import_surface(self) -> None:
        expected_names = {
            "AgentLLMPolicy",
            "CandidateResponse",
            "EnsembleResult",
            "EnsembleTemplate",
            "LLMOrchestrator",
            "ResponseError",
            "RuntimeLLMRequest",
            "load_catalog_config",
            "resolve_route",
        }
        for name in expected_names:
            self.assertTrue(
                hasattr(orchestration_mod, name),
                msg=f"missing orchestration facade export: {name}",
            )

        init_signature = inspect.signature(orchestration_mod.LLMOrchestrator.__init__)
        self.assertEqual(
            list(init_signature.parameters),
            ["self", "llmctl", "catalog", "env"],
        )
        self.assertEqual(
            init_signature.parameters["env"].kind, inspect.Parameter.KEYWORD_ONLY
        )

        call_signature = inspect.signature(orchestration_mod.LLMOrchestrator.call)
        self.assertEqual(
            list(call_signature.parameters),
            ["self", "request", "profile_id"],
        )

        call_parallel_signature = inspect.signature(
            orchestration_mod.LLMOrchestrator.call_parallel
        )
        self.assertEqual(
            list(call_parallel_signature.parameters),
            ["self", "request", "providers", "strategy"],
        )

        call_for_agent_signature = inspect.signature(
            orchestration_mod.LLMOrchestrator.call_for_agent
        )
        self.assertEqual(
            list(call_for_agent_signature.parameters),
            ["self", "agent_id", "purpose", "request", "agent_policy"],
        )

        judge_signature = inspect.signature(orchestration_mod.LLMOrchestrator.judge)
        self.assertEqual(
            list(judge_signature.parameters),
            ["self", "request", "candidates", "judge_profile_id", "rubric"],
        )

    def setUp(self) -> None:
        self.llmctl = LLMCTL.from_config(
            {
                "version": 1,
                "llmctl": {"default_provider": "stub", "default_model": "stub-v1"},
                "providers": {},
                "agents": {},
            }
        )
        self.provider_fast = _StaticProvider("prov_fast", text="fast answer")
        self.provider_slow = _StaticProvider(
            "prov_slow", text="slow answer", delay_s=0.12
        )
        self.provider_fail = _StaticProvider("prov_fail", error_code="PROVIDER_ERROR")
        self.provider_alt = _StaticProvider("prov_alt", text="alternative answer")
        self.provider_flaky = _FlakyProvider("prov_flaky")
        self.provider_dict = _AdapterDictProvider("prov_dict")
        self.provider_cost = _CostlyProvider("prov_cost")
        self.provider_judge = _StaticProvider(
            "prov_judge",
            text='{"winner_candidate_id":"cand-1","reasons":["clearer"],"scores":{"cand-0":0.2,"cand-1":0.9}}',
        )
        for provider in (
            self.provider_fast,
            self.provider_slow,
            self.provider_fail,
            self.provider_alt,
            self.provider_flaky,
            self.provider_dict,
            self.provider_cost,
            self.provider_judge,
        ):
            self.llmctl.registry.add(provider)

        self.catalog = load_catalog_config(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "fast",
                        "provider": "prov_fast",
                        "model": "m-fast",
                        "supports_json": True,
                    },
                    {
                        "id": "slow",
                        "provider": "prov_slow",
                        "model": "m-slow",
                        "supports_json": True,
                    },
                    {
                        "id": "fail",
                        "provider": "prov_fail",
                        "model": "m-fail",
                        "supports_json": True,
                    },
                    {
                        "id": "alt",
                        "provider": "prov_alt",
                        "model": "m-alt",
                        "supports_json": True,
                    },
                    {
                        "id": "flaky",
                        "provider": "prov_flaky",
                        "model": "m-flaky",
                        "supports_json": True,
                    },
                    {
                        "id": "dict",
                        "provider": "prov_dict",
                        "model": "m-dict",
                        "supports_json": True,
                    },
                    {
                        "id": "cost",
                        "provider": "prov_cost",
                        "model": "m-cost",
                        "supports_json": True,
                    },
                    {
                        "id": "judge",
                        "provider": "prov_judge",
                        "model": "m-judge",
                        "supports_json": True,
                    },
                ],
                "ensembles": [
                    {
                        "id": "panel",
                        "mode": "panel_judge",
                        "providers": ["fast", "alt"],
                        "judge_profile_id": "judge",
                        "selection_policy": "pick_highest_score",
                        "timeout_ms": 2000,
                        "max_parallel": 2,
                        "stop_early": False,
                    }
                ],
                "defaults": {"default_timeout_ms": 1000, "default_max_parallel": 2},
                "limits": {"max_parallel_global": 2, "max_tokens_per_call_hard": 2048},
                "logging": {"store_raw_provider_payloads": True, "emit_events": True},
            }
        )
        self.orch = LLMOrchestrator(self.llmctl, self.catalog)

    def _request(
        self, purpose: str = "plan", timeout_ms: int = 200
    ) -> RuntimeLLMRequest:
        return RuntimeLLMRequest(
            purpose=purpose,
            messages=[Message(role="user", content="hello world")],
            budget={"timeout_ms": timeout_ms, "max_tokens": 800},
            trace={"session_id": "s1", "trace_id": "t1", "agent_id": "a1"},
        )

    def test_resolve_route_prefers_purpose_then_default(self) -> None:
        policy = AgentLLMPolicy.model_validate(
            {
                "default_route": {"mode": "single", "profile_id": "fast"},
                "by_purpose": {"reflect": {"mode": "single", "profile_id": "alt"}},
            }
        )
        reflect_route = resolve_route(policy, "reflect")
        self.assertEqual(reflect_route.profile_id, "alt")
        plan_route = resolve_route(policy, "plan")
        self.assertEqual(plan_route.profile_id, "fast")

    def test_fallback_selection_uses_provider_order_not_response_length(self) -> None:
        result = heuristic_selection(
            [
                CandidateResponse(
                    candidate_id="cand-0",
                    profile_id="short-first",
                    provider="prov",
                    model="model",
                    status="success",
                    text="ok",
                    usage={"output_tokens": 1},
                ),
                CandidateResponse(
                    candidate_id="cand-1",
                    profile_id="long-second",
                    provider="prov",
                    model="model",
                    status="success",
                    text="long answer " * 100,
                    usage={"output_tokens": 500},
                ),
            ]
        )

        self.assertEqual(result.winner_candidate_id, "cand-0")
        self.assertIsNone(result.scores)
        self.assertEqual(result.risk_flags, ["judge_unavailable_first_success"])

    def test_candidate_error_normalizes_llmctl_exception(self) -> None:
        profile = self.orch._get_profile("fast")
        candidate = self.orch._candidate_error(
            candidate_id="cand-0",
            profile=profile,
            error=LLMCtlError("RATE_LIMITED", "retry later", {"provider": "stub"}),
            started=time.perf_counter(),
        )
        self.assertIsNotNone(candidate.error)
        assert candidate.error is not None
        self.assertEqual(candidate.error.code, "RATE_LIMITED")
        self.assertEqual(candidate.error.message, "retry later")
        self.assertEqual(candidate.error.details, {"provider": "stub"})

    def test_call_parallel_timeout_and_event_emission(self) -> None:
        strategy = EnsembleTemplate.model_validate(
            {
                "id": "so",
                "mode": "second_opinion",
                "providers": ["fast", "slow"],
                "selection_policy": "pick_primary_if_ok",
                "timeout_ms": 50,
                "max_parallel": 2,
            }
        )
        result = asyncio.run(
            self.orch.call_parallel(
                self._request(timeout_ms=50), ["fast", "slow"], strategy
            )
        )
        statuses = [item.status for item in result.candidates]
        self.assertIn("success", statuses)
        self.assertIn("timeout", statuses)
        event_types = [item["type"] for item in self.orch.events]
        self.assertIn("llm.request.started", event_types)
        self.assertIn("llm.candidate.finished", event_types)
        self.assertIn("llm.ensemble.completed", event_types)

    def test_panel_judge_returns_winner(self) -> None:
        route_policy = AgentLLMPolicy.model_validate(
            {"by_purpose": {"reflect": {"mode": "ensemble", "strategy_id": "panel"}}}
        )
        result = asyncio.run(
            self.orch.call_for_agent(
                "agent-x", "reflect", self._request("reflect"), route_policy
            )
        )
        self.assertTrue(hasattr(result, "selection"))
        self.assertEqual(result.selection.winner_candidate_id, "cand-1")
        self.assertIn("clearer", " ".join(result.selection.reasons))

    def test_allowlist_blocks_disallowed_route(self) -> None:
        policy = AgentLLMPolicy.model_validate(
            {
                "allow_profiles": ["fast"],
                "by_purpose": {"plan": {"mode": "single", "profile_id": "alt"}},
            }
        )
        with self.assertRaises(LLMCtlError) as context:
            asyncio.run(
                self.orch.call_for_agent("agent-1", "plan", self._request(), policy)
            )
        self.assertEqual(context.exception.code, "POLICY_DENIED")

    def test_budget_clamps_max_tokens_per_call(self) -> None:
        policy = AgentLLMPolicy.model_validate(
            {
                "budgets": {
                    "max_tokens_per_call": 123,
                    "max_tokens_per_turn": 1000,
                    "max_parallel": 2,
                    "max_ensemble_fanout": 2,
                    "max_time_ms_per_turn": 10000,
                },
                "by_purpose": {"plan": {"mode": "single", "profile_id": "fast"}},
            }
        )
        request = RuntimeLLMRequest(
            purpose="plan",
            messages=[Message(role="user", content="clamp test")],
            budget={"timeout_ms": 500, "max_tokens": 5000},
            trace={"agent_id": "a"},
        )
        result = asyncio.run(self.orch.call_for_agent("a", "plan", request, policy))
        self.assertIsInstance(result, CandidateResponse)
        self.assertEqual(result.status, "success")
        self.assertTrue(self.provider_fast.seen_requests)
        self.assertEqual(self.provider_fast.seen_requests[-1].max_output_tokens, 123)
        client = self.orch._get_profile_client("fast")
        self.assertIsNone(client.profile.budgets.max_output_tokens)

    def test_single_route_fallback_on_failure(self) -> None:
        policy = AgentLLMPolicy.model_validate(
            {
                "by_purpose": {"plan": {"mode": "single", "profile_id": "fail"}},
                "fallbacks": {
                    "plan": {
                        "fallback_profile_ids": ["fast"],
                        "fallback_mode": "single",
                        "max_fallback_attempts": 1,
                    }
                },
            }
        )
        result = asyncio.run(
            self.orch.call_for_agent("agent-f", "plan", self._request(), policy)
        )
        self.assertIsInstance(result, CandidateResponse)
        self.assertEqual(result.status, "success")
        self.assertEqual(result.profile_id, "fast")

    def test_parallel_respects_global_concurrency_cap(self) -> None:
        catalog = self.catalog.model_copy(
            update={
                "limits": self.catalog.limits.model_copy(
                    update={"max_parallel_global": 1}
                )
            }
        )
        orch = LLMOrchestrator(self.llmctl, catalog)
        strategy = EnsembleTemplate.model_validate(
            {
                "id": "serial-cap",
                "mode": "second_opinion",
                "providers": ["slow", "alt"],
                "selection_policy": "pick_primary_if_ok",
                "timeout_ms": 1000,
                "max_parallel": 8,
            }
        )
        started = time.perf_counter()
        result = asyncio.run(
            orch.call_parallel(
                self._request(timeout_ms=1000), ["slow", "alt"], strategy
            )
        )
        elapsed = time.perf_counter() - started
        self.assertEqual(len(result.candidates), 2)
        self.assertGreater(elapsed, 0.10)

    def test_structured_output_requires_json_capability(self) -> None:
        no_json_provider = _StaticProvider("prov_no_json", text='{"ok": true}')
        self.llmctl.registry.add(no_json_provider)
        catalog = load_catalog_config(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "no_json",
                        "provider": "prov_no_json",
                        "model": "m",
                        "supports_json": False,
                    },
                ],
            }
        )
        orch = LLMOrchestrator(self.llmctl, catalog)
        request = RuntimeLLMRequest(
            purpose="validate",
            messages=[Message(role="user", content="return json")],
            output_schema={"type": "object"},
            budget={"timeout_ms": 500, "max_tokens": 100},
        )
        candidate = orch.call(request, "no_json")
        self.assertEqual(candidate.status, "failed")
        self.assertIsNotNone(candidate.error)
        self.assertEqual(candidate.error.code, "INVALID_ARGUMENT")

    def test_ensemble_fallback_single_mode_preserves_semantics(self) -> None:
        policy = AgentLLMPolicy.model_validate(
            {
                "by_purpose": {
                    "plan": {
                        "mode": "ensemble",
                        "providers": ["fail"],
                        "selection_policy": "pick_primary_if_ok",
                    }
                },
                "fallbacks": {
                    "plan": {
                        "fallback_profile_ids": ["fast"],
                        "fallback_mode": "single",
                        "max_fallback_attempts": 1,
                    }
                },
            }
        )
        result = asyncio.run(
            self.orch.call_for_agent("agent-e", "plan", self._request(), policy)
        )
        self.assertIsInstance(result, EnsembleResult)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].profile_id, "fast")
        self.assertEqual(result.candidates[0].status, "success")
        self.assertIsNotNone(result.selection)
        self.assertEqual(result.selection.winner_profile_id, "fast")

    def test_orchestrator_path_invokes_client_tool_policy_stage(self) -> None:
        client = self.orch._get_profile_client("fast")
        original = client._apply_tool_policy_pre
        calls = {"count": 0}

        def _wrapped(request: LLMRequest):
            calls["count"] += 1
            return original(request)

        client._apply_tool_policy_pre = _wrapped  # type: ignore[method-assign]
        candidate = self.orch.call(self._request(), "fast")
        self.assertEqual(candidate.status, "success")
        self.assertEqual(calls["count"], 1)

    def test_orchestrator_path_uses_client_retry_behavior(self) -> None:
        candidate = self.orch.call(self._request(), "flaky")
        self.assertEqual(candidate.status, "success")
        self.assertEqual(self.provider_flaky.calls, 2)
        self.assertEqual(candidate.profile_id, "flaky")

    def test_orchestrator_path_uses_client_normalization(self) -> None:
        candidate = self.orch.call(self._request(), "dict")
        self.assertEqual(candidate.status, "success")
        self.assertEqual(candidate.text, "dict-normalized")
        self.assertEqual(candidate.usage.input_tokens, 2)
        self.assertEqual(candidate.usage.output_tokens, 3)

    def test_orchestrator_path_uses_client_cost_budget_enforcement(self) -> None:
        client = self.orch._get_profile_client("cost")
        client.profile = client.profile.model_copy(
            update={
                "budgets": client.profile.budgets.model_copy(
                    update={"max_cost_usd": 0.1}
                )
            }
        )
        candidate = self.orch.call(self._request(), "cost")
        self.assertEqual(candidate.status, "failed")
        self.assertIsNotNone(candidate.error)
        self.assertEqual(candidate.error.code, "POLICY_DENIED")

    def test_profile_client_cache_is_keyed_by_profile_id(self) -> None:
        fast_a = self.orch._get_profile_client("fast")
        fast_b = self.orch._get_profile_client("fast")
        alt = self.orch._get_profile_client("alt")

        self.assertIs(fast_a, fast_b)
        self.assertIsNot(fast_a, alt)
        self.assertEqual(fast_a.profile.default_provider, "prov_fast")
        self.assertEqual(fast_a.profile.default_model, "m-fast")
        self.assertEqual(alt.profile.default_provider, "prov_alt")
        self.assertEqual(alt.profile.default_model, "m-alt")

    def test_shared_cost_estimator_supports_object_and_mapping_hints(self) -> None:
        usage = UsageInfo(input_tokens=1000, output_tokens=500, total_tokens=1500)
        model_hint = SimpleNamespace(input_per_1k=0.01, output_per_1k=0.02)
        mapping_hint = {"input_per_1k": 0.01, "output_per_1k": 0.02}

        from_object = estimate_usage_cost_usd(usage=usage, cost_hint=model_hint)
        from_mapping = estimate_usage_cost_usd(usage=usage, cost_hint=mapping_hint)

        self.assertEqual(from_object, 0.02)
        self.assertEqual(from_mapping, 0.02)

    def test_orchestrator_cost_estimate_uses_hint_when_provider_cost_missing(
        self,
    ) -> None:
        catalog = load_catalog_config(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "fast_hint",
                        "provider": "prov_fast",
                        "model": "m-fast",
                        "supports_json": True,
                        "cost_hint": {"input_per_1k": 0.01, "output_per_1k": 0.02},
                    }
                ],
            }
        )
        orch = LLMOrchestrator(self.llmctl, catalog)

        candidate = orch.call(self._request(), "fast_hint")
        self.assertEqual(candidate.status, "success")
        self.assertEqual(candidate.usage.cost_estimate, 0.0001)

    def test_orchestrator_cost_estimate_prefers_provider_supplied_cost(self) -> None:
        catalog = load_catalog_config(
            {
                "schema_version": 1,
                "profiles": [
                    {
                        "id": "cost_hint",
                        "provider": "prov_cost",
                        "model": "m-cost",
                        "supports_json": True,
                        "cost_hint": {"input_per_1k": 999.0, "output_per_1k": 999.0},
                    }
                ],
            }
        )
        orch = LLMOrchestrator(self.llmctl, catalog)

        candidate = orch.call(self._request(), "cost_hint")
        self.assertEqual(candidate.status, "success")
        self.assertEqual(candidate.usage.cost_estimate, 2.5)

    def test_orchestrator_cost_estimate_none_when_no_hint_and_no_provider_cost(
        self,
    ) -> None:
        candidate = self.orch.call(self._request(), "fast")
        self.assertEqual(candidate.status, "success")
        self.assertIsNone(candidate.usage.cost_estimate)
