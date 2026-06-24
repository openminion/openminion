"""Project storage protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod

from openminion.modules.session.project.schemas import (
    Project,
    ProjectSessionBinding,
)


class ProjectStore(ABC):
    @abstractmethod
    def create(self, project: Project) -> Project: ...

    @abstractmethod
    def get(self, project_id: str) -> Project | None: ...

    @abstractmethod
    def list(self) -> list[Project]: ...

    @abstractmethod
    def delete(self, project_id: str) -> bool: ...

    @abstractmethod
    def bind_session(
        self, project_id: str, session_id: str
    ) -> ProjectSessionBinding: ...

    @abstractmethod
    def list_bindings_for_project(
        self, project_id: str
    ) -> list[ProjectSessionBinding]: ...

    @abstractmethod
    def project_for_session(self, session_id: str) -> Project | None: ...


__all__ = ["ProjectStore"]
