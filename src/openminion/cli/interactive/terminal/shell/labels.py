from __future__ import annotations

from typing import Any


def _runtime_label(runtime: Any) -> str:
    provider = str(getattr(runtime, "provider_name", "") or "").strip()
    model = str(getattr(runtime, "model_name", "") or "").strip()
    if provider and model:
        return f"{provider}/{model}"
    return model or provider or "—"
