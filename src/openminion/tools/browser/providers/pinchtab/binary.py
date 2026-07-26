from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import stat
import sys
import time
import uuid
from contextlib import contextmanager
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from openminion.base.config.paths import ensure_under_data_root

PINCHTAB_BIN_ENV = "PINCHTAB_BIN"
PINCHTAB_VERSION_ENV = "OPENMINION_PINCHTAB_RELEASE"
PINCHTAB_SOURCE_ENV = "OPENMINION_PINCHTAB_SOURCE"
PINCHTAB_ALLOW_EXTERNAL_ENV = "OPENMINION_PINCHTAB_ALLOW_EXTERNAL"
PINCHTAB_INSTALL_MODE_ENV = "OPENMINION_PINCHTAB_INSTALL_MODE"
PINCHTAB_SHA256_ENV = "OPENMINION_PINCHTAB_SHA256"
PINCHTAB_PLATFORM_ENV = "OPENMINION_PINCHTAB_PLATFORM"
PINCHTAB_DOWNLOAD_URL_ENV = "OPENMINION_PINCHTAB_DOWNLOAD_URL"
PINCHTAB_E2E_ENV = "PINCHTAB_E2E"

_MANIFEST_NAME = "manifest.json"
_CHECKSUM_NAME = "pinchtab.sha256"
_BINARY_NAME = "pinchtab.exe" if os.name == "nt" else "pinchtab"
_LOCK_STALE_SECONDS = 10 * 60
_MAX_RELEASE_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_BINARY_BYTES = 256 * 1024 * 1024

DownloadFn = Callable[[str, Path], None]
EventSink = Callable[[str, dict[str, Any]], None]
ReleaseDiscoveryFn = Callable[["PinchTabBinaryConfig"], "PinchTabReleaseAsset"]


@dataclass(frozen=True)
class PinchTabBinaryConfig:
    data_root: Path
    install_mode: str = "never"
    version: str = "latest"
    source: str = "github_release"
    sha256: str = ""
    platform_id: str = ""
    allow_external: bool = False
    explicit_path: str = ""
    download_url: str = ""
    e2e_enabled: bool = False

    @classmethod
    def from_env(
        cls, *, data_root: Path, runtime_env: Mapping[str, str] | None = None
    ) -> "PinchTabBinaryConfig":
        env = {**os.environ, **dict(runtime_env or {})}
        return cls(
            data_root=data_root,
            install_mode=_env_value(env, PINCHTAB_INSTALL_MODE_ENV, "never"),
            version=_env_value(env, PINCHTAB_VERSION_ENV, "latest"),
            source=_env_value(env, PINCHTAB_SOURCE_ENV, "github_release"),
            sha256=_env_value(env, PINCHTAB_SHA256_ENV, ""),
            platform_id=_env_value(env, PINCHTAB_PLATFORM_ENV, ""),
            allow_external=_is_truthy(_env_value(env, PINCHTAB_ALLOW_EXTERNAL_ENV, "")),
            explicit_path=_env_value(env, PINCHTAB_BIN_ENV, ""),
            download_url=_env_value(env, PINCHTAB_DOWNLOAD_URL_ENV, ""),
            e2e_enabled=_is_truthy(_env_value(env, PINCHTAB_E2E_ENV, "")),
        )


@dataclass(frozen=True)
class PinchTabBinaryResolution:
    binary_path: Path | None
    version: str
    platform_id: str
    source: str
    verified: bool
    managed: bool
    legacy: bool
    manifest_path: Path | None = None
    error_code: str = ""
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.binary_path is not None and not self.error_code

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "binary_path": str(self.binary_path) if self.binary_path else "",
            "version": self.version,
            "platform": self.platform_id,
            "source": self.source,
            "verified": self.verified,
            "managed": self.managed,
            "legacy": self.legacy,
            "manifest_path": str(self.manifest_path) if self.manifest_path else "",
            "error_code": self.error_code,
            "message": self.message,
        }


@dataclass(frozen=True)
class PinchTabReleaseAsset:
    download_url: str
    sha256: str = ""
    resolved_version: str = ""


