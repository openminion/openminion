from __future__ import annotations

import importlib
from typing import Any

from openminion.base.config import bootstrap_home_paths
from openminion.services.diagnostics.debug import (
    DebugProvider,
    DebugStatus,
    ModuleDebugPayload,
    WiringSource,
)


def _module_import_error_payload(
    module: str,
    exc: BaseException,
    *,
    fallback: str | None = None,
    extra_details: dict[str, Any] | None = None,
) -> ModuleDebugPayload:
    details: dict[str, Any] = {"import_ok": False}
    if extra_details:
        details.update(extra_details)
    return ModuleDebugPayload(
        module=module,
        status=DebugStatus.WARN,
        mode="runtime",
        wiring_source=WiringSource.DISABLED,
        fallback=fallback or f"{module} module not installed",
        last_error=str(exc),
        details=details,
    )


def _module_unexpected_error_payload(
    module: str, exc: BaseException
) -> ModuleDebugPayload:
    return ModuleDebugPayload(
        module=module,
        status=DebugStatus.FAIL,
        mode="runtime",
        wiring_source=WiringSource.UNKNOWN,
        last_error=str(exc),
        details={"import_ok": False, "unexpected_error": True},
    )


def _module_runtime_path_payload(
    module: str,
    *,
    resolved_path: str,
    path_mode: str,
    path_source: str,
    details: dict[str, Any],
) -> ModuleDebugPayload:
    return ModuleDebugPayload(
        module=module,
        status=DebugStatus.OK,
        mode="runtime",
        wiring_source=WiringSource.REAL,
        resolved_path=resolved_path,
        path_mode=path_mode,
        path_source=path_source,
        details=details,
    )


def _runtime_path_details(
    *,
    path_key: str,
    resolved_path: str | None,
    path_mode: str,
    path_source: str,
    home_root: str | None,
    extra_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "import_ok": True,
        path_key: resolved_path,
        "path_mode": path_mode,
        "path_source": path_source,
        "home_root": str(home_root or ""),
    }
    if extra_details:
        details.update(extra_details)
    return details


def _module_init_outcome_payload(
    module: str,
    *,
    init_ok: bool,
    details: dict[str, Any],
    init_error: str | None = None,
    failure_status: DebugStatus = DebugStatus.FAIL,
) -> ModuleDebugPayload:
    if init_ok:
        return ModuleDebugPayload(
            module=module,
            status=DebugStatus.OK,
            mode="runtime",
            wiring_source=WiringSource.REAL,
            details=details,
        )
    return ModuleDebugPayload(
        module=module,
        status=failure_status,
        mode="runtime",
        wiring_source=WiringSource.STUB,
        last_error=init_error,
        details=details,
    )


class _ModuleDebugProvider(DebugProvider):
    MODULE_NAME: str = ""

    def __init__(self) -> None:
        super().__init__(
            module_name=self.MODULE_NAME,
            probe_fn=self._probe,
            wiring_check_fn=None,
        )


class OpenMinionRetrieveDebugProvider(_ModuleDebugProvider):
    MODULE_NAME = "openminion-retrieve"

    @staticmethod
    def _try_init_retrieve_service(config_path) -> tuple[bool, str | None]:
        from openminion.modules.retrieve import RetrieveCtl

        retrieve_service = None
        try:
            retrieve_service = RetrieveCtl(config=str(config_path), vector_adapter=None)
            return True, None
        except Exception as exc:
            return False, str(exc)
        finally:
            if retrieve_service:
                try:
                    retrieve_service.close()
                except Exception:
                    pass

    @staticmethod
    def _retrieve_config_details(config_path) -> dict:
        from openminion.modules.retrieve.config import (
            load_config as load_retrieve_config,
        )

        try:
            cfg = load_retrieve_config(config_path)
            return {
                "sqlite_path": str(cfg.storage.sqlite_path),
                "blob_root": str(cfg.storage.blob_root),
                "wal_mode": cfg.storage.wal_mode,
                "default_strategy": cfg.defaults.strategy,
            }
        except Exception:
            return {}

    def _probe(self) -> ModuleDebugPayload:
        try:
            from openminion.modules.retrieve import resolve_config_path

            try:
                config_path = resolve_config_path()
                config_exists = config_path.exists()
            except Exception as exc:
                return ModuleDebugPayload(
                    module="openminion-retrieve",
                    status=DebugStatus.FAIL,
                    mode="runtime",
                    wiring_source=WiringSource.STUB,
                    last_error=f"Config path resolution failed: {exc}",
                    details={"import_ok": True, "config_resolved": False},
                )

            init_ok, init_error = self._try_init_retrieve_service(config_path)
            config_details = self._retrieve_config_details(config_path)
            base_details = {
                "import_ok": True,
                "config_resolved": True,
                "config_path": str(config_path),
                "config_exists": config_exists,
                "init_ok": init_ok,
            }
            return _module_init_outcome_payload(
                module="openminion-retrieve",
                init_ok=init_ok,
                init_error=init_error,
                details={**base_details, **config_details} if init_ok else base_details,
            )

        except ImportError as exc:
            return _module_import_error_payload("openminion-retrieve", exc)
        except Exception as exc:
            return _module_unexpected_error_payload("openminion-retrieve", exc)


