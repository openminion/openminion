import logging
from pathlib import Path

from openminion.services.agent.identity import IdentityBundle, load_identity_bundle

from openminion.modules.context.schemas import IdentitySnippet

logger = logging.getLogger(__name__)


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

        text_parts = []

        bundle_root = Path(bundle.root_path)

        if bundle.agent:
            agent_path = bundle_root / bundle.agent.relative_path
            try:
                content = agent_path.read_text(encoding="utf-8")
                text_parts.append(f"# Agent Identity\n\n{content}")
            except Exception:
                text_parts.append(f"# Agent Identity\n\n{bundle.agent.relative_path}")

        if bundle.soul:
            soul_path = bundle_root / bundle.soul.relative_path
            try:
                content = soul_path.read_text(encoding="utf-8")
                text_parts.append(f"# Agent Soul\n\n{content}")
            except Exception:
                text_parts.append(f"# Agent Soul\n\n{bundle.soul.relative_path}")

        if bundle.skills:
            skills_content = []
            for skill in bundle.skills:
                skill_path = bundle_root / skill.relative_path
                try:
                    content = skill_path.read_text(encoding="utf-8")
                    skills_content.append(f"## {skill.relative_path}\n\n{content}")
                except Exception:
                    skills_content.append(f"## {skill.relative_path}")
            if skills_content:
                text_parts.append("# Skills\n\n" + "\n\n".join(skills_content))

        if bundle.notes:
            notes_content = []
            for note in bundle.notes:
                note_path = bundle_root / note.relative_path
                try:
                    content = note_path.read_text(encoding="utf-8")
                    notes_content.append(f"## {note.relative_path}\n\n{content}")
                except Exception:
                    notes_content.append(f"## {note.relative_path}")
            if notes_content:
                text_parts.append("# Notes\n\n" + "\n\n".join(notes_content))

        if not text_parts:
            text_parts.append(f"Agent: {agent_id}")

        rendered_text = "\n\n".join(text_parts)

        return IdentitySnippet(
            agent_id=agent_id,
            purpose=purpose,
            text=rendered_text,
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
