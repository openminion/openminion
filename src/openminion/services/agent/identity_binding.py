from pathlib import Path
from typing import Any

from openminion.base.config.env import EnvironmentConfig
from openminion.base.config.runtime import resolve_identity_root_from_env
from openminion.base.constants import (
    OPENMINION_IDENTITY_DB_ENV,
    OPENMINION_IDENTITY_ROOT_ENV,
)
from openminion.modules.identity.config import (
    from_base_config,
    resolve_default_render_budget,
)
from openminion.modules.identity.runtime.renderer import (
    _CANONICAL_PURPOSES,
    normalize_purpose,
)
from openminion.services.config import (
    resolve_services_env,
    resolve_services_path,
    resolve_services_roots,
)
from openminion.services.bootstrap.paths import (
    SERVICES_IDENTITY_DB_FILENAME,
    SERVICES_IDENTITY_SUBDIR,
)

from .constants import (
    IDENTITY_RUNTIME_STATUS_BUNDLE_EMPTY,
    IDENTITY_RUNTIME_STATUS_BUNDLE_INVALID,
    IDENTITY_RUNTIME_STATUS_BUNDLE_ROOT_UNSET,
    IDENTITY_RUNTIME_STATUS_IDENTITYCTL_UNAVAILABLE,
    IDENTITY_RUNTIME_STATUS_IDENTITY_ROOT_MISSING,
    IDENTITY_RUNTIME_STATUS_IDENTITY_ROOT_NOT_DIRECTORY,
    IDENTITY_RUNTIME_STATUS_IMPORTED,
    IDENTITY_RUNTIME_STATUS_IMPORT_FAILED,
    IDENTITY_RUNTIME_STATUS_NOT_STARTED,
    IDENTITY_RUNTIME_STATUS_NO_YAML_PROFILES,
    IDENTITY_RUNTIME_STATUS_PARTIAL_FAILURE,
    IDENTITY_RUNTIME_STATUS_SKIPPED_AUTHORITY,
    IDENTITY_RUNTIME_STATUS_SKIPPED_UNCHANGED,
    IDENTITY_RUNTIME_STATUS_SYNCED,
    IDENTITY_RUNTIME_STATUS_SYNC_FAILED,
)
from .prompt_history import _IDENTITY_FRAME, _resolve_system_prompt


