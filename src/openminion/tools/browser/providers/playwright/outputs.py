from typing import Any, Mapping

from openminion.tools.browser.models import OutputOptions

from .coercion import tab_metadata


def screenshot(
    provider: Any, *, tab_id: str, output_path: str | None = None
) -> dict[str, Any]:
    tab = provider._tabs.get(tab_id)
    key = provider._lock_key(tab_id)
    with provider._locks.action_lock(key):
        blob = tab.page.screenshot(full_page=True)
    artifact = provider._artifact_writer.write_screenshot(blob, output_path=output_path)
    return {"artifact": artifact}


def tab_screenshot(
    provider: Any,
    *,
    tab_id: str = "",
    options: OutputOptions | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    output = output_options(options)
    return screenshot(provider, tab_id=tab_id, output_path=output.path)


def pdf(
    provider: Any, *, tab_id: str, output_path: str | None = None
) -> dict[str, Any]:
    if not provider.config.browser_is_chromium:
        raise RuntimeError("pdf_not_supported")
    tab = provider._tabs.get(tab_id)
    key = provider._lock_key(tab_id)
    with provider._locks.action_lock(key):
        blob = tab.page.pdf()
    artifact = provider._artifact_writer.write_pdf(blob, output_path=output_path)
    return {"artifact": artifact}


def tab_pdf(
    provider: Any,
    *,
    tab_id: str = "",
    options: OutputOptions | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    output = output_options(options)
    return pdf(provider, tab_id=tab_id, output_path=output.path)


def upload(
    provider: Any,
    *,
    tab_id: str,
    files: list[str],
    selector: str | None = None,
    role: Mapping[str, Any] | None = None,
    node_id: str | None = None,
) -> dict[str, Any]:
    tab = provider._tabs.get(tab_id)
    if not files:
        raise ValueError("upload requires at least one file")
    resolved = [str(resolve_workspace_file(provider, path)) for path in files]

    key = provider._lock_key(tab_id)
    with provider._locks.action_lock(key):
        locator = provider._selector_adapter.resolve_locator(
            page=tab.page,
            selector=selector,
            role=role,
            node_id=node_id,
            snapshot_hints=tab.last_snapshot_hints,
            require_target=True,
        )
        locator.set_input_files(resolved, timeout=provider.config.timeouts.action_ms)

    return {
        "tab": tab_metadata(tab),
        "uploaded": [to_workspace_relative(provider, path) for path in resolved],
    }


def resolve_workspace_file(provider: Any, raw_path: str) -> str:
    token = str(raw_path).strip()
    if not token:
        raise ValueError("empty file path")

    path = provider._artifact_writer.resolve_output_path(token)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"upload file not found: {path}")
    return str(path)


def to_workspace_relative(provider: Any, path: str) -> str:
    try:
        return str(
            provider._artifact_writer.resolve_output_path(path).relative_to(
                provider.config.workspace_path
            )
        )
    except Exception:
        return path


def resolve_profile_dir(provider: Any, profile: str | None) -> str:
    base = provider._artifact_writer.resolve_output_path(
        provider.config.persistent.user_data_dir
    )
    token = str(profile or "").strip()
    if not token:
        base.mkdir(parents=True, exist_ok=True)
        return str(base)
    if base.name == "default":
        target = (base.parent / token).resolve(strict=False)
    else:
        target = (base / token).resolve(strict=False)
    provider._artifact_writer.resolve_output_path(str(target))
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def output_options(options: OutputOptions | Mapping[str, Any] | None) -> OutputOptions:
    if isinstance(options, OutputOptions):
        return options
    if isinstance(options, Mapping):
        return OutputOptions.model_validate(dict(options))
    return OutputOptions()


__all__ = [
    "output_options",
    "pdf",
    "resolve_profile_dir",
    "resolve_workspace_file",
    "screenshot",
    "tab_pdf",
    "tab_screenshot",
    "to_workspace_relative",
    "upload",
]
