"""HTTP Events API verification helpers."""

from __future__ import annotations

import hmac
import json
import time
from hashlib import sha256
from typing import Any
from collections.abc import Mapping


class SlackSignatureError(ValueError):
    pass


def verify_slack_signature(
    *,
    signing_secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
    now: float | None = None,
    max_skew_seconds: int = 300,
) -> None:
    secret = str(signing_secret or "").strip()
    if not secret:
        raise SlackSignatureError("missing Slack signing secret")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise SlackSignatureError("invalid Slack signature timestamp") from exc
    current = time.time() if now is None else float(now)
    if abs(current - ts) > max_skew_seconds:
        raise SlackSignatureError("stale Slack signature timestamp")
    basestring = b"v0:" + str(ts).encode("utf-8") + b":" + body
    expected = "v0=" + hmac.new(secret.encode("utf-8"), basestring, sha256).hexdigest()
    if not hmac.compare_digest(expected, str(signature or "")):
        raise SlackSignatureError("invalid Slack signature")


def parse_json_body(body: bytes | str | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(body, Mapping):
        return dict(body)
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    payload = json.loads(text or "{}")
    if not isinstance(payload, dict):
        raise ValueError("Slack payload must be a JSON object")
    return payload


def url_verification_response(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    if payload.get("type") != "url_verification":
        return None
    challenge = str(payload.get("challenge") or "")
    return {"status": 200, "body": challenge}
