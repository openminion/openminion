import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Any

from openminion.modules.context.input_boundaries import (
    emit_boundary_event as _pidf_emit_boundary_event,
)
from openminion.modules.tool.family.events import emit_family_event
from openminion.modules.tool.family.policy import is_tool_disabled_by_policy
from openminion.modules.tool.family.runtime import StopChain, run_provider_chain
from openminion.modules.tool.registry import ToolRegistry, ToolSpec
from openminion.modules.tool.runtime import RuntimeContext
from openminion.modules.tool.runtime import preferred_artifact_ref
from openminion.modules.tool.runtime.routing import (
    resolve_runtime_provider_chain,
    resolve_runtime_tool_family_config,
)

from .constants import FETCH_ARTIFACTS_SUBDIR
from .policy import (
    FetchPolicyError,
    enforce_url_policy,
    resolve_allow_private_hosts,
)
from .providers.core_http import provider as _core_http_provider
from .schemas import FetchGetArgs, FetchHeadArgs, FetchProvidersArgs
from .providers import provider_registry, register_provider


class _FetchBackendError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = str(code or "UPSTREAM_ERROR")
        self.details = dict(details or {})
        super().__init__(str(message or "Fetch failed"))


def _error(
    code: str, message: str, *, details: dict[str, Any] | None = None, method: str = ""
) -> dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "details": dict(details or {}),
        },
        "data": {
            "source": "openminion-tool-fetch",
            "method": method,
            "error_code": code,
            "reason_code": str(code).lower(),
        },
    }


def _ensure_provider_registry() -> Any:
    register_provider(_core_http_provider)
    registry = provider_registry()
    try:
        registry.load_entry_points()
    except Exception:
        pass
    return registry


def _normalize_backend_token(value: str) -> str:
    token = str(value or "").strip().lower()
    if token in {"core", "core-http"}:
        return "core-http"
    return token


def _provider_option_block(
    provider_options: dict[str, Any], provider_name: str
) -> dict[str, Any]:
    raw = provider_options.get(provider_name, {})
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def _requested_backend_name(request: dict[str, Any], *, available: set[str]) -> str:
    preferred_token = request.get("prefer_backend")
    if preferred_token in (None, ""):
        preferred_token = request.get("backend", "auto")
    preferred = _normalize_backend_token(str(preferred_token or "auto"))
    if preferred and preferred != "auto":
        if preferred not in available:
            raise ValueError(preferred)
        return preferred
    return ""


def _choose_provider_name(request: dict[str, Any], *, available: set[str]) -> str:
    explicit = _requested_backend_name(request, available=available)
    if explicit:
        return explicit
    provider_options = request.get("provider_options", {})
    if isinstance(provider_options, dict):
        tinyfish_cfg = _provider_option_block(provider_options, "tinyfish")
        if tinyfish_cfg and "tinyfish" in available:
            return "tinyfish"
        firecrawl_cfg = _provider_option_block(provider_options, "firecrawl")
        if firecrawl_cfg and "firecrawl" in available:
            return "firecrawl"
        scrapling_cfg = _provider_option_block(provider_options, "scrapling")
        if scrapling_cfg:
            mode = str(scrapling_cfg.get("mode", "auto") or "auto").strip().lower()
            if mode in {"static", "dynamic", "stealth"} and "scrapling" in available:
                return "scrapling"

    return "core-http"


def _hinted_backend_order(request: dict[str, Any], *, available: set[str]) -> list[str]:
    provider_options = request.get("provider_options", {})
    if not isinstance(provider_options, dict):
        return []
    hinted: list[str] = []
    tinyfish_cfg = _provider_option_block(provider_options, "tinyfish")
    if tinyfish_cfg and "tinyfish" in available:
        hinted.append("tinyfish")
    firecrawl_cfg = _provider_option_block(provider_options, "firecrawl")
    if firecrawl_cfg and "firecrawl" in available:
        hinted.append("firecrawl")
    scrapling_cfg = _provider_option_block(provider_options, "scrapling")
    if scrapling_cfg:
        mode = str(scrapling_cfg.get("mode", "auto") or "auto").strip().lower()
        if mode in {"static", "dynamic", "stealth"} and "scrapling" in available:
            hinted.append("scrapling")
    return hinted


def _resolve_provider_chain(
    request: dict[str, Any],
    ctx: Any,
    *,
    available: set[str],
) -> list[str]:
    explicit = _requested_backend_name(request, available=available)
    if explicit:
        return [explicit]

    family_cfg = resolve_runtime_tool_family_config(ctx, family_name="fetch")
    if family_cfg is None:
        return [_choose_provider_name(request, available=available)]

    ordered = resolve_runtime_provider_chain(
        available=sorted(available),
        family_config=family_cfg,
        hinted_order=_hinted_backend_order(request, available=available),
    )
    return ordered