class OpenMinionSessionDebugProvider(_ModuleDebugProvider):
    MODULE_NAME = "openminion-session"

    def _probe(self) -> ModuleDebugPayload:
        try:
            from openminion.modules.session import SQLiteSessionStore, __version__

            store = None
            init_ok = False
            init_error = None
            db_path = None
            try:
                store = SQLiteSessionStore(database_path=":memory:")
                init_ok = True
                _ = store._conn.execute("SELECT 1").fetchone()
                db_path = ":memory:"
            except Exception as exc:
                init_ok = False
                init_error = str(exc)
            finally:
                if store:
                    try:
                        store.close()
                    except Exception:
                        pass

            success_details = {
                "import_ok": True,
                "version": __version__,
                "init_ok": True,
                "db_path": db_path,
                "store_type": "SQLiteSessionStore",
            }
            failure_details = {
                "import_ok": True,
                "version": __version__,
                "init_ok": False,
            }
            return _module_init_outcome_payload(
                module="openminion-session",
                init_ok=init_ok,
                init_error=init_error,
                details=success_details if init_ok else failure_details,
            )

        except ImportError as exc:
            return _module_import_error_payload("openminion-session", exc)
        except Exception as exc:
            return _module_unexpected_error_payload("openminion-session", exc)


class OpenMinionContextDebugProvider(_ModuleDebugProvider):
    MODULE_NAME = "openminion-context"

    def _probe(self) -> ModuleDebugPayload:
        try:
            importlib.import_module("openminion.modules.context.contracts")
            importlib.import_module("openminion.modules.context.schemas")

            service_importable = False
            try:
                importlib.import_module("openminion.modules.context.service")
                service_importable = True
            except ImportError:
                pass

            return ModuleDebugPayload(
                module="openminion-context",
                status=DebugStatus.OK,
                mode="runtime",
                wiring_source=WiringSource.REAL,
                details={
                    "import_ok": True,
                    "service_importable": service_importable,
                    "contracts_importable": True,
                    "schemas_importable": True,
                    "components": [
                        "SessionClient",
                        "MemoryClient",
                        "ContextPack",
                        "ContextBudgets",
                    ],
                },
            )

        except ImportError as exc:
            return _module_import_error_payload("openminion-context", exc)
        except Exception as exc:
            return _module_unexpected_error_payload("openminion-context", exc)


class OpenMinionMemoryDebugProvider(_ModuleDebugProvider):
    MODULE_NAME = "openminion-memory"

    def _probe(self) -> ModuleDebugPayload:
        try:
            from openminion.modules.memory import __version__

            importlib.import_module("openminion.modules.memory.service")
            importlib.import_module("openminion.modules.memory.storage")

            return ModuleDebugPayload(
                module="openminion-memory",
                status=DebugStatus.OK,
                mode="runtime",
                wiring_source=WiringSource.REAL,
                details={
                    "import_ok": True,
                    "version": __version__,
                    "service_importable": True,
                    "store_importable": True,
                    "note": "MemoryService requires MemoryStore initialization with config",
                },
            )

        except ImportError as exc:
            return _module_import_error_payload("openminion-memory", exc)
        except Exception as exc:
            return _module_unexpected_error_payload("openminion-memory", exc)


