import importlib
import importlib.metadata
from typing import Any, Mapping
from urllib.parse import urlparse

from openminion.modules.tool.runtime.resource_selectors import ResourceSelectors
from openminion.tools.browser.models import InstanceSpec

from .coercion import dedupe, domain_allowed


def resource_selectors(provider: Any, args: Mapping[str, Any]) -> ResourceSelectors:
    writes: list[str] = [
        provider.config.downloads.dir,
        provider.config.artifacts.root_dir,
        provider.config.artifacts.screenshots_dir,
        provider.config.artifacts.pdf_dir,
        provider.config.artifacts.traces_dir,
    ]
    output = args.get("output") if isinstance(args.get("output"), Mapping) else {}
    output_path = (
        str(output.get("path", "")).strip() if isinstance(output, Mapping) else ""
    )
    if output_path:
        writes.append(output_path)

    reads: list[str] = []
    op = str(args.get("op", "")).strip()
    if op in {"upload", "tab.upload"}:
        options = (
            args.get("options") if isinstance(args.get("options"), Mapping) else {}
        )
        files = options.get("files", options.get("file"))
        if isinstance(files, str) and files.strip():
            reads.append(files.strip())
        if isinstance(files, list):
            reads.extend(str(item).strip() for item in files if str(item).strip())

    url = str(args.get("url", "")).strip()
    domains: tuple[str, ...] = ()
    protocols: tuple[str, ...] = ()
    if url:
        parsed = urlparse(url)
        host = str(parsed.hostname or "").strip().lower()
        if host:
            domains = (host,)
        scheme = str(parsed.scheme or "").strip().lower()
        if scheme:
            protocols = (scheme,)

    return ResourceSelectors(
        paths_read=tuple(dedupe(reads)),
        paths_write=tuple(dedupe(writes)),
        domains=domains,
        protocols=protocols,
    )


def ensure_ready(provider: Any) -> dict[str, Any]:
    had_playwright = getattr(provider, "_playwright", None) is not None
    had_manager = getattr(provider, "_playwright_manager", None) is not None
    try:
        browser_type_obj = browser_type(provider)
        browser = browser_type_obj.launch(headless=True)
        browser.close()
        return {
            "ok": True,
            "provider_version": provider_version(provider),
            "capabilities": {
                "snapshot_refs": provider.capabilities.snapshot_refs,
                "selectors": provider.capabilities.selectors,
                "role_selectors": provider.capabilities.role_selectors,
                "pdf_export": provider.capabilities.pdf_export,
                "trace": provider.capabilities.trace,
                "network_intercept": provider.capabilities.network_intercept,
                "persistent_profiles": provider.capabilities.persistent_profiles,
            },
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": {
                "code": "DEPENDENCY_MISSING",
                "message": f"Playwright browser runtime is not ready: {exc}",
                "remediation": [
                    "Install Python package: pip install playwright",
                    "Install browser binaries: playwright install",
                ],
            },
        }
    finally:
        # Keep pre-existing runtime sessions intact; only tear down managers
        # created by this readiness probe.
        if not had_playwright and not had_manager:
            shutdown_playwright(provider)


def enforce_network_policy(provider: Any, url: str) -> None:
    parsed = urlparse(str(url))
    host = str(parsed.hostname or "").strip().lower()
    mode = provider.config.network.mode

    if mode == "allow_all":
        return
    if mode == "deny_all":
        raise PermissionError("network denied by browser provider policy")

    allow_domains = provider.config.network.allow_domains
    if allow_domains and host:
        if not domain_allowed(host, allow_domains):
            raise PermissionError(f"domain is not allowed by browser policy: {host}")


def instance_spec(
    *,
    spec: InstanceSpec | Mapping[str, Any] | None,
    profile: str | None,
    mode: str | None,
    port: int | None,
) -> InstanceSpec:
    if isinstance(spec, InstanceSpec):
        return spec
    if isinstance(spec, Mapping):
        return InstanceSpec.model_validate(dict(spec))
    return InstanceSpec(profile=profile, mode=mode, port=port)


def browser_type(provider: Any) -> Any:
    pw = ensure_playwright(provider)
    browser_name = provider.config.browser
    if not hasattr(pw, browser_name):
        raise ValueError(f"unsupported playwright browser: {browser_name}")
    return getattr(pw, browser_name)


def ensure_playwright(provider: Any) -> Any:
    if provider._playwright is not None:
        return provider._playwright

    if provider._playwright_factory is not None:
        api = provider._playwright_factory()
        provider._playwright = api.start() if hasattr(api, "start") else api
        return provider._playwright

    module = importlib.import_module("playwright.sync_api")
    manager = module.sync_playwright()
    provider._playwright_manager = manager
    provider._playwright = manager.start()
    return provider._playwright


def shutdown_playwright(provider: Any) -> None:
    if provider._playwright_manager is not None:
        try:
            provider._playwright_manager.stop()
        except Exception:
            pass
    provider._playwright_manager = None
    provider._playwright = None


def provider_version(provider: Any) -> str:
    del provider
    try:
        return importlib.metadata.version("playwright")
    except Exception:
        return "unknown"


def lock_key(provider: Any, tab_id: str) -> str:
    token = str(tab_id).strip()
    instance_id = ""
    try:
        instance_id = provider._tabs.get(token).instance_id
    except Exception:
        instance_id = "unknown"
    return f"{provider.provider_id}:{instance_id}:{token}"


__all__ = [
    "browser_type",
    "ensure_playwright",
    "ensure_ready",
    "enforce_network_policy",
    "instance_spec",
    "lock_key",
    "provider_version",
    "resource_selectors",
    "shutdown_playwright",
]
