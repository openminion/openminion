import json
from typing import Any, Callable, Mapping
from urllib.request import Request, urlopen


OutboundSender = Callable[[dict[str, Any]], None]
HttpPost = Callable[[str, dict[str, Any], dict[str, str]], int]


def deliver_cron_result(
    mode: str,
    to_value: str,
    job: Mapping[str, Any],
    run: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    outbound: OutboundSender | None = None,
    webhook_token: str | None = None,
    http_post: HttpPost | None = None,
) -> dict[str, Any]:
    delivery = job.get("delivery", {})
    best_effort = (
        bool(delivery.get("best_effort", False))
        if isinstance(delivery, Mapping)
        else False
    )
    normalized_mode = str(mode or "none").strip() or "none"
    target = str(to_value or "").strip()
    summary = str(result.get("summary", "") or "").strip()

    try:
        if normalized_mode == "none":
            return {"ok": True, "mode": normalized_mode, "skipped": True}

        if normalized_mode == "announce":
            if not target:
                raise ValueError("announce delivery target is required")
            if outbound is None:
                raise RuntimeError("announce delivery requires outbound callback")
            outbound(
                {
                    "type": "cron.announce",
                    "target": target,
                    "text": summary,
                    "job_id": job.get("job_id"),
                    "run_id": run.get("run_id"),
                    "summary": summary,
                    "result": dict(result),
                }
            )
            return {"ok": True, "mode": normalized_mode, "target": target}

        if normalized_mode == "webhook":
            if not summary:
                return {
                    "ok": True,
                    "mode": normalized_mode,
                    "skipped": True,
                    "reason": "missing_summary",
                }
            if not target:
                raise ValueError("webhook delivery target is required")
            post = http_post or _http_post_json
            headers = {"Content-Type": "application/json"}
            if webhook_token:
                headers["Authorization"] = f"Bearer {webhook_token}"
            body = {
                "event": "cron.run.finished",
                "job": dict(job),
                "run": dict(run),
                "result": dict(result),
            }
            status_code = int(post(target, body, headers))
            return {
                "ok": True,
                "mode": normalized_mode,
                "target": target,
                "status_code": status_code,
            }

        raise ValueError(f"unsupported delivery mode: {normalized_mode}")
    except Exception as exc:
        if not best_effort:
            raise
        return {
            "ok": False,
            "mode": normalized_mode,
            "target": target,
            "best_effort": True,
            "error": str(exc),
        }


def _http_post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> int:
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    request = Request(url=url, data=body, headers=headers, method="POST")
    with urlopen(request, timeout=15) as response:  # nosec B310 - explicit cron config controls target
        return int(getattr(response, "status", 0) or 0)
