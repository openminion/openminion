import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from openminion.base.config.paths import (
    ensure_under_data_root,
)
from openminion.tools.config import (
    ToolEnv,
    resolve_tool_data_root,
    resolve_tool_env,
    resolve_tool_workspace_root,
)

from .constants import (
    DEFAULT_PLAYWRIGHT_ARTIFACTS_ROOT_DIR,
    DEFAULT_PLAYWRIGHT_DOWNLOADS_DIR,
    DEFAULT_PLAYWRIGHT_PDF_DIR,
    DEFAULT_PLAYWRIGHT_PERSISTENT_USER_DATA_DIR,
    DEFAULT_PLAYWRIGHT_SCREENSHOTS_DIR,
    DEFAULT_PLAYWRIGHT_TRACES_DIR,
    DEFAULT_PLAYWRIGHT_WORKSPACE_ARTIFACTS_SUBPATH,
    DEFAULT_PLAYWRIGHT_WORKSPACE_DOWNLOADS_SUBPATH,
)


@dataclass(frozen=True)
class PlaywrightPersistentConfig:
    enabled: bool = True
    user_data_dir: str = DEFAULT_PLAYWRIGHT_PERSISTENT_USER_DATA_DIR


@dataclass(frozen=True)
class PlaywrightDownloadsConfig:
    dir: str = DEFAULT_PLAYWRIGHT_DOWNLOADS_DIR
    accept_downloads: bool = True


@dataclass(frozen=True)
class PlaywrightArtifactsConfig:
    root_dir: str = DEFAULT_PLAYWRIGHT_ARTIFACTS_ROOT_DIR
    screenshots_dir: str = DEFAULT_PLAYWRIGHT_SCREENSHOTS_DIR
    pdf_dir: str = DEFAULT_PLAYWRIGHT_PDF_DIR
    traces_dir: str = DEFAULT_PLAYWRIGHT_TRACES_DIR


@dataclass(frozen=True)
class PlaywrightTimeoutConfig:
    navigation_ms: int = 30000
    action_ms: int = 15000
    api_ms: int = 20000


@dataclass(frozen=True)
class PlaywrightSnapshotConfig:
    mode: str = "a11y"
    max_nodes: int = 800
    max_text_chars: int = 20000
    include_css_path: bool = False
    include_bbox: bool = False


@dataclass(frozen=True)
class PlaywrightNetworkConfig:
    mode: str = "policy"
    allow_domains: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlaywrightProviderConfig:
    browser: str = "chromium"
    headless_default: bool = True
    slow_mo_ms: int = 0
    locale: str = "en-US"
    timezone_id: str = "America/Los_Angeles"
    viewport_width: int = 1280
    viewport_height: int = 720
    workspace_root: str = ""
    persistent: PlaywrightPersistentConfig = PlaywrightPersistentConfig()
    downloads: PlaywrightDownloadsConfig = PlaywrightDownloadsConfig()
    artifacts: PlaywrightArtifactsConfig = PlaywrightArtifactsConfig()
    timeouts: PlaywrightTimeoutConfig = PlaywrightTimeoutConfig()
    snapshot: PlaywrightSnapshotConfig = PlaywrightSnapshotConfig()
    network: PlaywrightNetworkConfig = PlaywrightNetworkConfig()

    @property
    def browser_is_chromium(self) -> bool:
        return self.browser.strip().lower() == "chromium"

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace_root).resolve(strict=False)