class OpenMinionCompressDebugProvider(_ModuleDebugProvider):
    MODULE_NAME = "context.compress"

    def _probe(self) -> ModuleDebugPayload:
        try:
            from openminion.modules.context.compress import resolve_config_path
            from openminion.modules.context.compress.registry import MethodRegistry

            config_path = None
            config_exists = False
            try:
                config_path = resolve_config_path()
                config_exists = config_path.exists()
            except Exception:
                pass

            registry = None
            init_ok = False
            init_error = None
            method_count = 0
            try:
                registry = MethodRegistry()
                init_ok = True
                method_count = len(registry.list_methods())
            except Exception as exc:
                init_error = str(exc)

            details = {
                "import_ok": True,
                "config_resolved": config_path is not None,
                "config_exists": config_exists,
                "init_ok": init_ok,
                "method_count": method_count,
            }
            if config_path:
                details["config_path"] = str(config_path)

            return _module_init_outcome_payload(
                module="context.compress",
                init_ok=init_ok,
                init_error=init_error,
                details=details,
                failure_status=DebugStatus.WARN,
            )

        except ImportError as exc:
            return _module_import_error_payload("context.compress", exc)
        except Exception as exc:
            return _module_unexpected_error_payload("context.compress", exc)


class OpenMinionSkillDebugProvider(_ModuleDebugProvider):
    MODULE_NAME = "openminion-skill"

    @staticmethod
    def _collect_skill_inventory(skill_ctl) -> tuple[int, list, dict, str, str, str]:
        skills = skill_ctl.list_skills({})
        resolved_path = str(getattr(skill_ctl.config, "sqlite_path", ""))
        path_mode = str(getattr(skill_ctl.config, "path_mode", "module_standalone"))
        path_source = str(
            getattr(skill_ctl.config, "path_source", "standalone_default")
        )
        ingested_skills: list = []
        fixture_metadata: dict = {}
        for skill in skills:
            skill_id = skill.get("skill_id") or skill.get("id")
            if not skill_id:
                continue
            ingested_skills.append(
                {
                    "skill_id": skill_id,
                    "name": skill.get("name", ""),
                    "version": skill.get("version", ""),
                }
            )
            if skill_id.startswith("cli-chat-smoke-"):
                fixture_metadata[skill_id] = {
                    "type": "fixture",
                    "name": skill.get("name", ""),
                    "version": skill.get("version", ""),
                    "tags": skill.get("tags", []),
                }
        return (
            len(skills),
            ingested_skills,
            fixture_metadata,
            resolved_path,
            path_mode,
            path_source,
        )

    def _probe(self) -> ModuleDebugPayload:
        try:
            from openminion.modules.skill import Skill

            home_paths = bootstrap_home_paths()
            resolved_path: str | None = None
            path_mode = "module_standalone"
            path_source = "standalone_default"
            ingested_skills: list = []
            fixture_metadata: dict = {}
            skill_count = 0
            last_error: str | None = None
            try:
                skill_ctl = Skill(config={}, home_root=home_paths.home_root)
                try:
                    (
                        skill_count,
                        ingested_skills,
                        fixture_metadata,
                        resolved_path,
                        path_mode,
                        path_source,
                    ) = self._collect_skill_inventory(skill_ctl)
                except Exception as exc:
                    last_error = str(exc)
                finally:
                    skill_ctl.close()
            except Exception as exc:
                last_error = str(exc)

            fixture_summary = {
                "fixture_skills_count": len(fixture_metadata),
                "fixture_skills": list(fixture_metadata.keys()),
                "fixture_details": fixture_metadata,
                "last_ingest_status": "ok" if fixture_metadata else "no_fixtures",
            }

            return ModuleDebugPayload(
                module="openminion-skill",
                status=DebugStatus.OK,
                mode="runtime",
                wiring_source=WiringSource.REAL,
                resolved_path=resolved_path,
                path_mode=path_mode,
                path_source=path_source,
                details={
                    "import_ok": True,
                    "skill_count": skill_count,
                    "ingested_skills": ingested_skills[:10],
                    "last_error": last_error,
                    "sqlite_path": resolved_path,
                    "path_mode": path_mode,
                    "path_source": path_source,
                    "home_root": str(home_paths.home_root),
                    "nl_ingest": self._get_nl_ingest_diagnostics(),
                    "fixture_metadata": fixture_summary,
                },
            )

        except ImportError as exc:
            return _module_import_error_payload("openminion-skill", exc)
        except Exception as exc:
            return _module_unexpected_error_payload("openminion-skill", exc)

    def _get_nl_ingest_diagnostics(self) -> dict:
        test_cases = [
            ("path", "read /path/to/SKILL.md and learn it", "/path/to/SKILL.md"),
            (
                "url",
                "learn this skill from https://example.com/SKILL.md",
                "https://example.com/SKILL.md",
            ),
            (
                "url_raw",
                "load https://raw.githubusercontent.com/org/repo/main/skill.md",
                "https://raw.githubusercontent.com/org/repo/main/skill.md",
            ),
        ]

        diagnostics = {
            "source_extraction_available": True,
            "url_ingest_available": True,
            "source_patterns": {
                "url": [
                    r"https?://[^\s]+\.md(?:\?[^\s]*)?",
                ],
                "path": [
                    r"/[^\s]+\.md",
                    r"[A-Za-z]:\\[^\s]+\.md",
                    r"\.\.?/[^\s]+\.md",
                ],
            },
            "test_extractions": [],
            "error_codes": [
                "INVALID_SCHEME",
                "BLOCKED_HOST",
                "INVALID_FILE_TYPE",
                "FETCH_FAILED",
                "INVALID_MARKDOWN",
                "FETCH_EXCEPTION",
                "INGEST_DISABLED",
                "PATH_TRAVERSAL",
                "PATH_NOT_ALLOWED",
                "PATH_NOT_FOUND",
            ],
        }

        for source_type, message, expected in test_cases:
            extracted = self._test_extract_source(message)
            diagnostics["test_extractions"].append(
                {
                    "type": source_type,
                    "message": message,
                    "expected": expected,
                    "extracted": extracted.get("value") if extracted else None,
                    "match": (extracted.get("value") == expected)
                    if extracted
                    else False,
                }
            )

        return diagnostics

    def _test_extract_source(self, message: str) -> dict | None:
        import re

        url_patterns = [
            r"(https?://[^\s]+\.md(?:\?[^\s]*)?)",
            r"(https?://[^\s]+/[^\s]*\.md(?:\?[^\s]*)?)",
        ]
        for pattern in url_patterns:
            match = re.search(pattern, message, re.IGNORECASE)
            if match:
                return {"type": "url", "value": match.group(1)}

        path_patterns = [
            r"(/[^\s]+\.md)",
            r"([A-Za-z]:\\[^\s]+\.md)",
            r"(\.\.?/[^\s]+\.md)",
        ]
        for pattern in path_patterns:
            match = re.search(pattern, message)
            if match:
                return {"type": "path", "value": match.group(1)}

        return None


