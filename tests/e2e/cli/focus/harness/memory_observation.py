"""Test-only, process-local guard for nonstreaming MiniMax HTTP observations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path
import re
from threading import Lock, get_ident
from time import monotonic
from typing import Any, NoReturn, TextIO
from unittest.mock import patch
from urllib import request as urllib_request
from urllib.parse import urlsplit

from openminion.modules.llm.providers.transport import curl, http, sse
from openminion.modules.llm.providers.transport.client import ProviderHTTPClient

_INSTALLATION = Lock()
_NONCE = re.compile(r"(?=([0-9a-fA-F]{32}))")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:/-]{1,160}")
_METADATA_IDS = (
    "session_id",
    "runtime_session_id",
    "turn_id",
    "root_turn_id",
    "capture_event_id",
    "capture_id",
    "candidate_id",
    "record_id",
    "context_manifest_id",
    "llm_call_id",
    "context_llm_call_id",
    "trace_id",
    "request_id",
    "run_id",
    "agent_id",
    "answer_id",
)


class ObservationError(RuntimeError):
    """A latched observation failure; messages contain only fixed reason codes."""


def _discard_content_trace(*args: Any, **kwargs: Any) -> None:
    pass


def _nonce_hashes(value: Any) -> set[str]:
    pending = [value]
    hashes: set[str] = set()
    while pending:
        child = pending.pop()
        if isinstance(child, str):
            hashes.update(
                sha256(nonce.encode()).hexdigest() for nonce in _NONCE.findall(child)
            )
        elif isinstance(child, dict):
            pending.extend(child.values())
        elif isinstance(child, list):
            pending.extend(child)
    return hashes


def _text_only(payload: dict[str, Any]) -> bool:
    if any(key in payload for key in ("audio", "image", "video", "input_audio")):
        return False
    if payload.get("modalities", ["text"]) != ["text"]:
        return False
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict) or "audio" in message:
            return False
        content = message.get("content")
        if content is None or isinstance(content, str):
            continue
        if not isinstance(content, list) or not all(
            isinstance(part, dict)
            and set(part) == {"type", "text"}
            and part["type"] == "text"
            and isinstance(part["text"], str)
            for part in content
        ):
            return False
    return True


@dataclass
class _Observation:
    endpoint: str
    model: str
    request_limit: int
    body_limit: int
    output_limit: int
    timeout_limit: float
    deadline_monotonic: float
    session_id: str
    target_hashes: dict[str, str]
    evidence: TextIO
    opener: urllib_request.OpenerDirector
    lock: Any = field(default_factory=Lock)
    reserved: int = 0
    failure: str = ""
    closed: bool = False
    active: int = 0
    pending: dict[int, tuple[bytes, dict[str, str], dict[str, Any]]] = field(
        default_factory=dict
    )

    def write(self, event: str, **facts: Any) -> None:
        try:
            self.evidence.write(
                json.dumps({"event": event, **facts}, sort_keys=True) + "\n"
            )
            self.evidence.flush()
        except OSError:
            self.failure = "evidence_write_failed"
            raise ObservationError(self.failure) from None

    def fail(self, reason: str) -> NoReturn:
        if not self.failure:
            self.failure = reason
            self.write("rejected", reason=reason, reservations=self.reserved)
        raise ObservationError(self.failure)

    def deny_transport(self, *args: Any, **kwargs: Any) -> NoReturn:
        with self.lock:
            self.fail("transport_denied")

    def deny_redirect(self, *args: Any, **kwargs: Any) -> NoReturn:
        with self.lock:
            self.fail("redirect_denied")

    def ensure_open(self) -> None:
        if self.failure:
            self.fail(self.failure)
        if self.closed:
            self.fail("observer_closed")
        self.check_deadline()

    def check_deadline(self) -> None:
        if monotonic() >= self.deadline_monotonic:
            self.fail("admission_deadline_expired")

    def finish(self) -> None:
        with self.lock:
            if self.failure:
                self.closed = True
                self.fail(self.failure)
            if self.closed:
                return
            self.closed = True
            self.check_deadline()
            if self.pending or self.active:
                self.fail("incomplete_dispatch")
            self.write("completed", reservations=self.reserved)

    def trace(
        self,
        *,
        url: str,
        body_json: str,
        headers: dict[str, str],
        timeout_seconds: float,
        transport: str,
        method: str = "POST",
        trace_metadata: dict[str, Any] | None = None,
        **unused: Any,
    ) -> None:
        with self.lock:
            self.ensure_open()
            if transport != "urllib" or method != "POST":
                self.fail("transport_denied")
            if url != self.endpoint:
                self.fail("endpoint_mismatch")
            if get_ident() in self.pending:
                self.fail("unconsumed_trace")
            if self.reserved >= self.request_limit:
                self.fail("request_limit")
            # Reservations survive validation, I/O failures and retries.
            self.reserved += 1
            body = body_json.encode("utf-8")
            if len(body) > self.body_limit:
                self.fail("body_limit")
            try:
                payload = json.loads(body_json)
            except (ValueError, RecursionError):
                self.fail("invalid_json")
            output_cap = self.check_payload(payload)
            if not self.valid_timeout(timeout_seconds):
                self.fail("timeout_limit")
            metadata = self.metadata(trace_metadata or {})
            hashes = _nonce_hashes(payload)
            facts = dict(
                request_index=self.reserved,
                observer_session_id=self.session_id,
                canonical=metadata,
                body_bytes=len(body),
                output_cap=output_cap,
                timeout_seconds=timeout_seconds,
                target_matches=sorted(
                    label
                    for label, digest in self.target_hashes.items()
                    if digest in hashes
                ),
            )
            self.write("reserved", request_index=self.reserved)
            self.pending[get_ident()] = (
                body,
                {key.lower(): value for key, value in headers.items()},
                facts,
            )

    def check_payload(self, payload: Any) -> int:
        if not isinstance(payload, dict) or payload.get("model") != self.model:
            self.fail("model_mismatch")
        if payload.get("stream", False) is not False:
            self.fail("stream_denied")
        if payload.get("service_tier", "standard") != "standard":
            self.fail("service_tier_denied")
        if type(payload.get("n", 1)) is not int or payload.get("n", 1) != 1:
            self.fail("multiple_outputs_denied")
        if not _text_only(payload):
            self.fail("media_denied")
        caps = [
            payload[key]
            for key in ("max_tokens", "max_completion_tokens")
            if key in payload
        ]
        if not caps or any(
            type(cap) is not int or not 0 < cap <= self.output_limit for cap in caps
        ):
            self.fail("output_limit")
        if len(caps) == 2 and caps[0] != caps[1]:
            self.fail("conflicting_output_caps")
        return caps[0]

    def valid_timeout(self, timeout: Any) -> bool:
        return (
            type(timeout) in (int, float)
            and math.isfinite(timeout)
            and 0 < timeout <= self.timeout_limit
        )

    def metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key in _METADATA_IDS:
            value = metadata.get(key)
            if value is None or value == "":
                continue
            if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
                self.fail("invalid_metadata_id")
            if _nonce_hashes(value) & set(self.target_hashes.values()):
                self.fail("target_in_metadata")
            result[key] = value
        if type(metadata.get("inference_step")) is int:
            result["inference_step"] = metadata["inference_step"]
        return result

    def urlopen(self, request: urllib_request.Request, *, timeout: float) -> Any:
        with self.lock:
            self.ensure_open()
            pending = self.pending.pop(get_ident(), None)
            if pending is None:
                self.fail("unobserved_send")
            body, headers, facts = pending
            if (
                not isinstance(request, urllib_request.Request)
                or request.full_url != self.endpoint
                or request.get_method() != "POST"
                or type(request.data) is not bytes
                or request.data != body
                or {key.lower(): value for key, value in request.header_items()}
                != headers
            ):
                self.fail("wire_mismatch")
            if not self.valid_timeout(timeout) or timeout > facts["timeout_seconds"]:
                self.fail("timeout_limit")
            self.write("request", **{**facts, "timeout_seconds": timeout})
            # Evidence flushing may have consumed the remaining admission time.
            self.check_deadline()
            self.active += 1
        try:
            return self.opener.open(request, timeout=timeout)
        finally:
            with self.lock:
                self.active -= 1


@contextmanager
def observe_requests(
    *,
    endpoint: str,
    model: str,
    request_limit: int,
    body_limit: int,
    output_limit: int,
    timeout_limit: float,
    deadline_monotonic: float,
    session_id: str,
    target_hashes: dict[str, str],
    evidence_path: Path,
) -> Iterator[_Observation]:
    """Enclose runtime construction AND cleanup in a dedicated child process.

    The caller owns the wall deadline and disabling nontransport content traces.
    Reservations and send attempts are not proof of provider receipt or billing.
    Missing canonical metadata stays missing; observer_session_id is not a join.
    finish() closes admission, not response bodies or remote generation. The caller
    must finish runtime cleanup first and exit before restoring patches if workers remain.
    deadline_monotonic is the shared same-host campaign deadline, never a fresh
    per-process allowance. It closes admission, not already-admitted wire activity.
    """
    parsed = urlsplit(endpoint)
    loopback_http = bool(re.fullmatch(r"http://127\.0\.0\.1:[0-9]+/[^?#]*", endpoint))
    if loopback_http:
        try:
            loopback_http = parsed.port is not None and 0 < parsed.port <= 65535
        except ValueError:
            loopback_http = False
    if (
        not (parsed.scheme == "https" or loopback_http)
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid observation endpoint")
    if any(
        type(value) is not int or value <= 0
        for value in (request_limit, body_limit, output_limit)
    ):
        raise ValueError("invalid observation limits")
    if (
        type(deadline_monotonic) not in (int, float)
        or not math.isfinite(deadline_monotonic)
        or deadline_monotonic <= 0
    ):
        raise ValueError("invalid observation deadline")
    if (
        type(timeout_limit) not in (int, float)
        or not math.isfinite(timeout_limit)
        or timeout_limit <= 0
        or not model
        or not _IDENTIFIER.fullmatch(session_id)
    ):
        raise ValueError("invalid observation configuration")
    if any(
        not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", label)
        or not re.fullmatch(r"[0-9a-f]{64}", digest)
        for label, digest in target_hashes.items()
    ):
        raise ValueError("invalid observation targets")
    if _nonce_hashes(session_id) & set(target_hashes.values()):
        raise ValueError("target in observation session")
    if any(
        _nonce_hashes(label) & set(target_hashes.values()) for label in target_hashes
    ):
        raise ValueError("target in observation label")
    if not _INSTALLATION.acquire(blocking=False):
        raise ObservationError("observer_already_installed")
    try:
        with ExitStack() as stack:
            evidence = stack.enter_context(evidence_path.open("x", encoding="utf-8"))
            redirects = urllib_request.HTTPRedirectHandler()
            observer = _Observation(
                endpoint,
                model,
                request_limit,
                body_limit,
                output_limit,
                timeout_limit,
                deadline_monotonic,
                session_id,
                dict(target_hashes),
                evidence,
                urllib_request.build_opener(redirects),
            )
            stack.enter_context(
                patch.object(redirects, "redirect_request", observer.deny_redirect)
            )
            stack.enter_context(
                patch.object(http, "trace_http_json_request", observer.trace)
            )
            stack.enter_context(
                patch.object(http, "trace_http_json_response", _discard_content_trace)
            )
            for module in (http, curl):
                stack.enter_context(
                    patch.object(
                        module, "write_llm_debug_event", _discard_content_trace
                    )
                )
            for module in (sse, curl):
                stack.enter_context(
                    patch.object(
                        module, "trace_http_json_request", observer.deny_transport
                    )
                )
            stack.enter_context(
                patch.object(ProviderHTTPClient, "urlopen", observer.deny_transport)
            )
            stack.enter_context(
                patch.object(urllib_request, "urlopen", observer.urlopen)
            )
            with observer.lock:
                observer.ensure_open()
            yield observer
            observer.finish()
    finally:
        _INSTALLATION.release()
