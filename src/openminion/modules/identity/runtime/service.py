import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from openminion.modules.identity.config import load_yaml_file
from openminion.modules.identity.runtime.generated_bundle import (
    materialize_generated_identity_bundle,
    resolve_generated_bundle_root_for_profile_path,
)
from openminion.modules.identity.interfaces import (
    IDENTITY_DEFAULT_RENDER_VERSION,
    IDENTITY_INTERFACE_VERSION,
)
from openminion.modules.identity.models import (
    AgentProfile,
    AgentProfileInput,
    AgentProfileSummary,
    IdentitySnippet,
    ValidationResult,
)
from openminion.modules.identity.runtime.renderer import (
    normalize_purpose,
    render_identity_snippet,
)
from openminion.modules.identity.storage.base import IdentityStore, StoredProfile


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        normalized = [_canonicalize(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ),
        )
    return value


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _ProfileInputFile:
    profile: AgentProfileInput
    source_path: Path
    materialize_bundle: bool = False


class IdentityCtl:
    contract_version = IDENTITY_INTERFACE_VERSION

    def __init__(
        self,
        *,
        store: IdentityStore,
        skillctl: Any | None = None,
        render_version: str = IDENTITY_DEFAULT_RENDER_VERSION,
        bullet_prefix: str = "- ",
        section_headers: bool = False,
    ) -> None:
        self.store = store
        self.skillctl = skillctl
        self.render_version = render_version
        self.bullet_prefix = bullet_prefix
        self.section_headers = section_headers

    @property
    def resolved_render_version(self) -> str:
        payload = {
            "render_version": self.render_version,
            "bullet_prefix": self.bullet_prefix,
            "section_headers": self.section_headers,
            "algorithm": "identityctl-render-v1",
            "section_order": [
                "role.mission",
                "role.hard_constraints",
                "risk",
                "tool_posture",
                "personality",
                "role.escalation_rules",
            ],
        }
        serialized = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return f"{self.render_version}:{_sha256(serialized)[:12]}"

    def close(self) -> None:
        self.store.close()

    def get_profile(self, agent_id: str) -> AgentProfile | None:
        try:
            return self._resolve_profile(agent_id)
        except ValueError:
            return None

    def list_profiles(self) -> list[AgentProfileSummary]:
        summaries: list[AgentProfileSummary] = []
        for row in self.store.list_profiles():
            resolved, version = self._resolved_profile_version(row)
            summaries.append(
                AgentProfileSummary(
                    agent_id=row.agent_id,
                    display_name=resolved.display_name,
                    profile_revision=resolved.profile_revision,
                    profile_version=version,
                    updated_at=row.updated_at,
                )
            )
        return summaries

    def upsert_profile(
        self,
        profile: AgentProfile,
        actor: str | None = None,
        reason: str | None = None,
    ) -> str:
        del actor, reason
        profile_version = self._compute_profile_version(profile)
        self.store.upsert_profile(profile, profile_version)
        self._refresh_versions()
        self.clear_cache(agent_id=profile.agent_id)
        refreshed = self.store.get_profile(profile.agent_id)
        if refreshed is None:
            raise RuntimeError("failed to reload profile after upsert")
        return refreshed.profile_version

    def delete_profile(self, agent_id: str) -> None:
        self.store.delete_profile(agent_id)
        self.clear_cache(agent_id=agent_id)
        self._refresh_versions()

    def render(
        self,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        max_chars: int | None = None,
        provider_pref: str | None = None,
        query_text: str | None = None,
    ) -> IdentitySnippet:
        resolved = self._resolve_profile(agent_id)
        profile_version = self._compute_profile_version(resolved)
        self._sync_profile_version(
            agent_id=resolved.agent_id, profile_version=profile_version
        )

        normalized_purpose = normalize_purpose(purpose)
        render_version = self.resolved_render_version
        cache_key = self._cache_key(
            agent_id=resolved.agent_id,
            purpose=normalized_purpose,
            profile_version=profile_version,
            render_version=render_version,
            max_tokens=max_tokens,
            max_chars=max_chars,
            provider_pref=provider_pref,
            query_text=query_text,
        )

        cached = self.store.get_cached_snippet(cache_key)
        effective_max_chars = max(1, int(max_chars or (max_tokens * 4)))
        if cached is not None:
            return IdentitySnippet(
                agent_id=resolved.agent_id,
                purpose=normalized_purpose,
                text=cached.snippet_text,
                profile_version=profile_version,
                render_version=render_version,
                budget={
                    "max_tokens": int(max_tokens),
                    "used_tokens": int(cached.used_tokens),
                    "max_chars": int(effective_max_chars),
                    "used_chars": int(cached.used_chars),
                },
                sections=dict(cached.sections) if cached.sections else None,
                included_fields=list(cached.included_fields),
                omitted_fields=list(cached.omitted_fields),
                warnings=list(cached.warnings),
            )

        snippet = render_identity_snippet(
            resolved,
            purpose=normalized_purpose,
            max_tokens=max(1, int(max_tokens)),
            max_chars=max_chars,
            render_version=render_version,
            profile_version=profile_version,
            bullet_prefix=self.bullet_prefix,
            section_headers=self.section_headers,
            skillctl=self.skillctl,
            query_text=query_text,
        )

        self.store.upsert_cached_snippet(
            cache_key=cache_key,
            snippet_text=snippet.text,
            used_tokens=snippet.budget.used_tokens,
            used_chars=snippet.budget.used_chars,
            sections=dict(snippet.sections or {}),
            included_fields=snippet.included_fields,
            omitted_fields=snippet.omitted_fields,
            warnings=snippet.warnings,
        )
        return snippet

    def render_from_profile(
        self,
        profile: AgentProfile,
        purpose: str,
        max_tokens: int,
        max_chars: int | None = None,
        provider_pref: str | None = None,
        query_text: str | None = None,
    ) -> IdentitySnippet:
        _ = provider_pref
        profile_version = self._compute_profile_version(profile)
        return render_identity_snippet(
            profile,
            purpose=normalize_purpose(purpose),
            max_tokens=max(1, int(max_tokens)),
            max_chars=max_chars,
            render_version=self.resolved_render_version,
            profile_version=profile_version,
            bullet_prefix=self.bullet_prefix,
            section_headers=self.section_headers,
            skillctl=self.skillctl,
            query_text=query_text,
        )

    def validate_profile(
        self, profile: AgentProfile | dict[str, Any]
    ) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        try:
            parsed = (
                profile
                if isinstance(profile, AgentProfile)
                else AgentProfile.model_validate(profile)
            )
        except ValidationError as exc:
            return ValidationResult(ok=False, errors=[str(exc)], warnings=[])

        if parsed.role.mission.count("\n") > 1:
            warnings.append("role.mission should be 1-2 lines")
        if not (3 <= len(parsed.role.responsibilities) <= 7):
            warnings.append("role.responsibilities recommended range is 3-7")
        if not (3 <= len(parsed.role.hard_constraints) <= 10):
            warnings.append("role.hard_constraints recommended range is 3-10")
        if not parsed.risk.confirm_before:
            warnings.append(
                "risk.confirm_before should usually include at least one category"
            )

        return ValidationResult(ok=not errors, errors=errors, warnings=warnings)

    def validate_render(
        self, snippet: IdentitySnippet | dict[str, Any]
    ) -> ValidationResult:
        try:
            parsed = (
                snippet
                if isinstance(snippet, IdentitySnippet)
                else IdentitySnippet.model_validate(snippet)
            )
        except ValidationError as exc:
            return ValidationResult(ok=False, errors=[str(exc)], warnings=[])

        errors: list[str] = []
        warnings: list[str] = []

        if parsed.budget.used_tokens > parsed.budget.max_tokens:
            errors.append("used_tokens exceeds max_tokens")
        if parsed.budget.used_chars > parsed.budget.max_chars:
            errors.append("used_chars exceeds max_chars")

        overlap = set(parsed.included_fields).intersection(set(parsed.omitted_fields))
        if overlap:
            warnings.append(f"included/omitted overlap: {sorted(overlap)}")

        return ValidationResult(ok=not errors, errors=errors, warnings=warnings)

    def warm_cache(
        self, agent_id: str, purposes: list[str] | None = None, max_tokens: int = 220
    ) -> int:
        purposes_to_warm = purposes or ["decide", "plan", "act", "reflect"]
        rendered = 0
        for purpose in purposes_to_warm:
            self.render(agent_id, purpose=purpose, max_tokens=max_tokens)
            rendered += 1
        return rendered

    def clear_cache(self, agent_id: str | None = None) -> None:
        self.store.clear_cache(agent_id=agent_id)

    def load_profiles_from_path(
        self,
        path: str | Path,
        *,
        skip_unchanged: bool = False,
    ) -> list[str]:
        profile_inputs = self._load_profile_inputs(path)
        loaded: list[str] = []

        resolved_profiles: dict[str, AgentProfile] = {}
        in_progress: set[str] = set()

        def resolve(agent_id: str) -> AgentProfile:
            if agent_id in resolved_profiles:
                return resolved_profiles[agent_id]
            if agent_id in in_progress:
                raise ValueError(f"inheritance cycle detected for profile: {agent_id}")
            if agent_id not in profile_inputs:
                raise ValueError(
                    f"profile not found for inheritance target: {agent_id}"
                )

            in_progress.add(agent_id)
            raw_input = profile_inputs[agent_id].profile
            patch = raw_input.model_dump(mode="python", exclude_none=True)
            base_payload: dict[str, Any] = {}
            parent = patch.get("inherits")
            if parent:
                base_payload = resolve(str(parent)).model_dump(
                    mode="python", exclude_none=True
                )

            merged_payload = _deep_merge(base_payload, patch)
            merged_payload["agent_id"] = agent_id
            merged_payload.setdefault("display_name", agent_id)
            merged_payload.setdefault("profile_revision", 1)
            existing_meta = merged_payload.get("meta")
            meta_payload = (
                dict(existing_meta) if isinstance(existing_meta, dict) else {}
            )
            meta_payload["source"] = "yaml"
            merged_payload["meta"] = meta_payload

            profile = AgentProfile.model_validate(merged_payload)
            resolved_profiles[agent_id] = profile
            in_progress.remove(agent_id)
            return profile

        for agent_id in sorted(profile_inputs):
            profile = resolve(agent_id)
            incoming_version = self._compute_profile_version(profile)
            if skip_unchanged:
                existing = self.get_profile(agent_id)
                if existing is not None:
                    existing_version = self._compute_profile_version(existing)
                    if existing_version == incoming_version:
                        self._materialize_generated_bundle_if_needed(
                            source_path=profile_inputs[agent_id].source_path,
                            materialize_bundle=profile_inputs[
                                agent_id
                            ].materialize_bundle,
                            profile=profile,
                            profile_version=incoming_version,
                        )
                        continue
            self.upsert_profile(profile)
            self._materialize_generated_bundle_if_needed(
                source_path=profile_inputs[agent_id].source_path,
                materialize_bundle=profile_inputs[agent_id].materialize_bundle,
                profile=profile,
                profile_version=incoming_version,
            )
            loaded.append(agent_id)

        return loaded

    def _load_profile_inputs(self, path: str | Path) -> dict[str, _ProfileInputFile]:
        src_path = Path(path).expanduser().resolve(strict=False)
        if not src_path.exists():
            raise FileNotFoundError(f"profile path not found: {src_path}")

        files = (
            [src_path]
            if src_path.is_file()
            else sorted([*src_path.glob("*.yaml"), *src_path.glob("*.yml")])
        )

        profile_inputs: dict[str, _ProfileInputFile] = {}
        for file_path in files:
            raw = load_yaml_file(file_path)
            payloads = self._extract_profile_payloads(raw)
            materialize_bundle = file_path.name == "profile.yaml" and len(payloads) == 1
            for payload in payloads:
                parsed = AgentProfileInput.model_validate(payload)
                agent_id = (parsed.agent_id or "").strip() or file_path.stem
                profile_inputs[agent_id] = _ProfileInputFile(
                    profile=parsed.model_copy(update={"agent_id": agent_id}),
                    source_path=file_path,
                    materialize_bundle=materialize_bundle,
                )
        return profile_inputs

    def _materialize_generated_bundle_if_needed(
        self,
        *,
        source_path: Path,
        materialize_bundle: bool,
        profile: AgentProfile,
        profile_version: str,
    ) -> None:
        if not materialize_bundle:
            return
        bundle_root = resolve_generated_bundle_root_for_profile_path(source_path)
        if bundle_root is None:
            return
        materialize_generated_identity_bundle(
            profile=profile,
            bundle_root=bundle_root,
            profile_version=profile_version,
        )

    def _extract_profile_payloads(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        if "profiles" not in raw:
            return [raw]

        payloads: list[dict[str, Any]] = []
        profiles = raw["profiles"]

        if isinstance(profiles, list):
            for item in profiles:
                if isinstance(item, dict):
                    payloads.append(item)
            return payloads

        if isinstance(profiles, dict):
            for agent_id, payload in profiles.items():
                if not isinstance(payload, dict):
                    continue
                item = dict(payload)
                item.setdefault("agent_id", str(agent_id))
                payloads.append(item)
            return payloads

        return [raw]

    def _refresh_versions(self) -> None:
        for row in self.store.list_profiles():
            self._resolved_profile_version(row)

    def _resolved_profile_version(self, row: StoredProfile) -> tuple[AgentProfile, str]:
        try:
            resolved = self._resolve_profile(row.agent_id)
        except ValueError:
            return row.profile, row.profile_version
        version = self._compute_profile_version(resolved)
        if version != row.profile_version:
            self.store.update_profile_version(row.agent_id, version)
        return resolved, version

    def _sync_profile_version(self, *, agent_id: str, profile_version: str) -> None:
        row = self.store.get_profile(agent_id)
        if row is None:
            return
        if row.profile_version != profile_version:
            self.store.update_profile_version(agent_id, profile_version)

    def _resolve_profile(
        self, agent_id: str, _stack: tuple[str, ...] = ()
    ) -> AgentProfile:
        if agent_id in _stack:
            chain = " -> ".join((*_stack, agent_id))
            raise ValueError(f"inheritance cycle detected: {chain}")

        row = self.store.get_profile(agent_id)
        if row is None:
            raise ValueError(f"profile not found: {agent_id}")
        profile = row.profile

        if not profile.inherits:
            return profile

        parent = self._resolve_profile(profile.inherits, _stack=(*_stack, agent_id))
        base_payload = parent.model_dump(mode="python", exclude_none=True)
        child_payload = profile.model_dump(mode="python", exclude_none=True)
        merged_payload = _deep_merge(base_payload, child_payload)
        merged_payload["agent_id"] = profile.agent_id
        merged_payload["inherits"] = profile.inherits
        return AgentProfile.model_validate(merged_payload)

    def _compute_profile_version(self, profile: AgentProfile) -> str:
        payload = profile.model_dump(mode="python", exclude_none=True)
        canonical = _canonicalize(payload)
        serialized = json.dumps(
            canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        return _sha256(serialized)

    def _cache_key(
        self,
        *,
        agent_id: str,
        purpose: str,
        profile_version: str,
        render_version: str,
        max_tokens: int,
        max_chars: int | None,
        provider_pref: str | None,
        query_text: str | None,
    ) -> str:
        return "|".join(
            [
                agent_id,
                purpose,
                profile_version,
                render_version,
                str(max(1, int(max_tokens))),
                "" if max_chars is None else str(max(1, int(max_chars))),
                (provider_pref or "").strip(),
                (query_text or "").strip().lower(),
            ]
        )