class AgentIdentityMixin:
    def _resolve_identity_render_purpose(
        self,
        *,
        inbound_metadata: dict[str, str] | None = None,
    ) -> str:
        metadata = dict(inbound_metadata or {})
        for key in (
            "identity_purpose",
            "turn_purpose",
            "purpose",
            "llm_purpose",
            "turn_phase",
            "phase",
        ):
            value = str(metadata.get(key, "") or "").strip()
            if value:
                return normalize_purpose(value)
        return "act"

    def _identity_env(self) -> EnvironmentConfig:
        env_owner = getattr(self, "_env", None)
        if isinstance(env_owner, EnvironmentConfig):
            return env_owner
        runtime_env = getattr(getattr(self, "_config", object()), "runtime", object())
        raw_runtime_env = getattr(runtime_env, "env", None)
        runtime_payload = raw_runtime_env if isinstance(raw_runtime_env, dict) else None
        return resolve_services_env(runtime_env=runtime_payload)

    def _resolve_identity_rendering_budgets(self) -> dict[str, int]:
        cached = getattr(self, "_identity_render_budgets", None)
        if isinstance(cached, dict) and cached:
            return dict(cached)

        identity_cfg = self._load_identity_ctl_config()
        resolved = {
            purpose: resolve_default_render_budget(purpose, identity_cfg=identity_cfg)
            for purpose in _CANONICAL_PURPOSES
        }
        self._identity_render_budgets = dict(resolved)
        return dict(resolved)

    def _load_identity_ctl_config(self):
        env = self._identity_env()
        roots = resolve_services_roots(env=env, home_root=self._home_root)
        try:
            return from_base_config(
                base_config=self._config,
                home_root=roots.home_root,
                data_root=roots.data_root,
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.debug(
                "identity runtime budget config load failed agent_id=%s reason=%s",
                self._identity_agent_id,
                exc,
            )
            from openminion.modules.identity.config import IdentityCtlConfig

            return IdentityCtlConfig()

    def _resolve_identity_render_budget_tokens(self, *, purpose: str) -> int:
        budgets = self._resolve_identity_rendering_budgets()
        normalized = normalize_purpose(purpose)
        fallback = int(budgets.get("act", 180) or 180)
        return int(budgets.get(normalized, fallback) or fallback)

    def _is_llm_policy_ref_enforced(self, llm_policy_ref: str) -> bool:
        _ = llm_policy_ref
        policy = getattr(self, "_security_policy", None)
        if policy is None:
            return False

        for attr in (
            "enforces_llm_policy_ref",
            "llm_policy_ref_enforced",
            "supports_llm_policy_ref",
        ):
            value = getattr(policy, attr, None)
            if isinstance(value, bool):
                return value

        checker = getattr(policy, "is_llm_policy_ref_enforced", None)
        if callable(checker):
            try:
                return bool(checker(llm_policy_ref))
            except TypeError:
                try:
                    return bool(checker())
                except Exception:  # noqa: BLE001
                    return False
            except Exception:  # noqa: BLE001
                return False
        return False

    def _update_llm_policy_ref_diagnostics(self, *, profile: Any) -> None:
        llm_policy_ref = str(getattr(profile, "llm_policy_ref", "") or "").strip()
        self._identity_llm_policy_ref = llm_policy_ref
        enforced = self._is_llm_policy_ref_enforced(llm_policy_ref)
        self._identity_llm_policy_ref_enforced = enforced
        if not llm_policy_ref or enforced:
            return

        warned_keys = set(getattr(self, "_identity_llm_policy_warned", set()) or set())
        warning_key = (
            str(getattr(profile, "agent_id", "") or self._identity_agent_id),
            str(getattr(profile, "profile_revision", "") or ""),
            llm_policy_ref,
        )
        if warning_key in warned_keys:
            return
        warned_keys.add(warning_key)
        self._identity_llm_policy_warned = warned_keys
        self._logger.warning(
            "identity llm_policy_ref present but unenforced agent_id=%s llm_policy_ref=%s",
            str(getattr(profile, "agent_id", "") or self._identity_agent_id),
            llm_policy_ref,
        )

    def _resolve_bundle_root(self) -> str:
        env_bundle_root = (
            self._identity_env().get(OPENMINION_IDENTITY_ROOT_ENV, "").strip()
        )
        if env_bundle_root:
            return str(Path(env_bundle_root).expanduser().resolve())
        configured_bundle_root = str(
            getattr(self._config.identity, "bundle_root", "")
        ).strip()
        if configured_bundle_root:
            return str(Path(configured_bundle_root).expanduser().resolve())
        legacy_root = str(getattr(self._config.identity, "root", "")).strip()
        if not legacy_root:
            return ""
        legacy_path = Path(legacy_root).expanduser()
        if legacy_path.suffix.lower() == ".db":
            return ""
        return str(legacy_path.resolve())

    def _resolve_startup_identity_root(self) -> Path:
        env = self._identity_env()
        env_root = env.get(OPENMINION_IDENTITY_ROOT_ENV, "").strip()
        if env_root:
            return resolve_identity_root_from_env(env=env, home_root=self._home_root)

        configured_root = str(getattr(self._config.identity, "root", "")).strip()
        if configured_root and Path(configured_root).suffix.lower() != ".db":
            candidate = Path(configured_root).expanduser()
            if candidate.is_absolute():
                return candidate.resolve(strict=False)
            return resolve_services_path(
                candidate,
                roots=resolve_services_roots(env=env, home_root=self._home_root),
            )

        return resolve_identity_root_from_env(env=env, home_root=self._home_root)

    @staticmethod
    def _discover_startup_yaml_profile_paths(identity_root: Path) -> list[Path]:
        if not identity_root.exists() or not identity_root.is_dir():
            return []
        profile_paths: list[Path] = []
        for child in sorted(identity_root.iterdir(), key=lambda item: item.name):
            if not child.is_dir():
                continue
            profile_yaml = child / "profile.yaml"
            if profile_yaml.is_file():
                profile_paths.append(profile_yaml.resolve())
        return profile_paths

    def _sync_startup_yaml_profiles(self) -> dict[str, Any]:
        summary: dict[str, Any] = {
            "status": IDENTITY_RUNTIME_STATUS_NOT_STARTED,
            "identity_root": "",
            "profile_files_count": 0,
            "upserted_profiles_count": 0,
            "errors_count": 0,
        }
        if self._identityctl is None:
            summary["status"] = IDENTITY_RUNTIME_STATUS_IDENTITYCTL_UNAVAILABLE
            return summary

        identity_root = self._resolve_startup_identity_root()
        summary["identity_root"] = str(identity_root)
        if not identity_root.exists():
            summary["status"] = IDENTITY_RUNTIME_STATUS_IDENTITY_ROOT_MISSING
            return summary
        if not identity_root.is_dir():
            summary["status"] = IDENTITY_RUNTIME_STATUS_IDENTITY_ROOT_NOT_DIRECTORY
            return summary

        profile_paths = self._discover_startup_yaml_profile_paths(identity_root)
        summary["profile_files_count"] = len(profile_paths)
        if not profile_paths:
            summary["status"] = IDENTITY_RUNTIME_STATUS_NO_YAML_PROFILES
            return summary

        upserted_ids: set[str] = set()
        errors: list[str] = []
        for profile_path in profile_paths:
            try:
                for loaded_id in self._identityctl.load_profiles_from_path(
                    profile_path,
                    skip_unchanged=True,
                ):
                    upserted_ids.add(str(loaded_id))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{profile_path}: {exc}")
                self._logger.debug(
                    "identity startup yaml sync failed path=%s reason=%s",
                    profile_path,
                    exc,
                )

        summary["upserted_profiles"] = sorted(upserted_ids)
        summary["upserted_profiles_count"] = len(upserted_ids)
        summary["errors_count"] = len(errors)
        if errors and upserted_ids:
            summary["status"] = IDENTITY_RUNTIME_STATUS_PARTIAL_FAILURE
            summary["errors"] = errors
            return summary
        if errors:
            summary["status"] = IDENTITY_RUNTIME_STATUS_SYNC_FAILED
            summary["errors"] = errors
            return summary
        summary["status"] = IDENTITY_RUNTIME_STATUS_SYNCED
        return summary

    @staticmethod
    def _classify_profile_source(profile: Any) -> str:
        if profile is None:
            return "missing"
        meta = dict(getattr(profile, "meta", {}) or {})
        explicit_source = str(meta.get("source", "") or "").strip().lower()
        if explicit_source:
            return explicit_source
        if str(meta.get("bundle_fingerprint", "") or "").strip():
            return "legacy-bundle"
        return "legacy-protected"

    def _identity_bundle_existing_profile_state(
        self, *, existing: Any, bundle: Any, summary: dict[str, Any]
    ) -> tuple[bool, int]:
        existing_meta = dict(getattr(existing, "meta", {}) or {}) if existing else {}
        source_classification = self._classify_profile_source(existing)
        summary["existing_source_classification"] = source_classification
        if existing is not None and source_classification not in {
            "bundle",
            "legacy-bundle",
        }:
            summary["status"] = IDENTITY_RUNTIME_STATUS_SKIPPED_AUTHORITY
            summary["skip_reason"] = f"source_classification={source_classification}"
            self._logger.debug(
                "identity bundle import skipped agent_id=%s reason=source_authority source=%s",
                self._identity_agent_id,
                source_classification,
            )
            return True, 1
        existing_fingerprint = str(existing_meta.get("bundle_fingerprint", "")).strip()
        if existing_fingerprint and existing_fingerprint == str(bundle.fingerprint):
            summary["status"] = IDENTITY_RUNTIME_STATUS_SKIPPED_UNCHANGED
            self._logger.debug(
                "identity bundle import skipped agent_id=%s reason=unchanged_fingerprint",
                self._identity_agent_id,
            )
            return True, 1
        next_revision = 1
        if existing is not None:
            try:
                next_revision = max(1, int(existing.profile_revision) + 1)
            except Exception:  # noqa: BLE001
                next_revision = 1
        return False, next_revision

    @staticmethod
    def _identity_bundle_documents(bundle: Any) -> list[Any]:
        from openminion.modules.identity.runtime.bundle_importer import (
            BundleTextDocument,
        )

        documents: list[Any] = []
        for item in [bundle.agent, bundle.soul, *list(bundle.skills)]:
            if item is None:
                continue
            path = Path(bundle.root_path) / item.relative_path
            if path.is_file():
                documents.append(
                    BundleTextDocument(
                        relative_path=item.relative_path,
                        content=path.read_text(encoding="utf-8", errors="ignore"),
                    )
                )
        return documents

    def _upsert_identity_bundle_profile(
        self,
        *,
        bundle: Any,
        documents: list[Any],
        next_profile_revision: int,
        summary: dict[str, Any],
    ) -> None:
        from openminion.modules.identity.runtime.bundle_importer import (
            build_profile_from_parsed_bundle,
            parse_bundle_documents,
        )

        parsed_bundle = parse_bundle_documents(documents)
        defaulted_fields: list[str] = []
        import_warnings = [str(value) for value in list(bundle.warnings)]
        if not str(parsed_bundle.mission).strip():
            defaulted_fields.append("role.mission")
            import_warnings.append(
                "missing AGENT.md section Mission; default role.mission applied"
            )
        if not parsed_bundle.voice:
            defaulted_fields.append("personality.tone")
            import_warnings.append(
                "missing SOUL.md section Voice; default personality.tone applied"
            )
        profile = build_profile_from_parsed_bundle(
            agent_id=self._identity_agent_id,
            parsed=parsed_bundle,
            profile_revision=next_profile_revision,
            display_name=self._identity_agent_id,
            system_prompt=_resolve_system_prompt(self._config),
        )
        meta = dict(getattr(profile, "meta", {}) or {})
        meta.update(
            {
                "bundle_fingerprint": str(bundle.fingerprint),
                "bundle_imported": True,
                "source": "bundle",
            }
        )
        if defaulted_fields:
            meta["bundle_import_defaulted_fields"] = list(defaulted_fields)
        if import_warnings:
            meta["bundle_import_warnings"] = list(import_warnings)
        profile = profile.model_copy(update={"meta": meta})
        self._identityctl.upsert_profile(
            profile,
            actor="identity-runtime",
            reason="bundle_import",
        )
        summary["status"] = IDENTITY_RUNTIME_STATUS_IMPORTED
        summary["imported"] = True
        summary["defaulted_fields_count"] = len(defaulted_fields)
        summary["warnings_count"] = len(import_warnings)
        summary["profile_revision"] = int(profile.profile_revision)
        if import_warnings or defaulted_fields:
            self._logger.debug(
                "identity bundle import defaults agent_id=%s defaulted=%s warnings=%s",
                self._identity_agent_id,
                defaulted_fields,
                import_warnings,
            )

    def _import_identity_bundle_profile(self) -> bool:
        summary: dict[str, Any] = {
            "status": IDENTITY_RUNTIME_STATUS_NOT_STARTED,
            "imported": False,
            "defaulted_fields_count": 0,
            "warnings_count": 0,
            "errors_count": 0,
        }
        if self._identityctl is None:
            summary["status"] = IDENTITY_RUNTIME_STATUS_IDENTITYCTL_UNAVAILABLE
            self._identity_import_summary = summary
            return False
        bundle_root = self._resolve_bundle_root()
        if not bundle_root:
            summary["status"] = IDENTITY_RUNTIME_STATUS_BUNDLE_ROOT_UNSET
            self._identity_import_summary = summary
            return False
        try:
            from openminion.services.agent.identity import load_identity_bundle

            bundle = load_identity_bundle(self._identity_agent_id, root=bundle_root)
            summary["bundle_root"] = str(bundle.root_path)
            if not bundle.ok:
                summary["status"] = IDENTITY_RUNTIME_STATUS_BUNDLE_INVALID
                summary["errors_count"] = len(list(bundle.errors))
                self._logger.debug(
                    "identity bundle import skipped agent_id=%s reason=bundle_not_ok errors=%s",
                    self._identity_agent_id,
                    list(bundle.errors),
                )
                self._identity_import_summary = summary
                return False
            existing = self._identityctl.get_profile(self._identity_agent_id)
            skip_import, next_profile_revision = (
                self._identity_bundle_existing_profile_state(
                    existing=existing,
                    bundle=bundle,
                    summary=summary,
                )
            )
            if skip_import:
                self._identity_import_summary = summary
                return True
            documents = self._identity_bundle_documents(bundle)
            if not documents:
                summary["status"] = IDENTITY_RUNTIME_STATUS_BUNDLE_EMPTY
                self._logger.debug(
                    "identity bundle import skipped agent_id=%s reason=no_documents",
                    self._identity_agent_id,
                )
                self._identity_import_summary = summary
                return False
            self._upsert_identity_bundle_profile(
                bundle=bundle,
                documents=documents,
                next_profile_revision=next_profile_revision,
                summary=summary,
            )
            self._identity_import_summary = summary
            return True
        except Exception as exc:  # noqa: BLE001
            summary["status"] = IDENTITY_RUNTIME_STATUS_IMPORT_FAILED
            summary["error"] = str(exc)
            self._logger.debug(
                "identity bundle import failed agent_id=%s reason=%s",
                self._identity_agent_id,
                exc,
            )
            self._identity_import_summary = summary
            return False

    def _resolve_identity_db_path(self) -> str:
        env = self._identity_env()
        env_path = env.get(OPENMINION_IDENTITY_DB_ENV, "").strip()
        if env_path:
            return env_path
        configured_path = str(getattr(self._config.identity, "db_path", "")).strip()
        if configured_path:
            candidate = Path(configured_path)
            if candidate.is_absolute():
                return str(candidate)
            roots = resolve_services_roots(env=env, home_root=self._home_root)
            return str(resolve_services_path(candidate, roots=roots))
        legacy_root = str(getattr(self._config.identity, "root", "")).strip()
        if legacy_root and Path(legacy_root).suffix.lower() == ".db":
            candidate = Path(legacy_root)
            if candidate.is_absolute():
                return str(candidate)
            roots = resolve_services_roots(env=env, home_root=self._home_root)
            return str(resolve_services_path(candidate, roots=roots))

        roots = resolve_services_roots(env=env, home_root=self._home_root)
        return str(
            resolve_services_path(
                Path(SERVICES_IDENTITY_SUBDIR) / SERVICES_IDENTITY_DB_FILENAME,
                roots=roots,
            )
        )

    def _init_identity_runtime(self) -> None:
        try:
            env = self._identity_env()
            env_path = env.get(OPENMINION_IDENTITY_DB_ENV, "").strip()
            configured_path = str(getattr(self._config.identity, "db_path", "")).strip()
            legacy_root = str(getattr(self._config.identity, "root", "")).strip()
            bundle_root = self._resolve_bundle_root()
            if (
                not env_path
                and not configured_path
                and not legacy_root
                and not bundle_root
            ):
                return
            from openminion.modules.identity.runtime.service import IdentityCtl
            from openminion.modules.identity.storage.store import SQLiteIdentityStore
            from openminion.services.identity.bootstrap import ensure_default_profile

            db_path = Path(self._resolve_identity_db_path())
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._identityctl = IdentityCtl(
                store=SQLiteIdentityStore(sqlite_path=str(db_path))
            )
            yaml_summary = self._sync_startup_yaml_profiles()
            self._identity_yaml_sync_summary = dict(yaml_summary or {})
            imported = self._import_identity_bundle_profile()
            existing_profile = self._identityctl.get_profile(self._identity_agent_id)
            fallback_applied = False
            if not imported and existing_profile is None:
                ensure_default_profile(
                    self._identityctl,
                    self._identity_agent_id,
                    system_prompt=_resolve_system_prompt(self._config),
                )
                fallback_applied = True
            import_summary = dict(getattr(self, "_identity_import_summary", {}) or {})
            self._logger.info(
                "identity startup sync agent_id=%s yaml_status=%s yaml_profiles=%s yaml_upserted=%s yaml_errors=%s status=%s imported=%s fallback_default=%s defaulted_fields=%s warnings=%s errors=%s",
                self._identity_agent_id,
                str(yaml_summary.get("status", "unknown")),
                int(yaml_summary.get("profile_files_count", 0) or 0),
                int(yaml_summary.get("upserted_profiles_count", 0) or 0),
                int(yaml_summary.get("errors_count", 0) or 0),
                str(import_summary.get("status", "unknown")),
                bool(import_summary.get("imported", False)),
                fallback_applied,
                int(import_summary.get("defaulted_fields_count", 0) or 0),
                int(import_summary.get("warnings_count", 0) or 0),
                int(import_summary.get("errors_count", 0) or 0),
            )
            self._refresh_identity_runtime_state()
        except Exception as exc:  # noqa: BLE001
            self._identityctl = None
            self._identity_tool_filter = None
            self._logger.debug(
                "identity runtime not active agent_id=%s reason=%s",
                self._identity_agent_id,
                exc,
            )

    def _refresh_identity_runtime_state(self) -> None:
        if self._identityctl is None:
            return
        try:
            profile = self._identityctl.get_profile(self._identity_agent_id)
            if profile is None:
                self._identity_llm_policy_ref = ""
                self._identity_llm_policy_ref_enforced = False
                return
            self._update_llm_policy_ref_diagnostics(profile=profile)
            self._identity_tool_filter = (
                profile.tool_posture.model_dump()
                if hasattr(profile.tool_posture, "model_dump")
                else None
            )
            if self._security_policy is not None and hasattr(
                self._security_policy, "update_identity_constraints"
            ):
                constraints = list(getattr(profile.role, "hard_constraints", []) or [])
                self._security_policy.update_identity_constraints(constraints)
        except Exception as exc:  # noqa: BLE001
            self._logger.debug(
                "failed to refresh identity runtime state agent_id=%s reason=%s",
                self._identity_agent_id,
                exc,
            )

    @staticmethod
    def _budget_value(budget: Any, key: str, default: int = 0) -> int:
        if budget is None:
            return int(default)
        if isinstance(budget, dict):
            value = budget.get(key, default)
            try:
                return int(value)
            except Exception:
                return int(default)
        value = getattr(budget, key, default)
        try:
            return int(value)
        except Exception:
            return int(default)

    def _identity_metadata(self) -> dict[str, str]:
        llm_policy_ref = str(
            getattr(self, "_identity_llm_policy_ref", "") or ""
        ).strip()
        llm_policy_ref_enforced = bool(
            getattr(self, "_identity_llm_policy_ref_enforced", False)
        )
        llm_policy_ref_warning = bool(llm_policy_ref) and (not llm_policy_ref_enforced)
        snippet = self._last_identity_snippet
        if snippet is None:
            return {
                "identity_profile_version": "none",
                "identity_render_version": "none",
                "identity_purpose": "none",
                "identity_budget_used_tokens": "0",
                "identity_budget_max_tokens": "0",
                "identity_llm_policy_ref": llm_policy_ref or "none",
                "identity_llm_policy_ref_enforced": (
                    "true"
                    if llm_policy_ref and llm_policy_ref_enforced
                    else "false"
                    if llm_policy_ref
                    else "none"
                ),
                "identity_llm_policy_ref_warning": "true"
                if llm_policy_ref_warning
                else "false",
            }
        budget = getattr(snippet, "budget", None)
        return {
            "identity_profile_version": str(
                getattr(snippet, "profile_version", "") or "none"
            ),
            "identity_render_version": str(
                getattr(snippet, "render_version", "") or "none"
            ),
            "identity_purpose": str(getattr(snippet, "purpose", "") or "act"),
            "identity_budget_used_tokens": str(
                self._budget_value(budget, "used_tokens", 0)
            ),
            "identity_budget_max_tokens": str(
                self._budget_value(budget, "max_tokens", 0)
            ),
            "identity_llm_policy_ref": llm_policy_ref or "none",
            "identity_llm_policy_ref_enforced": (
                "true"
                if llm_policy_ref and llm_policy_ref_enforced
                else "false"
                if llm_policy_ref
                else "none"
            ),
            "identity_llm_policy_ref_warning": "true"
            if llm_policy_ref_warning
            else "false",
        }

    def _inject_identity_system_prompt(
        self,
        *,
        system_prompt: str,
        inbound_metadata: dict[str, str] | None = None,
    ) -> str:
        if self._identityctl is None:
            return system_prompt
        render_purpose = self._resolve_identity_render_purpose(
            inbound_metadata=inbound_metadata
        )
        render_max_tokens = self._resolve_identity_render_budget_tokens(
            purpose=render_purpose
        )
        try:
            snippet = self._identityctl.render(
                agent_id=self._identity_agent_id,
                purpose=render_purpose,
                max_tokens=render_max_tokens,
            )
        except Exception as exc:  # noqa: BLE001
            self._logger.debug(
                "identity render failed agent_id=%s reason=%s",
                self._identity_agent_id,
                exc,
            )
            return system_prompt

        self._last_identity_snippet = snippet
        snippet_text = str(getattr(snippet, "text", "")).strip()
        if not snippet_text:
            return system_prompt

        framed_identity = f"{_IDENTITY_FRAME}{snippet_text}"
        if not system_prompt.strip():
            return framed_identity
        return f"{system_prompt}\n\n{framed_identity}".strip()


_AGENT_IDENTITY_RUNTIME_API_NAMES = (
    "_resolve_identity_render_purpose",
    "_identity_env",
    "_load_identity_ctl_config",
    "_resolve_identity_rendering_budgets",
    "_resolve_identity_render_budget_tokens",
    "_is_llm_policy_ref_enforced",
    "_update_llm_policy_ref_diagnostics",
    "_resolve_bundle_root",
    "_resolve_startup_identity_root",
    "_discover_startup_yaml_profile_paths",
    "_sync_startup_yaml_profiles",
    "_classify_profile_source",
    "_import_identity_bundle_profile",
    "_resolve_identity_db_path",
    "_init_identity_runtime",
    "_refresh_identity_runtime_state",
    "_budget_value",
    "_identity_metadata",
    "_inject_identity_system_prompt",
)


def bind_agent_identity_runtime_api(target_cls: type[Any]) -> type[Any]:
    """Attach the runtime identity API directly to a service class.

    This preserves the patch/import surface that tests expect on `AgentService`
    while removing the live MRO dependency on `AgentIdentityMixin`.
    """

    for name in _AGENT_IDENTITY_RUNTIME_API_NAMES:
        setattr(target_cls, name, AgentIdentityMixin.__dict__[name])
    return target_cls
