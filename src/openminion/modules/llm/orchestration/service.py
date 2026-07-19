import asyncio
import hashlib
import json
import time
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

from pydantic import ValidationError

from openminion.base.errors.adapt import (
    error_info_from_exception,
    error_info_from_mapping,
)
from openminion.base.config.env import EnvironmentConfig, resolve_environment_config
from openminion.base.config.parse import _as_optional_float
from ..runtime.client import LLMCTL, LLMClient
from ..config import AgentProfile
from ..constants import (
    LLM_CANDIDATE_STATUS_FAILED,
    LLM_CANDIDATE_STATUS_SUCCESS,
    LLM_CANDIDATE_STATUS_TIMEOUT,
)
from ..errors import LLMCtlError
from ..interfaces import ensure_llm_response_compatibility
from ..schemas import LLMRequest, Message, ResponseError
from .coercion import (
    _candidate_profile_id,
    _extract_json_dict,
    _first_positive_int,
    _safe_error_code,
)
from .capabilities import capability_error_details
from .config import load_catalog_config, resolve_route
from .disagreement import aggregate_usage, compute_disagreement
from .schemas import (
    AgentLLMBudgets,
    AgentLLMPolicy,
    CandidateResponse,
    CandidateStatus,
    CatalogLoggingConfig,
    DisagreementCluster,
    DisagreementConfig,
    DisagreementReport,
    EnsembleMode,
    EnsembleResult,
    EnsembleRoute,
    EnsembleTemplate,
    FallbackMode,
    FallbackPolicy,
    GlobalLimits,
    LLMCatalogConfig,
    LLMCatalogDefaults,
    LLMRoute,
    NormalizationRules,
    ProfileCapabilities,
    ProfileCostHint,
    ProviderProfile,
    RequestBudget,
    Rubric,
    RubricCriterion,
    RuntimeLLMRequest,
    SecretResolutionConfig,
    SelectionPolicyName,
    SelectionResult,
    SingleRoute,
    TraceContext,
    Usage,
    UsageTotal,
)
from .selection import (
    apply_budget_clamps,
    apply_ensemble_budget_clamps,
    enforce_route_access,
    expand_self_consistency,
    heuristic_selection,
    resolve_ensemble_provider_ids,
    resolve_ensemble_strategy,
    route_profile_ids,
    select_candidate,
)
from ..providers.cost import estimate_usage_cost_usd

__all__ = [
    "AgentLLMBudgets",
    "AgentLLMPolicy",
    "CandidateResponse",
    "CandidateStatus",
    "CatalogLoggingConfig",
    "DisagreementCluster",
    "DisagreementConfig",
    "DisagreementReport",
    "EnsembleMode",
    "EnsembleResult",
    "EnsembleRoute",
    "EnsembleTemplate",
    "FallbackMode",
    "FallbackPolicy",
    "GlobalLimits",
    "LLMCatalogConfig",
    "LLMCatalogDefaults",
    "LLMOrchestrator",
    "LLMRoute",
    "NormalizationRules",
    "ProfileCapabilities",
    "ProfileCostHint",
    "ProviderProfile",
    "RequestBudget",
    "ResponseError",
    "Rubric",
    "RubricCriterion",
    "RuntimeLLMRequest",
    "SecretResolutionConfig",
    "SelectionPolicyName",
    "SelectionResult",
    "SingleRoute",
    "TraceContext",
    "Usage",
    "UsageTotal",
    "load_catalog_config",
    "resolve_route",
]


