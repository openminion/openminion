from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
import inspect
from typing import Any

from openminion.modules.context.input_boundaries import (
    emit_boundary_event as _pidf_emit_boundary_event,
)
from openminion.modules.tool.family.runtime import run_provider_chain
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime import RuntimeContext

from openminion.tools.config import resolve_tool_context_env
from openminion.tools.env import get_web_search_provider_override
from openminion.modules.tool.runtime.routing import (
    resolve_runtime_provider_chain,
    resolve_runtime_tool_family_config,
)

from .constants import (
    SEARCH_BRAVE_PROVIDER_ALIASES,
    SEARCH_BRAVE_PROVIDER_ID,
    SEARCH_FIRECRAWL_PROVIDER_ALIASES,
    SEARCH_FIRECRAWL_PROVIDER_ID,
    SEARCH_PROVIDER_AUTO,
    SEARCH_SERPAPI_PROVIDER_ALIASES,
    SEARCH_SERPAPI_PROVIDER_ID,
    SEARCH_SERPER_PROVIDER_ALIASES,
    SEARCH_SERPER_PROVIDER_ID,
    SEARCH_TINYFISH_PROVIDER_ALIASES,
    SEARCH_TINYFISH_PROVIDER_ID,
    SEARCH_TAVILY_PROVIDER_ALIASES,
    SEARCH_TAVILY_PROVIDER_ID,
)
from .providers import (
    SearchProvider,
    SearchProviderError,
    provider_registry,
    register_provider as _registry_register_provider,
)
from .schemas import SearchArgs

_PROVIDERS: dict[str, SearchProvider] = provider_registry()._providers  # noqa: SLF001
_PROVIDER_ORDER: list[str] = provider_registry()._provider_order  # noqa: SLF001


def _normalize_provider_id(raw: Any) -> str:
    return str(raw or "").strip().lower()


def register_provider(provider: SearchProvider) -> None:
    provider_id = _normalize_provider_id(getattr(provider, "provider_id", ""))
    if not provider_id:
        raise ValueError("search provider must define provider_id")
    _registry_register_provider(provider)


def list_provider_ids() -> tuple[str, ...]:
    return tuple(provider_registry().list_provider_ids())


def _registered_provider(provider_id: str) -> SearchProvider | None:
    if not provider_id:
        return None
    try:
        return provider_registry().get(provider_id)
    except KeyError:
        return None


def _registered_provider_ids() -> list[str]:
    return provider_registry().list_provider_ids()


def _provider_pref_from_token(raw: Any) -> str:
    token = _normalize_provider_id(raw)
    if _registered_provider(token) is not None:
        return token
    if token in {SEARCH_PROVIDER_AUTO, ""}:
        return SEARCH_PROVIDER_AUTO
    if token in SEARCH_TAVILY_PROVIDER_ALIASES or ".tavily." in token:
        return SEARCH_TAVILY_PROVIDER_ID
    if token in SEARCH_BRAVE_PROVIDER_ALIASES or ".brave." in token:
        return SEARCH_BRAVE_PROVIDER_ID
    if token in SEARCH_SERPAPI_PROVIDER_ALIASES or ".serpapi." in token:
        return SEARCH_SERPAPI_PROVIDER_ID
    if token in SEARCH_FIRECRAWL_PROVIDER_ALIASES or ".firecrawl." in token:
        return SEARCH_FIRECRAWL_PROVIDER_ID
    if token in SEARCH_SERPER_PROVIDER_ALIASES or ".serper." in token:
        return SEARCH_SERPER_PROVIDER_ID
    if token in SEARCH_TINYFISH_PROVIDER_ALIASES or ".tinyfish." in token:
        return SEARCH_TINYFISH_PROVIDER_ID
    return ""


