from abc import ABC, abstractmethod
from typing import Any


class SkillStore(ABC):
    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def upsert_skill(
        self,
        *,
        skill_id: str,
        name: str,
        status: str,
        scope: str,
        agent_id: str | None,
        ts: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def insert_skill_version(
        self,
        *,
        skill_id: str,
        version_hash: str,
        source_artifact_ref: str,
        package_json: str,
        created_at: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def upsert_skill_index(
        self,
        *,
        skill_id: str,
        version_hash: str,
        tags_json: str,
        tools_json: str,
        keywords_json: str,
        applies_to_json: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def get_skill_package(
        self, skill_id: str, version_hash: str | None = None
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def list_latest_skills(
        self,
        *,
        status_filter: list[str] | None = None,
        agent_id: str | None = None,
        scopes: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def list_skills(
        self,
        *,
        status_filter: list[str] | None = None,
        scope: str | None = None,
        agent_id: str | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def insert_skill_run(
        self,
        *,
        run_id: str,
        session_id: str,
        agent_id: str,
        skill_id: str,
        version_hash: str,
        used_for: str,
        outcome: str,
        evidence_refs_json: str,
        created_at: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def delete_skill(
        self,
        *,
        skill_id: str,
        version_hash: str | None = None,
    ) -> dict[str, int]:
        raise NotImplementedError

    @abstractmethod
    def create_proposal(
        self,
        *,
        proposal_id: str,
        source_task_shape_ref: str,
        proposer_policy_id: str,
        proposed_at: str,
        proposal_json: str,
        created_at: str,
    ) -> bool:
        """Insert a pending proposal row."""
        raise NotImplementedError

    @abstractmethod
    def list_proposals(
        self,
        *,
        queue_state: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_proposal(
        self,
        *,
        proposal_id: str,
    ) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def record_proposal_review(
        self,
        *,
        proposal_id: str,
        status: str,
        reviewer_id: str,
        review_policy_id: str,
        decided_at: str,
        review_json: str,
        created_at: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def apply_proposal(
        self,
        *,
        proposal_id: str,
        applied_at: str,
        applied_addition_json: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def record_suggestion_event(
        self,
        *,
        event_id: str,
        proposal_id: str,
        signature: str,
        event_type: str,
        reason: str | None,
        outcome: str | None,
        surfaced_at: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def latest_surfaced_at_for_signature(
        self,
        *,
        signature: str,
    ) -> str | None:
        raise NotImplementedError

    @abstractmethod
    def count_suggestion_events(self) -> dict[str, Any]:
        raise NotImplementedError