class OpenMinionRegistryDebugProvider(_ModuleDebugProvider):
    MODULE_NAME = "openminion-registry"

    def _probe(self) -> ModuleDebugPayload:
        try:
            from openminion.modules.registry.config import (
                load_config as load_registry_config,
            )

            home_paths = bootstrap_home_paths()
            cfg = load_registry_config(home_root=home_paths.home_root)
            resolved_path = str(cfg.store.sqlite_path)
            path_mode = str(getattr(cfg.store, "path_mode", "unknown"))
            path_source = str(getattr(cfg.store, "path_source", "unknown"))

            return _module_runtime_path_payload(
                module="openminion-registry",
                resolved_path=resolved_path,
                path_mode=path_mode,
                path_source=path_source,
                details=_runtime_path_details(
                    path_key="sqlite_path",
                    resolved_path=resolved_path,
                    path_mode=path_mode,
                    path_source=path_source,
                    home_root=str(getattr(cfg, "home_root", "") or ""),
                    extra_details={"manifest_path": str(cfg.manifest_path)},
                ),
            )
        except ImportError as exc:
            return _module_import_error_payload("openminion-registry", exc)
        except Exception as exc:
            return _module_unexpected_error_payload("openminion-registry", exc)


class OpenMinionTelemetryDebugProvider(_ModuleDebugProvider):
    MODULE_NAME = "openminion-telemetry"

    def _probe(self) -> ModuleDebugPayload:
        try:
            from openminion.modules.telemetry.service import resolve_telemetry_db_path

            home_paths = bootstrap_home_paths()
            path_info = resolve_telemetry_db_path(home_root=home_paths.home_root)

            return _module_runtime_path_payload(
                module="openminion-telemetry",
                resolved_path=path_info.db_path,
                path_mode=path_info.path_mode,
                path_source=path_info.path_source,
                details=_runtime_path_details(
                    path_key="db_path",
                    resolved_path=path_info.db_path,
                    path_mode=path_info.path_mode,
                    path_source=path_info.path_source,
                    home_root=path_info.home_root,
                ),
            )
        except ImportError as exc:
            return _module_import_error_payload("openminion-telemetry", exc)
        except Exception as exc:
            return _module_unexpected_error_payload("openminion-telemetry", exc)