def _policy_provider_order(ctx: RuntimeContext) -> list[str]:
    raw = getattr(getattr(ctx, "policy", None), "raw", {})
    if not isinstance(raw, Mapping):
        return []
    context_metadata = raw.get("context_metadata", {})
    if not isinstance(context_metadata, Mapping):
        return []
    runtime_binding_policies = context_metadata.get("runtime_binding_policies", {})
    if not isinstance(runtime_binding_policies, Mapping):
        return []
    web_policy = runtime_binding_policies.get("runtime.web.search", {})
    if not isinstance(web_policy, Mapping):
        return []

    ordered: list[str] = []
    primary = _provider_pref_from_token(web_policy.get("primary"))
    if primary and primary != "auto":
        ordered.append(primary)
    for token in web_policy.get("fallback_tools", ()):
        candidate = _provider_pref_from_token(token)
        if candidate and candidate != "auto" and candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _resolve_provider_chain(
    validated: SearchArgs, ctx: RuntimeContext
) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    chain: list[str] = []

    requested = _provider_pref_from_token(validated.provider)
    if validated.provider not in {"", "auto"} and not requested:
        raise SearchProviderError(
            f"unsupported search provider '{validated.provider}'",
            code="INVALID_REQUEST",
            details={"supported_provider": sorted(_registered_provider_ids())},
        )

    def _add(candidate: str) -> None:
        token = _normalize_provider_id(candidate)
        if token and token != "auto" and token not in chain:
            chain.append(token)

    family_cfg = resolve_runtime_tool_family_config(ctx, family_name="search")
    env_provider = _provider_pref_from_token(
        get_web_search_provider_override(env=resolve_tool_context_env(ctx))
    )
    hinted_order = list(_policy_provider_order(ctx))
    if env_provider and env_provider != SEARCH_PROVIDER_AUTO:
        hinted_order.append(env_provider)

    if requested and requested != "auto":
        _add(requested)
        if getattr(family_cfg, "allow_fallback", None) is False:
            return chain, warnings

    for provider_id in resolve_runtime_provider_chain(
        available=_registered_provider_ids(),
        family_config=family_cfg,
        hinted_order=hinted_order,
    ):
        _add(provider_id)

    if not chain:
        return [], warnings

    existing = [
        provider_id
        for provider_id in chain
        if _registered_provider(provider_id) is not None
    ]
    if not existing:
        return [], warnings

    healthy: list[str] = []
    explicit_request = requested if requested and requested != "auto" else ""
    for provider_id in existing:
        provider = _registered_provider(provider_id)
        if provider is None:
            continue
        try:
            if _provider_is_healthy(provider, ctx):
                healthy.append(provider_id)
            else:
                warnings.append(f"provider '{provider_id}' reported unhealthy")
        except Exception as exc:
            warnings.append(f"provider '{provider_id}' healthcheck failed: {exc}")

    if explicit_request and explicit_request in existing:
        preferred_chain = [explicit_request]
        preferred_chain.extend(
            provider_id for provider_id in healthy if provider_id != explicit_request
        )
        if len(preferred_chain) > 1 or explicit_request in healthy:
            return preferred_chain, warnings
        return existing, warnings

    if healthy:
        return healthy, warnings
    return existing, warnings