class LLMOrchestrator:
    def __init__(
        self,
        llmctl: LLMCTL,
        catalog: Union[LLMCatalogConfig, Dict[str, Any]],
        *,
        env: EnvironmentConfig | Dict[str, object] | None = None,
    ) -> None:
        ensure_llm_response_compatibility(llmctl, component_name="llmctl")
        self.llmctl = llmctl
        self._env = resolve_environment_config(env=env)
        self.catalog = load_catalog_config(catalog)
        self._profiles = {item.id: item for item in self.catalog.profiles}
        self._ensembles = {item.id: item for item in self.catalog.ensembles}
        self._clients_by_profile_id: Dict[str, LLMClient] = {}
        self.events: List[Dict[str, Any]] = []
        self.artifacts: Dict[str, Dict[str, Any]] = {}
        max_inflight = self.catalog.limits.max_inflight_requests
        self._inflight_semaphore: Optional[asyncio.Semaphore] = None
        if isinstance(max_inflight, int) and max_inflight > 0:
            self._inflight_semaphore = asyncio.Semaphore(max_inflight)

    def call(
        self, request: Union[RuntimeLLMRequest, Dict[str, Any]], profile_id: str
    ) -> CandidateResponse:
        req = (
            request
            if isinstance(request, RuntimeLLMRequest)
            else RuntimeLLMRequest.model_validate(request)
        )
        self._emit_event(
            "llm.request.started",
            {
                "request_id": req.request_id,
                "agent_id": req.trace.agent_id,
                "purpose": req.purpose,
                "mode": "single",
                "profile_id": profile_id,
            },
        )
        candidate = self._call_candidate_sync(
            req, profile_id=profile_id, candidate_id="cand-0"
        )
        return candidate

    async def call_parallel(
        self,
        request: Union[RuntimeLLMRequest, Dict[str, Any]],
        providers: List[str],
        strategy: Union[EnsembleTemplate, Dict[str, Any]],
    ) -> EnsembleResult:
        req = (
            request
            if isinstance(request, RuntimeLLMRequest)
            else RuntimeLLMRequest.model_validate(request)
        )
        resolved_strategy = (
            strategy
            if isinstance(strategy, EnsembleTemplate)
            else EnsembleTemplate.model_validate(strategy)
        )
        provider_ids = list(providers or resolved_strategy.providers)
        if not provider_ids:
            raise LLMCtlError("INVALID_ARGUMENT", "call_parallel requires provider ids")

        self._emit_event(
            "llm.request.started",
            {
                "request_id": req.request_id,
                "agent_id": req.trace.agent_id,
                "purpose": req.purpose,
                "mode": resolved_strategy.mode,
                "providers": provider_ids,
            },
        )

        provider_ids = self._expand_self_consistency(provider_ids, resolved_strategy)
        indexed_candidates = list(enumerate(provider_ids))
        per_candidate_timeout_ms = _first_positive_int(
            req.budget.timeout_ms,
            resolved_strategy.timeout_ms,
            self.catalog.defaults.default_timeout_ms,
        )
        max_parallel = max(
            1,
            min(
                _first_positive_int(
                    resolved_strategy.max_parallel,
                    self.catalog.defaults.default_max_parallel,
                    1,
                ),
                max(1, self.catalog.limits.max_parallel_global),
            ),
        )
        semaphore = asyncio.Semaphore(max_parallel)
        task_map: Dict[asyncio.Task[CandidateResponse], Tuple[int, str]] = {}

        async def _run_candidate(index: int, profile_id: str) -> CandidateResponse:
            async with semaphore:
                coro = asyncio.to_thread(
                    self._call_candidate_sync,
                    req,
                    profile_id,
                    f"cand-{index}",
                )
                timeout_s = max(0.001, float(per_candidate_timeout_ms) / 1000.0)
                try:
                    if self._inflight_semaphore is None:
                        return await asyncio.wait_for(coro, timeout=timeout_s)
                    async with self._inflight_semaphore:
                        return await asyncio.wait_for(coro, timeout=timeout_s)
                except asyncio.TimeoutError:
                    return CandidateResponse(
                        candidate_id=f"cand-{index}",
                        profile_id=profile_id,
                        provider=self._profile_provider(profile_id),
                        model=self._profile_model(profile_id),
                        status=LLM_CANDIDATE_STATUS_TIMEOUT,
                        text=None,
                        json_output=None,
                        usage=Usage(latency_ms=per_candidate_timeout_ms),
                        error=ResponseError(
                            code="TIMEOUT",
                            message="Candidate timed out",
                            details={
                                "timeout_ms": per_candidate_timeout_ms,
                                "profile_id": profile_id,
                            },
                        ),
                        raw_artifact_ref=None,
                    )
                except Exception as exc:
                    profile = self._get_profile(profile_id)
                    return CandidateResponse(
                        candidate_id=f"cand-{index}",
                        profile_id=profile_id,
                        provider=profile.provider,
                        model=profile.model,
                        status=LLM_CANDIDATE_STATUS_FAILED,
                        text=None,
                        json_output=None,
                        usage=Usage(latency_ms=0),
                        error=ResponseError(
                            code="PROVIDER_ERROR",
                            message=f"{type(exc).__name__}: {exc}",
                            details={"profile_id": profile_id},
                        ),
                        raw_artifact_ref=None,
                    )

        for index, profile_id in indexed_candidates:
            task = asyncio.create_task(_run_candidate(index, profile_id))
            task_map[task] = (index, profile_id)

        completed: Dict[int, CandidateResponse] = {}
        try:
            for finished in asyncio.as_completed(task_map.keys()):
                result = await finished
                index = (
                    int(result.candidate_id.split("-")[-1])
                    if result.candidate_id.startswith("cand-")
                    else -1
                )
                if index >= 0:
                    completed[index] = result
                if (
                    resolved_strategy.stop_early
                    and resolved_strategy.selection_policy == "pick_primary_if_ok"
                    and index == 0
                    and result.status == LLM_CANDIDATE_STATUS_SUCCESS
                ):
                    for task in task_map:
                        if not task.done():
                            task.cancel()
                    break
        finally:
            for task, (index, profile_id) in task_map.items():
                if task.done():
                    continue
                task.cancel()
                completed[index] = CandidateResponse(
                    candidate_id=f"cand-{index}",
                    profile_id=profile_id,
                    provider=self._profile_provider(profile_id),
                    model=self._profile_model(profile_id),
                    status=LLM_CANDIDATE_STATUS_FAILED,
                    text=None,
                    json_output=None,
                    usage=Usage(latency_ms=0),
                    error=ResponseError(
                        code="PROVIDER_ERROR",
                        message="Candidate cancelled by stop_early",
                        details={"profile_id": profile_id},
                    ),
                    raw_artifact_ref=None,
                )

        ordered_candidates = [
            completed[index] for index, _ in indexed_candidates if index in completed
        ]
        selection = self._select_candidate(
            request=req,
            candidates=ordered_candidates,
            strategy=resolved_strategy,
        )
        disagreement = self._compute_disagreement(
            ordered_candidates, resolved_strategy.disagreement
        )
        usage_total = self._aggregate_usage(ordered_candidates)
        result = EnsembleResult(
            request_id=req.request_id,
            mode=resolved_strategy.mode,
            candidates=ordered_candidates,
            selection=selection,
            disagreement=disagreement,
            usage_total=usage_total,
        )

        self._emit_event(
            "llm.ensemble.completed",
            {
                "request_id": req.request_id,
                "mode": resolved_strategy.mode,
                "winner_candidate_id": selection.winner_candidate_id
                if selection
                else None,
            },
        )
        if self.catalog.logging.store_ensemble_report:
            self._store_artifact(
                alias=f"session:{req.trace.session_id or 'none'}/llm/{req.request_id}/ensemble",
                payload=result.model_dump(mode="json"),
            )
        return result

    async def call_for_agent(
        self,
        agent_id: str,
        purpose: str,
        request: Union[RuntimeLLMRequest, Dict[str, Any]],
        agent_policy: Union[AgentLLMPolicy, Dict[str, Any]],
    ) -> Union[CandidateResponse, EnsembleResult]:
        req = (
            request
            if isinstance(request, RuntimeLLMRequest)
            else RuntimeLLMRequest.model_validate(request)
        )
        policy = (
            agent_policy
            if isinstance(agent_policy, AgentLLMPolicy)
            else AgentLLMPolicy.model_validate(agent_policy)
        )
        req = req.model_copy(
            update={
                "purpose": purpose,
                "trace": req.trace.model_copy(update={"agent_id": agent_id}),
            }
        )
        route = resolve_route(policy, purpose)
        self._enforce_route_access(route, policy)
        req = self._apply_budget_clamps(req, policy.budgets)

        fallback = policy.fallbacks.get(purpose)
        if isinstance(route, SingleRoute):
            result = self.call(req, route.profile_id)
            if result.status == LLM_CANDIDATE_STATUS_SUCCESS:
                return result
            return await self._apply_fallback_single(req, result, fallback)

        strategy = self._resolve_ensemble_strategy(route)
        providers = self._resolve_ensemble_provider_ids(route, strategy)
        strategy = self._apply_ensemble_budget_clamps(strategy, route, policy.budgets)
        ensemble = await self.call_parallel(req, providers=providers, strategy=strategy)
        if any(
            item.status == LLM_CANDIDATE_STATUS_SUCCESS for item in ensemble.candidates
        ):
            return ensemble
        return await self._apply_fallback_ensemble(req, ensemble, fallback, strategy)

    def judge(
        self,
        request: RuntimeLLMRequest,
        candidates: List[CandidateResponse],
        judge_profile_id: str,
        rubric: Optional[Rubric] = None,
    ) -> SelectionResult:
        if not candidates:
            raise LLMCtlError(
                "INVALID_ARGUMENT", "judge requires at least one candidate"
            )
        prompt_lines = [
            "You are selecting the best candidate response.",
            "Return strict JSON with keys: winner_candidate_id, reasons, scores.",
            f"Purpose: {request.purpose}",
        ]
        if rubric is not None:
            prompt_lines.append(f"Rubric: {rubric.model_dump_json()}")
        prompt_lines.append("Candidates:")
        for item in candidates:
            text_excerpt = (item.text or "")[:1200]
            prompt_lines.append(
                f"- {item.candidate_id} ({item.profile_id}): {text_excerpt}"
            )

        judge_request = RuntimeLLMRequest(
            request_id=f"{request.request_id}:judge",
            purpose="judge",
            messages=[Message(role="user", content="\n".join(prompt_lines))],
            budget=request.budget,
            trace=request.trace,
            metadata={"judge_for_request_id": request.request_id},
        )
        judge_candidate = self.call(judge_request, profile_id=judge_profile_id)
        if judge_candidate.status == LLM_CANDIDATE_STATUS_SUCCESS:
            payload = _extract_json_dict(judge_candidate.text or "")
            if payload:
                winner_candidate_id = str(
                    payload.get("winner_candidate_id", "")
                ).strip()
                if winner_candidate_id:
                    winner_profile = _candidate_profile_id(
                        candidates, winner_candidate_id
                    )
                    if winner_profile is not None:
                        reasons = payload.get("reasons")
                        scores = payload.get("scores")
                        self._emit_event(
                            "llm.judge.completed",
                            {
                                "request_id": request.request_id,
                                "winner_candidate_id": winner_candidate_id,
                                "judge_profile_id": judge_profile_id,
                            },
                        )
                        return SelectionResult(
                            winner_candidate_id=winner_candidate_id,
                            winner_profile_id=winner_profile,
                            scores=scores if isinstance(scores, dict) else None,
                            reasons=reasons
                            if isinstance(reasons, list)
                            else ["Judge selected winner"],
                            risk_flags=None,
                        )

        return self._heuristic_selection(candidates)

    def _call_candidate_sync(
        self, request: RuntimeLLMRequest, profile_id: str, candidate_id: str
    ) -> CandidateResponse:
        started = time.perf_counter()
        profile = self._get_profile(profile_id)
        capability_error = capability_error_details(profile, request)
        if capability_error is not None:
            return self._candidate_error(
                candidate_id=candidate_id,
                profile=profile,
                code="INVALID_ARGUMENT",
                message="Provider profile does not satisfy the request capabilities",
                details=capability_error,
                started=started,
            )
        provider_name = profile.provider
        model_name = profile.model
        call_request = self._build_provider_request(request, profile)
        provider_raw: Optional[Dict[str, Any]] = None
        client = self._get_profile_client(profile_id)

        try:
            response = client.call_sync(call_request)
        except LLMCtlError as exc:
            return self._candidate_error(
                candidate_id=candidate_id,
                profile=profile,
                error=exc,
                started=started,
            )
        except ValidationError as exc:
            return self._candidate_error(
                candidate_id=candidate_id,
                profile=profile,
                error={
                    "code": "INTERNAL_ERROR",
                    "message": "Provider response schema validation failed",
                    "details": {"errors": exc.errors()},
                },
                started=started,
            )
        except Exception as exc:
            return self._candidate_error(
                candidate_id=candidate_id,
                profile=profile,
                error={
                    "code": "PROVIDER_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                },
                started=started,
            )

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        status: CandidateStatus
        if response.ok:
            status = LLM_CANDIDATE_STATUS_SUCCESS
        elif response.error is not None and response.error.code == "TIMEOUT":
            status = LLM_CANDIDATE_STATUS_TIMEOUT
        else:
            status = LLM_CANDIDATE_STATUS_FAILED

        usage = response.usage
        cost_estimate = response.cost_usd
        if cost_estimate is None:
            cost_estimate = estimate_usage_cost_usd(
                usage=usage, cost_hint=profile.cost_hint
            )
        parsed_json = _extract_json_dict(response.output_text or "")
        if request.output_schema is None:
            parsed_json = None

        raw_ref: Optional[str] = None
        if (
            self.catalog.logging.store_raw_provider_payloads
            and response.provider_raw is not None
        ):
            provider_raw = response.provider_raw
            raw_ref = self._store_artifact(
                alias=f"session:{request.trace.session_id or 'none'}/llm/{request.request_id}/candidate/{candidate_id}",
                payload=provider_raw,
            )

        candidate = CandidateResponse(
            candidate_id=candidate_id,
            profile_id=profile.id,
            provider=response.provider or provider_name,
            model=response.model or model_name,
            status=status,
            text=response.output_text or None,
            json_output=parsed_json,
            usage=Usage(
                latency_ms=max(elapsed_ms, response.latency_ms),
                input_tokens=int(usage.input_tokens or 0),
                output_tokens=int(usage.output_tokens or 0),
                cost_estimate=cost_estimate,
            ),
            error=response.error,
            raw_artifact_ref=raw_ref,
        )
        self._emit_event(
            "llm.candidate.finished",
            {
                "request_id": request.request_id,
                "candidate_id": candidate_id,
                "profile_id": profile.id,
                "status": status,
                "latency_ms": candidate.usage.latency_ms,
            },
        )
        return candidate

    def _build_provider_request(
        self, request: RuntimeLLMRequest, profile: ProviderProfile
    ) -> LLMRequest:
        params = dict(profile.params or {})
        max_tokens = _first_positive_int(
            request.budget.max_tokens,
            int(params.get("max_tokens", 0))
            if str(params.get("max_tokens", "")).strip()
            else 0,
            int(params.get("max_output_tokens", 0))
            if str(params.get("max_output_tokens", "")).strip()
            else 0,
            self.catalog.limits.max_tokens_per_call_hard,
        )
        max_tokens = min(
            max_tokens, max(1, self.catalog.limits.max_tokens_per_call_hard)
        )
        metadata = dict(request.metadata)
        if request.output_schema is not None:
            metadata["output_schema"] = request.output_schema
        if request.constraints is not None:
            metadata["constraints"] = request.constraints
        if request.trace.trace_id:
            metadata["trace_id"] = request.trace.trace_id
        if request.trace.session_id:
            metadata["session_id"] = request.trace.session_id
        if request.trace.agent_id:
            metadata["agent_id"] = request.trace.agent_id

        return LLMRequest(
            provider=profile.provider,
            model=profile.model,
            messages=list(request.messages),
            temperature=_as_optional_float(params.get("temperature")),
            top_p=_as_optional_float(params.get("top_p")),
            max_output_tokens=max_tokens,
            stop=params.get("stop") if isinstance(params.get("stop"), list) else None,
            stream=bool(params.get("stream", False)),
            metadata=metadata,
        )

    def _select_candidate(
        self,
        *,
        request: RuntimeLLMRequest,
        candidates: List[CandidateResponse],
        strategy: EnsembleTemplate,
    ) -> Optional[SelectionResult]:
        return select_candidate(
            request=request,
            candidates=candidates,
            strategy=strategy,
            judge=self.judge,
            heuristic_selector=self._heuristic_selection,
        )

    def _heuristic_selection(
        self, candidates: List[CandidateResponse]
    ) -> SelectionResult:
        return heuristic_selection(candidates)

    def _compute_disagreement(
        self,
        candidates: List[CandidateResponse],
        config: Optional[DisagreementConfig],
    ) -> Optional[DisagreementReport]:
        return compute_disagreement(candidates, config)

    def _aggregate_usage(self, candidates: List[CandidateResponse]) -> UsageTotal:
        return aggregate_usage(candidates)

    def _resolve_ensemble_strategy(self, route: EnsembleRoute) -> EnsembleTemplate:
        return resolve_ensemble_strategy(
            route=route,
            ensembles=self._ensembles,
            defaults=self.catalog.defaults,
        )

    def _resolve_ensemble_provider_ids(
        self, route: EnsembleRoute, strategy: EnsembleTemplate
    ) -> List[str]:
        return resolve_ensemble_provider_ids(
            route,
            strategy,
            resolve_profile=self._get_profile,
        )

    def _expand_self_consistency(
        self, providers: List[str], strategy: EnsembleTemplate
    ) -> List[str]:
        return expand_self_consistency(providers, strategy)

    async def _apply_fallback_single(
        self,
        request: RuntimeLLMRequest,
        original: CandidateResponse,
        fallback: Optional[FallbackPolicy],
    ) -> CandidateResponse:
        if fallback is None or fallback.fallback_mode != "single":
            return original
        error_code = (
            original.error.code if original.error is not None else "PROVIDER_ERROR"
        )
        if fallback.on_error_codes and error_code not in set(fallback.on_error_codes):
            return original
        attempts = max(0, int(fallback.max_fallback_attempts))
        if attempts == 0:
            return original
        fallback_ids = list(fallback.fallback_profile_ids or [])
        for index, profile_id in enumerate(fallback_ids[:attempts]):
            candidate = await asyncio.to_thread(self.call, request, profile_id)
            if candidate.status == LLM_CANDIDATE_STATUS_SUCCESS:
                return candidate
            if index + 1 >= attempts:
                return candidate
        return original

    async def _apply_fallback_ensemble(
        self,
        request: RuntimeLLMRequest,
        original: EnsembleResult,
        fallback: Optional[FallbackPolicy],
        strategy: EnsembleTemplate,
    ) -> EnsembleResult:
        if fallback is None:
            return original
        if fallback.fallback_mode == "single":
            attempts = max(0, int(fallback.max_fallback_attempts))
            for profile_id in list(fallback.fallback_profile_ids or [])[:attempts]:
                candidate = await asyncio.to_thread(self.call, request, profile_id)
                if candidate.status == LLM_CANDIDATE_STATUS_SUCCESS:
                    return EnsembleResult(
                        request_id=request.request_id,
                        mode=strategy.mode,
                        candidates=[candidate],
                        selection=SelectionResult(
                            winner_candidate_id=candidate.candidate_id,
                            winner_profile_id=candidate.profile_id,
                            scores=None,
                            reasons=["Fallback single profile selected"],
                            risk_flags=["fallback"],
                        ),
                        disagreement=None,
                        usage_total=self._aggregate_usage([candidate]),
                    )
            return original

        fallback_strategy = strategy.model_copy(
            update={"providers": list(fallback.fallback_profile_ids or [])}
        )
        attempts = max(0, int(fallback.max_fallback_attempts))
        for _ in range(attempts):
            candidate_result = await self.call_parallel(
                request, fallback_strategy.providers, fallback_strategy
            )
            if any(
                item.status == LLM_CANDIDATE_STATUS_SUCCESS
                for item in candidate_result.candidates
            ):
                return candidate_result
        return original

    def _enforce_route_access(self, route: LLMRoute, policy: AgentLLMPolicy) -> None:
        enforce_route_access(
            route,
            policy,
            route_profile_ids=self._route_profile_ids,
        )

    def _route_profile_ids(self, route: LLMRoute) -> List[str]:
        return route_profile_ids(
            route,
            ensembles=self._ensembles,
            resolve_profile=self._get_profile,
        )

    def _apply_budget_clamps(
        self, request: RuntimeLLMRequest, budgets: AgentLLMBudgets
    ) -> RuntimeLLMRequest:
        return apply_budget_clamps(
            request,
            budgets,
            max_tokens_per_call_hard=self.catalog.limits.max_tokens_per_call_hard,
        )

    def _apply_ensemble_budget_clamps(
        self,
        strategy: EnsembleTemplate,
        route: EnsembleRoute,
        budgets: AgentLLMBudgets,
    ) -> EnsembleTemplate:
        return apply_ensemble_budget_clamps(
            strategy,
            route,
            budgets,
            max_parallel_global=self.catalog.limits.max_parallel_global,
        )

    def _candidate_error(
        self,
        *,
        candidate_id: str,
        profile: ProviderProfile,
        code: Optional[str] = None,
        message: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        error: BaseException | Mapping[str, Any] | None = None,
        started: float,
    ) -> CandidateResponse:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if isinstance(error, BaseException):
            normalized_error = error_info_from_exception(error, namespace="llm")
        elif isinstance(error, Mapping):
            normalized_error = error_info_from_mapping(error, namespace="llm")
        else:
            normalized_error = error_info_from_mapping(
                {"code": code, "message": message, "details": details},
                namespace="llm",
            )
        safe_code = _safe_error_code(normalized_error.code)
        status: CandidateStatus = LLM_CANDIDATE_STATUS_FAILED
        if safe_code == "TIMEOUT":
            status = LLM_CANDIDATE_STATUS_TIMEOUT
        candidate = CandidateResponse(
            candidate_id=candidate_id,
            profile_id=profile.id,
            provider=profile.provider,
            model=profile.model,
            status=status,
            text=None,
            json_output=None,
            usage=Usage(
                latency_ms=elapsed_ms,
                input_tokens=0,
                output_tokens=0,
                cost_estimate=None,
            ),
            error=ResponseError(
                code=safe_code,
                message=normalized_error.message,
                details=normalized_error.details,
            ),
            raw_artifact_ref=None,
        )
        self._emit_event(
            "llm.candidate.finished",
            {
                "candidate_id": candidate_id,
                "profile_id": profile.id,
                "status": status,
                "latency_ms": elapsed_ms,
            },
        )
        return candidate

    def _profile_provider(self, profile_id: str) -> str:
        return self._get_profile(profile_id).provider

    def _profile_model(self, profile_id: str) -> str:
        return self._get_profile(profile_id).model

    def _get_profile_client(self, profile_id: str) -> "LLMClient":
        cached = self._clients_by_profile_id.get(profile_id)
        if cached is not None:
            return cached

        profile = self._get_profile(profile_id)
        client_profile = AgentProfile(
            name=f"orchestrator:{profile.id}",
            default_provider=profile.provider,
            default_model=profile.model,
        )
        client = self.llmctl.client(profile=client_profile)
        self._clients_by_profile_id[profile_id] = client
        return client

    def _get_profile(self, profile_id: str) -> ProviderProfile:
        if profile_id not in self._profiles:
            raise LLMCtlError(
                "INVALID_ARGUMENT",
                f"Unknown profile_id: {profile_id}",
                {"profile_id": profile_id},
            )
        return self._profiles[profile_id]

    def _emit_event(self, event_type: str, payload: Dict[str, Any]) -> None:
        if not self.catalog.logging.emit_events:
            return
        envelope = {
            "type": event_type,
            "ts_ms": int(time.time() * 1000),
            "payload": dict(payload),
        }
        self.events.append(envelope)

    def _store_artifact(self, *, alias: str, payload: Dict[str, Any]) -> str:
        data = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        digest = hashlib.sha256(data).hexdigest()
        ref = f"artifact://sha256/{digest}"
        self.artifacts[ref] = {"alias": alias, "payload": payload}
        return ref