class OpenMinionControlplaneDebugProvider(_ModuleDebugProvider):
    MODULE_NAME = "openminion-controlplane"

    def _probe(self) -> ModuleDebugPayload:
        try:
            from openminion.modules.controlplane.config import (
                load_config as load_controlplane_config,
            )

            home_paths = bootstrap_home_paths()
            cfg = load_controlplane_config(home_root=home_paths.home_root)
            resolved_path = str(cfg.sqlite_path)
            path_mode = str(getattr(cfg, "path_mode", "unknown"))
            path_source = str(getattr(cfg, "path_source", "unknown"))

            return _module_runtime_path_payload(
                module="openminion-controlplane",
                resolved_path=resolved_path,
                path_mode=path_mode,
                path_source=path_source,
                details=_runtime_path_details(
                    path_key="sqlite_path",
                    resolved_path=resolved_path,
                    path_mode=path_mode,
                    path_source=path_source,
                    home_root=str(getattr(cfg, "home_root", "") or ""),
                    extra_details={
                        "store_backend": str(getattr(cfg, "store_backend", "sqlite"))
                    },
                ),
            )
        except ImportError as exc:
            return _module_import_error_payload("openminion-controlplane", exc)
        except Exception as exc:
            return _module_unexpected_error_payload("openminion-controlplane", exc)


class OpenMinionIdentityDebugProvider(_ModuleDebugProvider):
    MODULE_NAME = "openminion-identity"

    @staticmethod
    def _build_identity_test_profile(AgentProfile):
        return AgentProfile.model_validate(
            {
                "agent_id": "test-agent",
                "display_name": "Test Agent",
                "profile_revision": 1,
                "role": {
                    "mission": "Test mission",
                    "responsibilities": ["Test responsibility"],
                    "hard_constraints": ["Test constraint"],
                    "domain": ["test"],
                },
                "personality": {
                    "tone": "professional",
                    "verbosity": "normal",
                },
                "risk": {"risk_level": "low"},
                "tool_posture": {"tool_use": "restricted"},
            }
        )

    def _probe(self) -> ModuleDebugPayload:
        try:
            from openminion.modules.identity.models import AgentProfile
            from openminion.modules.identity.runtime.renderer import (
                render_identity_snippet,
            )
            from openminion.modules.identity.runtime.service import IdentityCtl
            from openminion.modules.identity.storage import InMemoryIdentityStore

            components = {
                "AgentProfile": AgentProfile is not None,
                "IdentityCtl": IdentityCtl is not None,
                "InMemoryIdentityStore": InMemoryIdentityStore is not None,
                "render_identity_snippet": render_identity_snippet is not None,
            }

            store = InMemoryIdentityStore()
            ctl = IdentityCtl(store=store)
            test_profile = self._build_identity_test_profile(AgentProfile)
            ctl.upsert_profile(test_profile)
            profile_version = ctl._compute_profile_version(test_profile)
            snippet = render_identity_snippet(
                profile=test_profile,
                purpose="decide",
                max_tokens=500,
                max_chars=2000,
                render_version="v1",
                profile_version=profile_version,
                bullet_prefix="- ",
                section_headers=True,
            )

            return ModuleDebugPayload(
                module="openminion-identity",
                status=DebugStatus.OK,
                mode="runtime",
                wiring_source=WiringSource.REAL,
                details={
                    "import_ok": True,
                    "components": components,
                    "profile_schema_version": "v1",
                    "snippet_render_ok": snippet is not None,
                    "snippet_text_length": len(snippet.text) if snippet else 0,
                },
            )

        except ImportError as exc:
            return _module_import_error_payload(
                "openminion-identity",
                exc,
                extra_details={"reason": "Module not installed or not in PYTHONPATH"},
            )
        except Exception as exc:
            return _module_unexpected_error_payload("openminion-identity", exc)
