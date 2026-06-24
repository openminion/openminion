from typing import Any


def normalize_mode(mode: str | None, *, default_headless: bool) -> tuple[bool, str]:
    token = str(mode or "").strip().lower()
    if token in {"headed", "headful", "ui"}:
        return False, "headed"
    if token in {"headless", "automation"}:
        return True, "headless"
    return bool(default_headless), "headless" if default_headless else "headed"


def safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def safe_url(page: Any) -> str:
    try:
        return str(page.url or "")
    except Exception:
        return ""


def safe_title(page: Any) -> str:
    try:
        return str(page.title() or "")
    except Exception:
        return ""


def tab_metadata(tab: Any) -> dict[str, str]:
    return {
        "id": str(getattr(tab, "id", "") or ""),
        "url": safe_url(getattr(tab, "page", None)),
        "title": safe_title(getattr(tab, "page", None)),
    }


def response_status(response: Any) -> int | None:
    if response is None:
        return None
    status = getattr(response, "status", None)
    if callable(status):
        try:
            status = status()
        except Exception:
            status = None
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def domain_allowed(host: str, allow_domains: tuple[str, ...]) -> bool:
    token = host.lower()
    for domain in allow_domains:
        needle = domain.lower()
        if token == needle or token.endswith("." + needle):
            return True
    return False


def dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        token = str(item).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


__all__ = [
    "dedupe",
    "domain_allowed",
    "normalize_mode",
    "response_status",
    "safe_int",
    "tab_metadata",
    "safe_title",
    "safe_url",
]