def _store_artifact(ctx: Any, *, token: str, payload: bytes, mime: str) -> str:
    if not isinstance(ctx, RuntimeContext):
        return ""
    digest = hashlib.sha256(payload).hexdigest()
    rel = f"{FETCH_ARTIFACTS_SUBDIR}/{token}-{digest[:12]}"
    if mime == "application/json":
        rel += ".json"
    elif mime.startswith("text/"):
        rel += ".txt"
    else:
        rel += ".bin"
    try:
        artifact = ctx.write_artifact(rel, payload, mime, durable=True)
        return preferred_artifact_ref(artifact)
    except Exception:
        return ""


def _build_preview(text: str, *, max_chars: int = 1200) -> tuple[str, bool]:
    normalized = str(text or "").strip()
    if len(normalized) <= max_chars:
        return normalized, False
    return normalized[:max_chars], True


def _format_get_response(
    *,
    request: dict[str, Any],
    result: dict[str, Any],
    backend_name: str,
    ctx: Any,
) -> dict[str, Any]:
    raw_payload = result.get("raw_body", b"")
    if isinstance(raw_payload, str):
        raw_payload_bytes = raw_payload.encode("utf-8", errors="replace")
    elif isinstance(raw_payload, (bytes, bytearray)):
        raw_payload_bytes = bytes(raw_payload)
    else:
        raw_payload_bytes = b""

    extracted_text = str(result.get("extracted_text", "") or "")
    content_type = str(result.get("content_type", "") or "")
    warnings = [str(item) for item in result.get("warnings", []) if str(item).strip()]
    max_chars = int(request.get("max_response_chars", 1200))
    preview, truncated = _build_preview(extracted_text, max_chars=max_chars)
    if truncated:
        warnings.append("TRUNCATED_PREVIEW")

    fetched_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    result_meta = result.get("meta", {})
    hash_token = ""
    if isinstance(result_meta, dict):
        hash_token = str(result_meta.get("hash", "")).strip()
    if not hash_token:
        hash_token = f"sha256:{hashlib.sha256(raw_payload_bytes).hexdigest()}"

    metadata_payload = {
        "request_url": str(request.get("url", "") or ""),
        "final_url": str(result.get("final_url", "") or ""),
        "status_code": int(result.get("status_code", 0) or 0),
        "content_type": content_type,
        "content_bytes": int(result.get("content_bytes", len(raw_payload_bytes)) or 0),
        "backend": backend_name,
        "hash": hash_token,
        "warnings": warnings,
    }
    metadata_bytes = json.dumps(
        metadata_payload, ensure_ascii=True, sort_keys=True
    ).encode("utf-8")
    extracted_bytes = extracted_text.encode("utf-8", errors="replace")

    artifacts = {
        "raw_body": _store_artifact(
            ctx,
            token="raw_body",
            payload=raw_payload_bytes,
            mime=content_type or "application/octet-stream",
        ),
        "extracted_text": _store_artifact(
            ctx, token="extracted_text", payload=extracted_bytes, mime="text/plain"
        ),
        "metadata_json": _store_artifact(
            ctx, token="metadata", payload=metadata_bytes, mime="application/json"
        ),
    }

    payload = {
        "ok": True,
        "final_url": str(result.get("final_url", "") or ""),
        "status_code": int(result.get("status_code", 0) or 0),
        "content_type": content_type,
        "fetched_at": fetched_at,
        "backend": backend_name,
        "title": str(result.get("title", "") or ""),
        "language": str(result.get("language", "") or ""),
        "text_preview": preview,
        "content_bytes": int(result.get("content_bytes", len(raw_payload_bytes)) or 0),
        "hash": hash_token,
        "artifacts": artifacts,
        "warnings": warnings,
        "verified": True,
    }
    return payload


def _format_head_response(
    *, result: dict[str, Any], backend_name: str
) -> dict[str, Any]:
    headers = result.get("headers", {})
    if not isinstance(headers, dict):
        headers = {}
    return {
        "ok": True,
        "final_url": str(result.get("final_url", "") or ""),
        "status_code": int(result.get("status_code", 0) or 0),
        "content_type": str(result.get("content_type", "") or ""),
        "content_length": int(headers.get("content-length", 0) or 0),
        "headers": {str(k): str(v) for k, v in headers.items()},
        "backend": backend_name,
        "warnings": [
            str(item) for item in result.get("warnings", []) if str(item).strip()
        ],
        "verified": True,
    }


