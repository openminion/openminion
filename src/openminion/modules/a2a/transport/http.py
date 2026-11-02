from __future__ import annotations

from typing import Any, Callable

import httpx

from openminion.modules.a2a.errors import (
    A2AError,
    ERROR_CODE_INTERNAL_ERROR,
    normalize_error_code,
)
from openminion.modules.a2a.models import Envelope, JobRecord
from openminion.modules.a2a.transport.base import Transport


class HTTPTransport(Transport):  # type: ignore[misc]
    """Cross-machine A2A transport that talks to an A2A HTTP server."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._headers = {"Authorization": f"Bearer {token}"}

    def call(self, envelope: Envelope) -> Envelope:
        payload = self._post(
            "/a2a/call", envelope.to_dict(), timeout_ms=envelope.timeout_ms
        )
        return self._decode(Envelope.from_dict, payload, "/a2a/call")

    def job_start(self, envelope: Envelope) -> str:
        payload = self._post(
            "/a2a/job/start", envelope.to_dict(), timeout_ms=envelope.timeout_ms
        )
        task_id = payload.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            raise A2AError(
                ERROR_CODE_INTERNAL_ERROR, "Server response missing 'task_id'"
            )
        return task_id

    def job_status(self, task_id: str) -> JobRecord:
        payload = self._post("/a2a/job/status", {"task_id": task_id})
        return self._decode(JobRecord.from_dict, payload, "/a2a/job/status")

    def job_cancel(self, task_id: str) -> JobRecord:
        payload = self._post("/a2a/job/cancel", {"task_id": task_id})
        return self._decode(JobRecord.from_dict, payload, "/a2a/job/cancel")

    def _post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        timeout = (timeout_ms or 30_000) / 1000.0
        try:
            response = httpx.post(
                url,
                json=body,
                headers=self._headers,
                timeout=timeout,
            )
        except httpx.HTTPError as exc:
            raise A2AError(
                ERROR_CODE_INTERNAL_ERROR,
                f"A2A HTTP request to {url} failed: {exc}",
            ) from exc

        if response.status_code >= 400:
            raise self._error_from_response(response, path)

        try:
            payload = response.json()
        except ValueError as exc:
            raise A2AError(
                ERROR_CODE_INTERNAL_ERROR,
                f"A2A server returned a non-JSON response for {path}",
            ) from exc

        if not isinstance(payload, dict):
            raise A2AError(
                ERROR_CODE_INTERNAL_ERROR,
                f"A2A server returned a malformed response body for {path}",
            )
        return payload

    @staticmethod
    def _decode(
        factory: Callable[[dict[str, Any]], Any],
        payload: dict[str, Any],
        path: str,
    ) -> Any:
        """Build a model from a 2xx body, translating decode errors to A2AError.

        A successful response that is a JSON dict but does not satisfy the
        model contract (wrong field types, a missing key, a non-numeric
        ``progress``, ...) must surface as the documented INTERNAL_ERROR rather
        than a raw ``ValueError``/``AttributeError`` traceback.
        """
        try:
            return factory(payload)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise A2AError(
                ERROR_CODE_INTERNAL_ERROR,
                f"A2A server returned a malformed response body for {path}: {exc}",
            ) from exc

    def _error_from_response(self, response: httpx.Response, path: str) -> A2AError:
        """Build an A2AError from a >= 400 response, preserving the error envelope.

        When the body is a valid ``{"error": {"code", "message", "details"}}``
        envelope the original code/message/details are preserved verbatim (only
        the code is normalized). Anything else — a plain-text body, a non-dict
        payload, or a malformed envelope — falls back to INTERNAL_ERROR with the
        raw body captured in details.
        """
        try:
            payload = response.json()
        except ValueError:
            return A2AError(
                ERROR_CODE_INTERNAL_ERROR,
                f"A2A server returned HTTP {response.status_code} for {path}",
                self._error_details(response),
            )
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            code = error.get("code")
            message = error.get("message")
            details = error.get("details")
            if isinstance(code, str) and isinstance(message, str):
                return A2AError(
                    normalize_error_code(code),
                    message,
                    dict(details) if isinstance(details, dict) else {},
                )
        return A2AError(
            ERROR_CODE_INTERNAL_ERROR,
            f"A2A server returned HTTP {response.status_code} for {path}",
            self._error_details(response),
        )

    @staticmethod
    def _error_details(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError:
            return {"status_code": response.status_code, "body": response.text}
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            return dict(error)
        return {"status_code": response.status_code, "body": payload}