def _provider_is_healthy(provider: SearchProvider, ctx: RuntimeContext) -> bool:
    healthcheck = getattr(provider, "healthcheck", None)
    if not callable(healthcheck):
        return False
    try:
        parameters = inspect.signature(healthcheck).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_ctx_keyword = "ctx" in parameters
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_ctx_keyword or accepts_kwargs:
        return bool(healthcheck(ctx=ctx))
    positional = [
        parameter
        for parameter in parameters.values()
        if parameter.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    if positional:
        return bool(healthcheck(ctx))
    return bool(healthcheck())


def _normalize_results(rows: Any, *, max_results: int) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        return []
    normalized: list[dict[str, Any]] = []
    for idx, item in enumerate(rows[:max_results], start=1):
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title", "") or "").strip() or "Untitled"
        url = str(item.get("url", "") or "").strip()
        description = str(
            item.get("description", item.get("snippet", item.get("content", ""))) or ""
        ).strip()
        record: dict[str, Any] = {
            "rank": int(item.get("rank", idx) or idx),
            "title": title,
            "url": url,
            "description": description,
        }
        snippets = item.get("extra_snippets")
        if isinstance(snippets, list):
            record["extra_snippets"] = [str(value) for value in snippets if str(value)]
        normalized.append(record)
    return normalized


def _normalize_provider_payload(
    *,
    provider_id: str,
    query: str,
    payload: Mapping[str, Any],
    max_results: int,
) -> dict[str, Any]:
    raw_query = payload.get("query", {})
    if isinstance(raw_query, Mapping):
        query_payload = {
            "original": str(raw_query.get("original", query) or query),
            "more_results_available": bool(
                raw_query.get("more_results_available", False)
            ),
        }
    else:
        query_payload = {"original": query, "more_results_available": False}

    normalized = {
        "provider": provider_id,
        "query": query_payload,
        "results": _normalize_results(
            payload.get("results", []), max_results=max_results
        ),
        "warnings": [
            str(item) for item in payload.get("warnings", []) if str(item).strip()
        ],
    }
    answer = str(payload.get("answer", "") or "").strip()
    if answer:
        normalized["answer"] = answer
    rate_limit = payload.get("rate_limit")
    if isinstance(rate_limit, Mapping):
        normalized["rate_limit"] = {
            str(k): str(v) for k, v in rate_limit.items() if str(v).strip()
        }
    return normalized


def _is_verified(payload: Mapping[str, Any]) -> bool:
    rows = payload.get("results", [])
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        url = str(row.get("url", "") or "").strip().lower()
        if url.startswith("https://") or url.startswith("http://"):
            return True
    return False


def _render_content(payload: Mapping[str, Any]) -> str:
    query_payload = payload.get("query", {})
    query = ""
    if isinstance(query_payload, Mapping):
        query = str(query_payload.get("original", "") or "").strip()
    query = query or "unknown query"
    provider = str(payload.get("provider", "") or "unknown")
    rows = payload.get("results", [])
    if not isinstance(rows, list):
        rows = []

    lines = [f'Web search for "{query}" via {provider} returned {len(rows)} result(s).']
    answer = str(payload.get("answer", "") or "").strip()
    if answer:
        lines.append(f"Answer: {answer}")

    for idx, row in enumerate(rows[:3], start=1):
        if not isinstance(row, Mapping):
            continue
        title = str(row.get("title", "") or "").strip() or "Untitled"
        url = str(row.get("url", "") or "").strip() or "unknown-url"
        lines.append(f"{idx}. {title} - {url}")
    rendered = "\n".join(lines)
    _pidf_emit_boundary_event(
        "search_result",
        rendered,
        seam_id="tools.search.plugin.render_content",
        provenance_ref=provider or None,
    )
    return rendered


def _execute_provider(
    *,
    provider_id: str,
    query: str,
    max_results: int,
    args: dict[str, Any],
    ctx: RuntimeContext,
) -> dict[str, Any]:
    provider = _registered_provider(provider_id)
    if provider is None:
        raise SearchProviderError(
            f"provider '{provider_id}' is not registered",
            code="DEPENDENCY_MISSING",
        )
    raw = provider.search(
        query,
        max_results=max_results,
        args=args,
        ctx=ctx,
    )
    if not isinstance(raw, Mapping):
        raise SearchProviderError(
            f"provider '{provider_id}' returned invalid payload",
            code="INVALID_RESPONSE",
        )
    return _normalize_provider_payload(
        provider_id=provider_id,
        query=query,
        payload=raw,
        max_results=max_results,
    )


def _handle_web_search(args: dict[str, Any], ctx: RuntimeContext) -> dict[str, Any]:
    try:
        validated = SearchArgs.model_validate(args)
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "code": "INVALID_REQUEST",
                "message": f"invalid web.search arguments: {exc}",
            },
        }

    provider_chain, warnings = _resolve_provider_chain(validated, ctx)
    if not provider_chain:
        return {
            "ok": False,
            "error": {
                "code": "DEPENDENCY_MISSING",
                "message": "No usable web search provider is configured",
            },
            "data": {
                "available_providers": _registered_provider_ids(),
                "warnings": warnings,
            },
        }

    query = str(validated.query or "").strip()
    max_results = int(validated.max_results)
    shared_args = validated.model_dump(exclude_none=True)
    requested_provider = _provider_pref_from_token(validated.provider)
    query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]

    def _attempt_payload(
        provider_id: str, attempt_index: int, total: int
    ) -> dict[str, Any]:
        return {
            "requested_provider": requested_provider or SEARCH_PROVIDER_AUTO,
            "selected_provider": provider_id,
            "attempt_index": attempt_index,
            "query_hash": query_hash,
        }

    def _invoke(provider_id: str, attempt_index: int) -> dict[str, Any]:
        payload = _execute_provider(
            provider_id=provider_id,
            query=query,
            max_results=max_results,
            args=shared_args,
            ctx=ctx,
        )
        merged_warnings = list(payload.get("warnings", []))
        merged_warnings.extend(warnings)
        if attempt_index > 1:
            merged_warnings.append(
                f"Primary provider failed; fell back to '{provider_id}'"
            )
        payload.setdefault("retrieved_at", datetime.now(timezone.utc).isoformat())
        payload["warnings"] = [
            str(item) for item in merged_warnings if str(item).strip()
        ]
        return {
            "ok": True,
            "content": _render_content(payload),
            "verified": _is_verified(payload),
            "data": payload,
            "source": provider_id,
        }

    def _fallback(
        chain: list[str], failures: list[tuple[str, Exception]]
    ) -> dict[str, Any]:
        last_exc = failures[-1][1] if failures else None
        return {
            "ok": False,
            "error": {
                "code": getattr(last_exc, "code", "UPSTREAM_ERROR"),
                "message": str(last_exc or "web search failed"),
                "details": dict(getattr(last_exc, "details", {}) or {}),
            },
            "data": {
                "provider_chain": chain,
                "warnings": warnings,
            },
        }

    return run_provider_chain(
        ctx,
        chain=provider_chain,
        attempt_event="search.provider.selected",
        attempt_payload_fn=_attempt_payload,
        invoke_fn=_invoke,
        fallback_result_fn=_fallback,
    )


