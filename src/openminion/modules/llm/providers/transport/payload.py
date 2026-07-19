from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SerializedJSONPayload:
    payload: dict[str, Any]
    body_json: str
    body_bytes: bytes

    @property
    def byte_count(self) -> int:
        return len(self.body_bytes)


def serialize_json_payload(payload: dict[str, Any]) -> SerializedJSONPayload:
    body_json = json.dumps(payload)
    return SerializedJSONPayload(
        payload=payload,
        body_json=body_json,
        body_bytes=body_json.encode("utf-8"),
    )


__all__ = ["SerializedJSONPayload", "serialize_json_payload"]