def provider_config_from_mapping(
    cfg: Mapping[str, Any] | None = None,
    *,
    env: ToolEnv | Mapping[str, Any] | None = None,
) -> PlaywrightProviderConfig:
    cfg = cfg or {}
    resolved_env = resolve_tool_env(env=env)
    workspace_root_path = resolve_tool_workspace_root(
        workspace_root=cfg.get("workspace_root"),
        env=resolved_env,
        fallback=os.getcwd(),
    )
    workspace_root = str(workspace_root_path)
    data_root_path = resolve_tool_data_root(
        data_root=cfg.get("data_root"),
        env=resolved_env,
    )
    data_root = str(data_root_path)

    persistent_cfg = (
        cfg.get("persistent") if isinstance(cfg.get("persistent"), Mapping) else {}
    )
    downloads_cfg = (
        cfg.get("downloads") if isinstance(cfg.get("downloads"), Mapping) else {}
    )
    artifacts_cfg = (
        cfg.get("artifacts") if isinstance(cfg.get("artifacts"), Mapping) else {}
    )
    timeouts_cfg = (
        cfg.get("timeouts") if isinstance(cfg.get("timeouts"), Mapping) else {}
    )
    snapshot_cfg = (
        cfg.get("snapshot") if isinstance(cfg.get("snapshot"), Mapping) else {}
    )
    network_cfg = cfg.get("network") if isinstance(cfg.get("network"), Mapping) else {}
    viewport_cfg = (
        cfg.get("viewport") if isinstance(cfg.get("viewport"), Mapping) else {}
    )

    # Default artifacts go under the workspace so artifact paths can be returned
    # workspace-relative (e.g. ".openminion/...").
    if "root_dir" in artifacts_cfg:
        artifacts_root_dir = _expand_vars(
            str(artifacts_cfg.get("root_dir", DEFAULT_PLAYWRIGHT_ARTIFACTS_ROOT_DIR)),
            {"workspace_root": workspace_root, "data_root": data_root},
        )
        artifacts_root_dir = _resolve_path_under_data_root(
            artifacts_root_dir,
            data_root_path,
            label="browser_playwright_artifacts_root",
        )
    else:
        artifacts_root_dir = str(
            (
                workspace_root_path / DEFAULT_PLAYWRIGHT_WORKSPACE_ARTIFACTS_SUBPATH
            ).resolve(strict=False)
        )
    artifacts = PlaywrightArtifactsConfig(
        root_dir=artifacts_root_dir,
        screenshots_dir=_resolve_io_path(
            _expand_vars(
                str(
                    artifacts_cfg.get(
                        "screenshots_dir", DEFAULT_PLAYWRIGHT_SCREENSHOTS_DIR
                    )
                ),
                {
                    "workspace_root": workspace_root,
                    "data_root": data_root,
                    "artifacts.root_dir": artifacts_root_dir,
                },
            ),
            workspace_root=workspace_root_path,
            data_root=data_root_path,
            prefer_workspace=("root_dir" not in artifacts_cfg),
            label="browser_playwright_artifacts_screenshots",
        ),
        pdf_dir=_resolve_io_path(
            _expand_vars(
                str(artifacts_cfg.get("pdf_dir", DEFAULT_PLAYWRIGHT_PDF_DIR)),
                {
                    "workspace_root": workspace_root,
                    "data_root": data_root,
                    "artifacts.root_dir": artifacts_root_dir,
                },
            ),
            workspace_root=workspace_root_path,
            data_root=data_root_path,
            prefer_workspace=("root_dir" not in artifacts_cfg),
            label="browser_playwright_artifacts_pdf",
        ),
        traces_dir=_resolve_io_path(
            _expand_vars(
                str(artifacts_cfg.get("traces_dir", DEFAULT_PLAYWRIGHT_TRACES_DIR)),
                {
                    "workspace_root": workspace_root,
                    "data_root": data_root,
                    "artifacts.root_dir": artifacts_root_dir,
                },
            ),
            workspace_root=workspace_root_path,
            data_root=data_root_path,
            prefer_workspace=("root_dir" not in artifacts_cfg),
            label="browser_playwright_artifacts_traces",
        ),
    )

    persistent = PlaywrightPersistentConfig(
        enabled=bool(persistent_cfg.get("enabled", True)),
        user_data_dir=_resolve_path_under_data_root(
            _expand_vars(
                str(
                    persistent_cfg.get(
                        "user_data_dir",
                        DEFAULT_PLAYWRIGHT_PERSISTENT_USER_DATA_DIR,
                    )
                ),
                {"workspace_root": workspace_root, "data_root": data_root},
            ),
            data_root_path,
            label="browser_playwright_user_data",
        ),
    )

    downloads = PlaywrightDownloadsConfig(
        dir=_resolve_io_path(
            _expand_vars(
                str(
                    downloads_cfg.get(
                        "dir",
                        str(
                            (
                                workspace_root_path
                                / DEFAULT_PLAYWRIGHT_WORKSPACE_DOWNLOADS_SUBPATH
                            ).resolve(strict=False)
                        ),
                    )
                ),
                {"workspace_root": workspace_root, "data_root": data_root},
            ),
            workspace_root=workspace_root_path,
            data_root=data_root_path,
            prefer_workspace=("dir" not in downloads_cfg),
            label="browser_playwright_downloads",
        ),
        accept_downloads=bool(downloads_cfg.get("accept_downloads", True)),
    )

    timeouts = PlaywrightTimeoutConfig(
        navigation_ms=max(1, int(timeouts_cfg.get("navigation_ms", 30000))),
        action_ms=max(1, int(timeouts_cfg.get("action_ms", 15000))),
        api_ms=max(1, int(timeouts_cfg.get("api_ms", 20000))),
    )

    snapshot = PlaywrightSnapshotConfig(
        mode=str(snapshot_cfg.get("mode", "a11y")),
        max_nodes=_clamp_int(
            snapshot_cfg.get("max_nodes"), default=800, lower=50, upper=5000
        ),
        max_text_chars=_clamp_int(
            snapshot_cfg.get("max_text_chars"), default=20000, lower=1000, upper=200000
        ),
        include_css_path=bool(snapshot_cfg.get("include_css_path", False)),
        include_bbox=bool(snapshot_cfg.get("include_bbox", False)),
    )

    allow_domains: tuple[str, ...] = ()
    raw_domains = network_cfg.get("allow_domains")
    if isinstance(raw_domains, list):
        allow_domains = tuple(
            sorted(
                {str(item).strip().lower() for item in raw_domains if str(item).strip()}
            )
        )

    network = PlaywrightNetworkConfig(
        mode=str(network_cfg.get("mode", "policy")).strip().lower() or "policy",
        allow_domains=allow_domains,
    )

    return PlaywrightProviderConfig(
        browser=str(cfg.get("browser", "chromium")).strip().lower() or "chromium",
        headless_default=bool(cfg.get("headless_default", True)),
        slow_mo_ms=max(0, int(cfg.get("slow_mo_ms", 0))),
        locale=str(cfg.get("locale", "en-US")),
        timezone_id=str(cfg.get("timezone_id", "America/Los_Angeles")),
        viewport_width=max(320, int(viewport_cfg.get("width", 1280))),
        viewport_height=max(240, int(viewport_cfg.get("height", 720))),
        workspace_root=workspace_root,
        persistent=persistent,
        downloads=downloads,
        artifacts=artifacts,
        timeouts=timeouts,
        snapshot=snapshot,
        network=network,
    )


def _expand_vars(template: str, values: Mapping[str, str]) -> str:
    out = str(template)
    for key, value in values.items():
        out = out.replace("${" + key + "}", value)
    return out


def _resolve_path_under_data_root(
    path_value: str, data_root: Path, *, label: str
) -> str:
    candidate = Path(path_value).expanduser()
    if not candidate.is_absolute():
        candidate = data_root / candidate
    resolved = ensure_under_data_root(candidate, data_root, label=label)
    return str(resolved.resolve(strict=False))


def _resolve_io_path(
    path_value: str,
    *,
    workspace_root: Path,
    data_root: Path,
    prefer_workspace: bool,
    label: str,
) -> str:
    candidate = Path(str(path_value or "")).expanduser()
    if not candidate.is_absolute():
        candidate = (workspace_root / candidate).resolve(strict=False)
    else:
        candidate = candidate.resolve(strict=False)

    if prefer_workspace:
        try:
            candidate.relative_to(workspace_root)
            return str(candidate)
        except ValueError:
            pass
    return _resolve_path_under_data_root(str(candidate), data_root, label=label)


def _clamp_int(value: Any, *, default: int, lower: int, upper: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))
