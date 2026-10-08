"""Stable commerce identity helpers."""

from __future__ import annotations

import hashlib
import json


def commerce_digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode()
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"
