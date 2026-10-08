from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openminion.base.version import OPENMINION_VERSION as OPENMINION_VERSION

from .exports import LAZY_EXPORTS, PUBLIC_EXPORTS, resolve_lazy_export

if TYPE_CHECKING:  # pragma: no cover - import-time contract only
    from .runtime.public_https import (
        DEFAULT_MAX_BODY_BYTES as DEFAULT_MAX_BODY_BYTES,
        DEFAULT_TIMEOUT_SECONDS as DEFAULT_TIMEOUT_SECONDS,
        PublicHttpsError as PublicHttpsError,
        PublicHttpsResponse as PublicHttpsResponse,
        request_public_https as request_public_https,
    )

__all__ = PUBLIC_EXPORTS


def __getattr__(name: str) -> Any:  # pragma: no cover
    value = resolve_lazy_export(package_name=__name__, name=name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:  # pragma: no cover
    return sorted(set(list(globals().keys()) + list(LAZY_EXPORTS.keys())))