def _handle_web_search_tavily(
    args: dict[str, Any], ctx: RuntimeContext
) -> dict[str, Any]:
    forced = dict(args)
    forced["provider"] = "tavily"
    return _handle_web_search(forced, ctx)


def _handle_web_search_brave(
    args: dict[str, Any], ctx: RuntimeContext
) -> dict[str, Any]:
    forced = dict(args)
    forced["provider"] = "brave"
    return _handle_web_search(forced, ctx)


def _handle_web_search_serpapi(
    args: dict[str, Any], ctx: RuntimeContext
) -> dict[str, Any]:
    forced = dict(args)
    forced["provider"] = "serpapi"
    return _handle_web_search(forced, ctx)


def _handle_web_search_firecrawl(
    args: dict[str, Any], ctx: RuntimeContext
) -> dict[str, Any]:
    forced = dict(args)
    forced["provider"] = "firecrawl"
    return _handle_web_search(forced, ctx)


def _handle_web_search_serper(
    args: dict[str, Any], ctx: RuntimeContext
) -> dict[str, Any]:
    forced = dict(args)
    forced["provider"] = "serper"
    return _handle_web_search(forced, ctx)


def _handle_web_search_tinyfish(
    args: dict[str, Any], ctx: RuntimeContext
) -> dict[str, Any]:
    forced = dict(args)
    forced["provider"] = "tinyfish"
    return _handle_web_search(forced, ctx)


def register(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name="search.dispatch",
            args_model=SearchArgs,
            min_scope="READ_ONLY",
            handler=_handle_web_search,
        )
    )
    registry.add(
        ToolSpec(
            name="search.tavily.search",
            args_model=SearchArgs,
            min_scope="READ_ONLY",
            handler=_handle_web_search_tavily,
        )
    )
    registry.add(
        ToolSpec(
            name="search.brave.search",
            args_model=SearchArgs,
            min_scope="READ_ONLY",
            handler=_handle_web_search_brave,
        )
    )
    registry.add(
        ToolSpec(
            name="search.serpapi.search",
            args_model=SearchArgs,
            min_scope="READ_ONLY",
            handler=_handle_web_search_serpapi,
        )
    )
    registry.add(
        ToolSpec(
            name="search.firecrawl.search",
            args_model=SearchArgs,
            min_scope="READ_ONLY",
            handler=_handle_web_search_firecrawl,
        )
    )
    registry.add(
        ToolSpec(
            name="search.serper.search",
            args_model=SearchArgs,
            min_scope="READ_ONLY",
            handler=_handle_web_search_serper,
        )
    )
    registry.add(
        ToolSpec(
            name="search.tinyfish.search",
            args_model=SearchArgs,
            min_scope="READ_ONLY",
            handler=_handle_web_search_tinyfish,
        )
    )

    try:
        provider_registry().load_entry_points()
    except Exception as exc:  # noqa: BLE001
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "search provider entry-point loading failed: %s", exc
        )


__all__ = ["register", "register_provider", "list_provider_ids"]