def _invoke_fetch(args: dict[str, Any], ctx: Any, *, method: str) -> dict[str, Any]:
    if is_tool_disabled_by_policy(ctx, "fetch"):
        payload = _error(
            "POLICY_DENIED",
            "fetch tool is disabled by policy",
            method=f"fetch.{method.lower()}",
        )
        emit_family_event(ctx, event="fetch.blocked", payload={"code": "POLICY_DENIED"})
        return payload

    started = time.monotonic()

    # enforce shared URL / SSRF policy at the facade level so that any
    try:
        enforce_url_policy(
            str(args.get("url", "") or ""),
            allow_private_hosts=resolve_allow_private_hosts(args, ctx),
        )
    except FetchPolicyError as exc:
        emit_family_event(
            ctx,
            event="fetch.blocked",
            payload={"code": exc.code},
        )
        return _error(
            exc.code,
            exc.message,
            details=dict(exc.details),
            method=f"fetch.{method.lower()}",
        )

    registry = _ensure_provider_registry()
    available_names = set(registry.list_names())
    try:
        provider_chain = _resolve_provider_chain(args, ctx, available=available_names)
    except ValueError as exc:
        missing = str(exc)
        return _error(
            "BACKEND_NOT_AVAILABLE",
            f"Requested backend is unavailable: {missing}",
            details={"backend": missing, "available": sorted(available_names)},
            method=f"fetch.{method.lower()}",
        )
    if not provider_chain:
        return _error(
            "DEPENDENCY_MISSING",
            "No usable fetch backend is configured",
            details={"available": sorted(available_names)},
            method=f"fetch.{method.lower()}",
        )

    request = dict(args)
    request["method"] = method
    preferred = _normalize_backend_token(
        str(request.get("prefer_backend", request.get("backend", "auto")) or "auto")
    )
    url_hash = hashlib.sha256(str(request.get("url", "")).encode("utf-8")).hexdigest()[
        :16
    ]

    def _attempt_payload(
        provider_name: str, _attempt_index: int, _total: int
    ) -> dict[str, Any]:
        return {
            "requested_backend": preferred,
            "selected_backend": provider_name,
        }

    def _invoke(provider_name: str, attempt_index: int) -> dict[str, Any]:
        provider = registry.get(provider_name)
        emit_family_event(
            ctx,
            event="fetch.requested",
            payload={
                "backend": provider_name,
                "url_hash": url_hash,
                "max_bytes": int(request.get("max_bytes", 0) or 0),
                "timeout_ms": int(request.get("timeout_ms", 0) or 0),
            },
        )

        result = provider.fetch(request, ctx)
        if not isinstance(result, dict):
            raise _FetchBackendError(
                "INVALID_RESPONSE",
                "Fetch provider returned invalid response payload",
                details={"backend": provider_name},
            )
        if not bool(result.get("ok", False)):
            err = result.get("error", {})
            code = str(
                (err or {}).get("code", "UPSTREAM_ERROR")
                if isinstance(err, dict)
                else "UPSTREAM_ERROR"
            )
            message = str(
                (err or {}).get("message", "Fetch failed")
                if isinstance(err, dict)
                else "Fetch failed"
            )
            details = (
                dict(err.get("details", {}))
                if isinstance(err, dict) and isinstance(err.get("details"), dict)
                else {}
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            if code in {"POLICY_DENIED", "SSRF_BLOCKED", "NEEDS_APPROVAL"}:
                emit_family_event(
                    ctx,
                    event="fetch.blocked",
                    payload={
                        "backend": provider_name,
                        "code": code,
                        "duration_ms": duration_ms,
                    },
                )
                raise StopChain(
                    _error(
                        code,
                        message,
                        details=details,
                        method=f"fetch.{method.lower()}",
                    )
                )
            emit_family_event(
                ctx,
                event="fetch.completed",
                payload={
                    "backend": provider_name,
                    "ok": False,
                    "code": code,
                    "duration_ms": duration_ms,
                },
            )
            raise _FetchBackendError(code, message, details=details)

        response_backend = str(result.get("backend", provider_name) or provider_name)
        backend_mode = "default"
        if ":" in response_backend:
            backend_mode = (
                str(response_backend.split(":", 1)[1] or "").strip() or "default"
            )
        elif response_backend == "core-http":
            backend_mode = "core"
        if method == "HEAD":
            response_payload = _format_head_response(
                result=result, backend_name=response_backend
            )
        else:
            response_payload = _format_get_response(
                request=request, result=result, backend_name=response_backend, ctx=ctx
            )

        duration_ms = int((time.monotonic() - started) * 1000)
        warnings = [
            str(item).strip()
            for item in response_payload.get("warnings", [])
            if str(item).strip()
        ]
        if attempt_index > 1:
            warnings.append(
                f"Primary backend failed; fell back to '{response_backend}'"
            )
        if warnings:
            response_payload["warnings"] = warnings
        if any(item == "DOWNGRADED_TO_STATIC" for item in warnings):
            emit_family_event(
                ctx,
                event="fetch.degraded",
                payload={
                    "backend": response_backend,
                    "mode": backend_mode,
                    "code": "DOWNGRADED_TO_STATIC",
                    "duration_ms": duration_ms,
                },
            )

        emit_family_event(
            ctx,
            event="fetch.completed",
            payload={
                "backend": response_backend,
                "mode": backend_mode,
                "ok": True,
                "status_code": int(response_payload.get("status_code", 0) or 0),
                "content_bytes": int(response_payload.get("content_bytes", 0) or 0),
                "duration_ms": duration_ms,
            },
        )
        content = str(response_payload.get("text_preview", "") or "")
        if not content:
            content = (
                f"Fetched {response_payload.get('final_url', '')} "
                f"({response_payload.get('status_code', 0)})"
            )
        # TGFC: provider id (e.g. "core-http", "scrapling", "firecrawl") is
        # the structural source-tag for OBGE grounding.
        backend_id = str(response_payload.get("backend", "") or "").strip()
        # PIDF: route web_fetch through the typed boundary owner. content_type
        # is metadata only -- never a routing key for escape selection.
        _pidf_emit_boundary_event(
            "web_fetch",
            content,
            seam_id="tools.fetch.plugin.fetch_response",
            provenance_ref=str(response_payload.get("hash", "") or "") or None,
            content_type=str(response_payload.get("content_type", "") or "") or None,
        )
        return {
            "ok": True,
            "content": content,
            "data": response_payload,
            "verified": True,
            "source": backend_id,
        }

    def _fallback(
        chain: list[str], failures: list[tuple[str, Exception]]
    ) -> dict[str, Any]:
        last_exc = failures[-1][1] if failures else None
        if isinstance(last_exc, _FetchBackendError):
            code = last_exc.code
            message = str(last_exc)
            details = dict(last_exc.details)
        else:
            code = "UPSTREAM_ERROR"
            message = str(last_exc or "No fetch backend could satisfy this request")
            details = {}
        details.setdefault("provider_chain", chain)
        return _error(
            code,
            message,
            details=details,
            method=f"fetch.{method.lower()}",
        )

    return run_provider_chain(
        ctx,
        chain=provider_chain,
        attempt_event="fetch.provider.selected",
        attempt_payload_fn=_attempt_payload,
        invoke_fn=_invoke,
        fallback_result_fn=_fallback,
    )


def _h_get(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _invoke_fetch(args, ctx, method="GET")


def _h_head(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
    return _invoke_fetch(args, ctx, method="HEAD")


def _h_providers(_args: dict[str, Any], _ctx: Any) -> dict[str, Any]:
    registry = _ensure_provider_registry()
    providers = [
        {
            "name": provider.name,
            "capabilities": dict(getattr(provider, "capabilities", {}) or {}),
        }
        for provider in registry.list()
    ]
    return {
        "ok": True,
        "content": "Available fetch providers",
        "data": {
            "providers": providers,
        },
        "verified": True,
        # TGFC: meta-listing comes from the fetch tool family itself.
        "source": "fetch_module",
    }


def register(registry: ToolRegistry) -> None:
    registry.add(
        ToolSpec(
            name="fetch.get",
            args_model=FetchGetArgs,
            min_scope="READ_ONLY",
            handler=_h_get,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "fetch"),
            capabilities=("read_only", "fetch"),
        )
    )
    registry.add(
        ToolSpec(
            name="fetch.head",
            args_model=FetchHeadArgs,
            min_scope="READ_ONLY",
            handler=_h_head,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "fetch"),
            capabilities=("read_only", "fetch"),
        )
    )
    registry.add(
        ToolSpec(
            name="fetch.providers",
            args_model=FetchProvidersArgs,
            min_scope="READ_ONLY",
            handler=_h_providers,
            dangerous=False,
            idempotent=True,
            tags=("plugin", "fetch"),
            capabilities=("read_only", "fetch"),
        )
    )


__all__ = ["register"]