class PinchTabBinaryError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def as_resolution(self, *, cfg: PinchTabBinaryConfig) -> PinchTabBinaryResolution:
        return PinchTabBinaryResolution(
            binary_path=None,
            version=cfg.version,
            platform_id=_platform_id(cfg.platform_id),
            source=cfg.source,
            verified=False,
            managed=False,
            legacy=False,
            error_code=self.code,
            message=self.message,
        )


class PinchTabBinaryResolver:
    def __init__(
        self,
        cfg: PinchTabBinaryConfig,
        *,
        downloader: DownloadFn | None = None,
        release_discoverer: ReleaseDiscoveryFn | None = None,
        event_sink: EventSink | None = None,
    ) -> None:
        self.cfg = cfg
        self._downloader = downloader or _download_url
        self._release_discoverer = release_discoverer or _discover_github_release_asset
        self._download_requires_e2e = downloader is None
        self._discovery_requires_e2e = release_discoverer is None
        self._event_sink = event_sink
        self._correlation_id = f"pinchtab-{uuid.uuid4().hex}"

    @property
    def root(self) -> Path:
        return ensure_under_data_root(
            self.cfg.data_root / "sidecars" / "pinchtab",
            self.cfg.data_root,
            label="pinchtab_binary_root",
        )

    def status(self) -> PinchTabBinaryResolution:
        try:
            cached = self._cached_resolution()
            if cached.error_code == "PINCHTAB_UNVERIFIED_MANAGED_BINARY":
                return cached
            return self.resolve(allow_download=False)
        except PinchTabBinaryError as exc:
            return exc.as_resolution(cfg=self.cfg)

    def resolve(self, *, allow_download: bool = False) -> PinchTabBinaryResolution:
        if self.cfg.explicit_path:
            return self._resolve_explicit_path()
        cached = self._cached_resolution()
        if cached.ok:
            return cached
        if allow_download and self.cfg.install_mode in {"auto", "required"}:
            return self._download_and_resolve()
        if self.cfg.allow_external:
            legacy = shutil.which("pinchtab")
            if legacy:
                self._emit("sidecar.pinchtab.legacy_use", {"binary_path": legacy})
                return PinchTabBinaryResolution(
                    binary_path=Path(legacy).resolve(strict=False),
                    version="external",
                    platform_id=_platform_id(self.cfg.platform_id),
                    source="legacy_npm",
                    verified=False,
                    managed=False,
                    legacy=True,
                )
        raise PinchTabBinaryError(
            "PINCHTAB_MISSING",
            "PinchTab binary is not cached under data_root. Run `openminion sidecar pinchtab install` or set OPENMINION_PINCHTAB_ALLOW_EXTERNAL=1.",
        )

    def _resolve_explicit_path(self) -> PinchTabBinaryResolution:
        candidate = Path(self.cfg.explicit_path).expanduser().resolve(strict=False)
        if not self.cfg.allow_external and not _is_under(candidate, self.cfg.data_root):
            raise PinchTabBinaryError(
                "PINCHTAB_EXTERNAL_PATH_DENIED",
                f"pinchtab_binary must be under data_root ({self.cfg.data_root}), got {candidate}",
            )
        if not candidate.exists():
            raise PinchTabBinaryError(
                "PINCHTAB_MISSING", f"PinchTab binary not found: {candidate}"
            )
        return PinchTabBinaryResolution(
            binary_path=candidate,
            version=self.cfg.version,
            platform_id=_platform_id(self.cfg.platform_id),
            source="local_path",
            verified=_sha256_matches(candidate, self.cfg.sha256)
            if self.cfg.sha256
            else False,
            managed=_is_under(candidate, self.cfg.data_root),
            legacy=not _is_under(candidate, self.cfg.data_root),
        )

    def _cached_resolution(
        self, *, trusted_sha256: str | None = None
    ) -> PinchTabBinaryResolution:
        release_dir = self._release_dir()
        binary = release_dir / _BINARY_NAME
        manifest_path = release_dir / _MANIFEST_NAME
        if not binary.exists():
            return PinchTabBinaryResolution(
                binary_path=None,
                version=self.cfg.version,
                platform_id=_platform_id(self.cfg.platform_id),
                source="cache",
                verified=False,
                managed=True,
                legacy=False,
                manifest_path=manifest_path,
                error_code="PINCHTAB_MISSING",
                message="No cached PinchTab binary found.",
            )
        verified = _cached_binary_verified(
            binary,
            self.cfg.sha256 if trusted_sha256 is None else trusted_sha256,
            release_dir,
        )
        if not verified:
            return PinchTabBinaryResolution(
                binary_path=None,
                version=self.cfg.version,
                platform_id=_platform_id(self.cfg.platform_id),
                source="cache",
                verified=False,
                managed=True,
                legacy=False,
                manifest_path=manifest_path,
                error_code="PINCHTAB_UNVERIFIED_MANAGED_BINARY",
                message="Cached managed PinchTab binary has no trusted checksum.",
            )
        return PinchTabBinaryResolution(
            binary_path=binary,
            version=self.cfg.version,
            platform_id=_platform_id(self.cfg.platform_id),
            source="cache",
            verified=verified,
            managed=True,
            legacy=False,
            manifest_path=manifest_path,
        )

    def _download_and_resolve(self) -> PinchTabBinaryResolution:
        source_url = self.cfg.download_url
        expected_sha256 = self.cfg.sha256
        resolved_version = self.cfg.version
        if not source_url and self.cfg.source == "github_release":
            if self._discovery_requires_e2e and not self.cfg.e2e_enabled:
                raise PinchTabBinaryError(
                    "PINCHTAB_DOWNLOAD_NOT_APPROVED",
                    "PinchTab GitHub release discovery is disabled unless PINCHTAB_E2E=1 or an injected release discoverer is used.",
                )
            asset = self._release_discoverer(self.cfg)
            source_url = asset.download_url
            expected_sha256 = expected_sha256 or asset.sha256
            resolved_version = asset.resolved_version or resolved_version
        if not source_url:
            raise PinchTabBinaryError(
                "PINCHTAB_DOWNLOAD_URL_MISSING",
                "PinchTab download requires OPENMINION_PINCHTAB_DOWNLOAD_URL or github_release discovery.",
            )
        if self._download_requires_e2e and not self.cfg.e2e_enabled:
            raise PinchTabBinaryError(
                "PINCHTAB_DOWNLOAD_NOT_APPROVED",
                "PinchTab download is disabled unless PINCHTAB_E2E=1 or an injected downloader is used.",
            )
        if not expected_sha256:
            raise PinchTabBinaryError(
                "PINCHTAB_UNVERIFIED_MANAGED_BINARY",
                "Managed PinchTab downloads require configured sha256 or trusted release digest.",
            )
        if not _is_sha256_hex(expected_sha256):
            raise PinchTabBinaryError(
                "PINCHTAB_INVALID_TRUST_DIGEST",
                "Managed PinchTab downloads require a 64-character sha256 digest.",
            )
        trusted_payload = self._event_payload(verified=True)
        self._emit("sidecar.pinchtab.download.requested", trusted_payload)
        self._emit("sidecar.pinchtab.download.approved", trusted_payload)
        release_dir = self._release_dir()
        binary = release_dir / _BINARY_NAME
        try:
            with _publish_lock(self.root / "lock"):
                cached = self._cached_resolution(trusted_sha256=expected_sha256)
                if cached.ok:
                    self._emit(
                        "sidecar.pinchtab.download.completed",
                        self._event_payload(verified=True),
                    )
                    return cached
                release_dir.mkdir(parents=True, exist_ok=True)
                tmp_binary = release_dir / f".{_BINARY_NAME}.{os.getpid()}.tmp"
                self._downloader(source_url, tmp_binary)
                if tmp_binary.stat().st_size > _MAX_BINARY_BYTES:
                    raise PinchTabBinaryError(
                        "PINCHTAB_DOWNLOAD_TOO_LARGE",
                        "Downloaded PinchTab binary exceeded the maximum allowed size.",
                    )
                _make_executable(tmp_binary)
                if not _sha256_matches(tmp_binary, expected_sha256):
                    tmp_binary.unlink(missing_ok=True)
                    raise PinchTabBinaryError(
                        "PINCHTAB_CHECKSUM_MISMATCH",
                        "Downloaded PinchTab checksum did not match trusted sha256.",
                    )
                os.replace(tmp_binary, binary)
                self._write_manifest(
                    binary=binary,
                    source_url=source_url,
                    resolved_version=resolved_version,
                    verified=True,
                )
        except PinchTabBinaryError as exc:
            self._emit_download_failed(exc)
            raise
        except (OSError, URLError) as exc:
            self._emit_download_failed(exc)
            raise PinchTabBinaryError("PINCHTAB_DOWNLOAD_FAILED", str(exc)) from exc
        self._emit("sidecar.pinchtab.download.completed", self._event_payload())
        return self._cached_resolution(trusted_sha256=expected_sha256)

    def _emit_download_failed(self, exc: Exception) -> None:
        self._emit(
            "sidecar.pinchtab.download.failed",
            {**self._event_payload(), "error": str(exc)},
        )

    def _release_dir(self) -> Path:
        return ensure_under_data_root(
            self.root
            / "releases"
            / self.cfg.version
            / _platform_id(self.cfg.platform_id),
            self.cfg.data_root,
            label="pinchtab_release_dir",
        )

    def _write_manifest(
        self,
        *,
        binary: Path,
        source_url: str,
        resolved_version: str,
        verified: bool,
    ) -> None:
        payload = {
            "version": resolved_version,
            "platform": _platform_id(self.cfg.platform_id),
            "source": self.cfg.source,
            "source_url": source_url,
            "download_time": int(time.time()),
            "size_bytes": binary.stat().st_size,
            "sha256": _sha256(binary),
            "verified": verified,
        }
        (binary.parent / _MANIFEST_NAME).write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (binary.parent / _CHECKSUM_NAME).write_text(
            f"{payload['sha256']}  {_BINARY_NAME}\n", encoding="utf-8"
        )

    def _event_payload(self, *, verified: bool | None = None) -> dict[str, Any]:
        return {
            "correlation_id": self._correlation_id,
            "version": self.cfg.version,
            "platform": _platform_id(self.cfg.platform_id),
            "source": self.cfg.source,
            "verified": bool(self.cfg.sha256) if verified is None else verified,
            "managed": True,
        }

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._event_sink is not None:
            self._event_sink(event, payload)


