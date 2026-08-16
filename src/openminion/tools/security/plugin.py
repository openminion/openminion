"""Security tool handlers over concrete scanner adapters."""

import json
import uuid
from typing import Any, Callable

from openminion.modules.tool import preferred_artifact_ref

from .config import resolve_local_target, resolve_security_config
from .providers import scan_artifact, scan_code, scan_dependencies, scan_secrets
from .schemas import (
    LocalScanArgs,
    SecurityScanResult,
)

Scanner = Callable[..., SecurityScanResult]


def _handler(args_model: type[LocalScanArgs], scanner: Scanner):
    def handler(args: dict[str, Any], ctx: Any) -> dict[str, Any]:
        parsed = args_model.model_validate(args)
        config = resolve_security_config(ctx)
        target = resolve_local_target(parsed.target, config)
        result = scanner(parsed, target=target, config=config)
        if parsed.include_evidence_artifact and result.status in {
            "completed",
            "partial",
        }:
            artifact = ctx.write_artifact(
                f"security/{uuid.uuid4().hex}.json",
                json.dumps(
                    result.model_dump(mode="json", exclude_none=True),
                    ensure_ascii=True,
                    sort_keys=True,
                ).encode(),
                "application/json",
                durable=True,
            )
            result.evidence_refs.append(preferred_artifact_ref(artifact))
        return result.as_tool_result()

    return handler


_h_scan_code = _handler(LocalScanArgs, scan_code)
_h_scan_dependencies = _handler(LocalScanArgs, scan_dependencies)
_h_scan_artifact = _handler(LocalScanArgs, scan_artifact)
_h_scan_secrets = _handler(LocalScanArgs, scan_secrets)

__all__ = [
    "_h_scan_artifact",
    "_h_scan_code",
    "_h_scan_dependencies",
    "_h_scan_secrets",
]
