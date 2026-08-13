import logging
from pathlib import Path

from openminion.modules.identity import (
    IdentityBundle,
    IdentityDocument,
    load_identity_bundle,
)
from openminion.modules.context.schemas import IdentitySnippet


def _render_document(
    bundle_root: Path,
    document: IdentityDocument,
    heading: str,
) -> str:
    document_path = bundle_root / document.relative_path
    try:
        content = document_path.read_text(encoding="utf-8")
    except Exception:
        content = document.relative_path
    return f"{heading}\n\n{content}"


class IdentityBundleClient:
    def __init__(
        self,
        *,
        agent_id: str,
        root: str | Path | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._agent_id = agent_id
        self._root = root
        self._log = logger or logging.getLogger(__name__)
        self._bundle: IdentityBundle | None = None
        self._profile_version: str | None = None
        self._render_version: str | None = None

    def _ensure_bundle(self) -> IdentityBundle:
        if self._bundle is None:
            self._bundle = load_identity_bundle(
                agent_id=self._agent_id,
                root=str(self._root) if self._root is not None else None,
            )
            self._profile_version = f"bundle:{self._bundle.fingerprint[:12]}"
            self._render_version = "v1:real"
            if not self._bundle.ok:
                self._log.warning(
                    "identity_client: bundle has errors for agent_id=%s errors=%s",
                    self._agent_id,
                    self._bundle.errors,
                )
        return self._bundle

    def render(
        self,
        *,
        agent_id: str,
        purpose: str,
        max_tokens: int,
        provider_pref: str | None = None,
        query_text: str | None = None,
    ) -> "IdentitySnippet":
        del max_tokens, provider_pref, query_text
        bundle = self._ensure_bundle()

        if not bundle.ok:
            self._log.warning(
                "identity_client: using fallback for agent_id=%s due to bundle errors",
                agent_id,
            )
            return IdentitySnippet(
                agent_id=agent_id,
                purpose=purpose,
                text=f"Agent: {agent_id} (fallback - bundle incomplete)",
                profile_version="fallback:v1",
                render_version="fallback:v1",
            )

        text_parts: list[str] = []
        bundle_root = Path(bundle.root_path)

        if bundle.agent:
            text_parts.append(
                _render_document(bundle_root, bundle.agent, "# Agent Identity")
            )

        if bundle.soul:
            text_parts.append(
                _render_document(bundle_root, bundle.soul, "# Agent Soul")
            )

        if bundle.skills:
            text_parts.append(
                "# Skills\n\n"
                + "\n\n".join(
                    _render_document(bundle_root, skill, f"## {skill.relative_path}")
                    for skill in bundle.skills
                )
            )

        if bundle.notes:
            text_parts.append(
                "# Notes\n\n"
                + "\n\n".join(
                    _render_document(bundle_root, note, f"## {note.relative_path}")
                    for note in bundle.notes
                )
            )

        if not text_parts:
            text_parts.append(f"Agent: {agent_id}")

        return IdentitySnippet(
            agent_id=agent_id,
            purpose=purpose,
            text="\n\n".join(text_parts),
            profile_version=self._profile_version or "unknown",
            render_version=self._render_version or "unknown",
        )

    @property
    def bundle_ok(self) -> bool:
        return self._ensure_bundle().ok

    @property
    def fingerprint(self) -> str:
        return self._ensure_bundle().fingerprint

    @property
    def root_path(self) -> str:
        return self._ensure_bundle().root_path