def build_pinchtab_binary_resolver(
    *,
    data_root: Path,
    runtime_env: Mapping[str, str] | None = None,
    event_sink: EventSink | None = None,
    downloader: DownloadFn | None = None,
    release_discoverer: ReleaseDiscoveryFn | None = None,
) -> PinchTabBinaryResolver:
    return PinchTabBinaryResolver(
        PinchTabBinaryConfig.from_env(data_root=data_root, runtime_env=runtime_env),
        event_sink=event_sink,
        downloader=downloader,
        release_discoverer=release_discoverer,
    )


def _env_value(env: Mapping[str, str], key: str, default: str) -> str:
    value = str(env.get(key, "") or "").strip()
    return value or default


def _is_truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _platform_id(override: str) -> str:
    if override.strip():
        return override.strip()
    system = platform.system().lower() or sys.platform.lower()
    machine = platform.machine().lower() or "unknown"
    aliases = {
        "darwin": "darwin",
        "linux": "linux",
        "windows": "windows",
        "x86_64": "x86_64",
        "amd64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    return f"{aliases.get(system, system)}/{aliases.get(machine, machine)}"


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _cached_binary_verified(
    binary: Path, configured_sha256: str, release_dir: Path
) -> bool:
    if configured_sha256:
        return _sha256_matches(binary, configured_sha256)
    manifest_path = release_dir / _MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    manifest_sha = str(manifest.get("sha256") or "").strip()
    return bool(
        manifest.get("verified") is True
        and manifest_sha
        and _sha256_matches(binary, manifest_sha)
    )


def _sha256_matches(path: Path, expected: str) -> bool:
    return _sha256(path).lower() == expected.strip().lower()


def _is_sha256_hex(value: str) -> bool:
    text = value.strip()
    return len(text) == 64 and all(char in "0123456789abcdefABCDEF" for char in text)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_executable(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _discover_github_release_asset(cfg: PinchTabBinaryConfig) -> PinchTabReleaseAsset:
    version = cfg.version.strip() or "latest"
    endpoint = (
        "https://api.github.com/repos/pinchtab/pinchtab/releases/latest"
        if version == "latest"
        else "https://api.github.com/repos/pinchtab/pinchtab/releases/tags/"
        + quote(version, safe="")
    )
    request = Request(endpoint, headers={"Accept": "application/vnd.github+json"})
    with urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.loads(
            _read_bounded(response, _MAX_RELEASE_RESPONSE_BYTES).decode("utf-8")
        )
    if not isinstance(payload, dict):
        raise PinchTabBinaryError(
            "PINCHTAB_RELEASE_DISCOVERY_FAILED",
            "GitHub release response was not an object.",
        )
    platform_id = _platform_id(cfg.platform_id)
    asset = _select_release_asset(payload.get("assets"), platform_id=platform_id)
    checksum = _select_release_checksum(payload.get("assets"), platform_id=platform_id)
    resolved_version = str(payload.get("tag_name") or version).strip() or version
    return PinchTabReleaseAsset(
        download_url=asset,
        sha256=checksum,
        resolved_version=resolved_version,
    )


def _select_release_asset(assets: Any, *, platform_id: str) -> str:
    if not isinstance(assets, list):
        raise PinchTabBinaryError(
            "PINCHTAB_RELEASE_ASSET_MISSING",
            "GitHub release response did not include assets.",
        )
    platform_terms = {
        term for term in platform_id.lower().replace("/", "-").split("-") if term
    }
    for item in assets:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").lower()
        if not name or "sha256" in name or "checksum" in name:
            continue
        if "pinchtab" not in name:
            continue
        if platform_terms and not all(term in name for term in platform_terms):
            continue
        url = str(item.get("browser_download_url") or "").strip()
        if url:
            return url
    raise PinchTabBinaryError(
        "PINCHTAB_RELEASE_ASSET_MISSING",
        f"No PinchTab release asset matched platform {platform_id!r}.",
    )


def _download_url(url: str, destination: Path) -> None:
    with urlopen(url, timeout=30) as response:  # noqa: S310
        destination.write_bytes(_read_bounded(response, _MAX_BINARY_BYTES))


def _select_release_checksum(assets: Any, *, platform_id: str) -> str:
    if not isinstance(assets, list):
        return ""
    platform_terms = {
        term for term in platform_id.lower().replace("/", "-").split("-") if term
    }
    for item in assets:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "").lower()
        if "sha256" not in name and "checksum" not in name:
            continue
        if platform_terms and not all(term in name for term in platform_terms):
            continue
        digest = str(item.get("digest") or "").strip()
        if digest.startswith("sha256:"):
            return digest.removeprefix("sha256:").strip()
    return ""


def _read_bounded(response: Any, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(min(1024 * 1024, max_bytes + 1 - total))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise PinchTabBinaryError(
                "PINCHTAB_DOWNLOAD_TOO_LARGE",
                "PinchTab download response exceeded the maximum allowed size.",
            )
        chunks.append(chunk)


@contextmanager
def _publish_lock(lock_path: Path) -> Any:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                age = 0
            if age > _LOCK_STALE_SECONDS:
                lock_path.unlink(missing_ok=True)
                continue
            time.sleep(0.05)
            continue
        break
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{os.getpid()}\n")
        yield
    finally:
        lock_path.unlink(missing_ok=True)


__all__ = [
    "PINCHTAB_ALLOW_EXTERNAL_ENV",
    "PINCHTAB_BIN_ENV",
    "PINCHTAB_DOWNLOAD_URL_ENV",
    "PINCHTAB_E2E_ENV",
    "PINCHTAB_INSTALL_MODE_ENV",
    "PINCHTAB_PLATFORM_ENV",
    "PINCHTAB_SHA256_ENV",
    "PINCHTAB_SOURCE_ENV",
    "PINCHTAB_VERSION_ENV",
    "PinchTabBinaryConfig",
    "PinchTabBinaryError",
    "PinchTabBinaryResolution",
    "PinchTabBinaryResolver",
    "PinchTabReleaseAsset",
    "build_pinchtab_binary_resolver",
]
