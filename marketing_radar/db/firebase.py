from __future__ import annotations

from typing import Iterable

from ..config import Settings
from .backend import Backend, MemoryBackend, WhereClause
from .paths import RadarPaths


class NamespaceViolation(PermissionError):
    """Raised on any write outside users/{uid}/marketingRadar/** or read outside that + context."""


class RadarStore:
    """Namespaced Firestore access. The only writer under users/{uid}/marketingRadar/**."""

    def __init__(self, user_id: str, backend: Backend, settings: Settings | None = None) -> None:
        if not user_id:
            raise ValueError("user_id is required")
        self.user_id = user_id
        self.paths = RadarPaths(user_id)
        self.backend = backend
        self.settings = settings or Settings()
        self._context_path = self.settings.context_path(user_id)

    def _guard_write(self, path: str) -> None:
        if not self.paths.is_inside_namespace(path):
            raise NamespaceViolation(f"refusing to write outside {self.paths.prefix}: {path}")

    def _guard_read(self, path: str) -> None:
        if path == self._context_path or self.paths.is_inside_namespace(path):
            return
        raise NamespaceViolation(f"refusing to read outside {self.paths.prefix}: {path}")

    def get(self, doc_path: str) -> dict | None:
        self._guard_read(doc_path)
        return self.backend.get(doc_path)

    def set(self, doc_path: str, data: dict, *, merge: bool = False) -> None:
        self._guard_write(doc_path)
        self.backend.set(doc_path, data, merge=merge)

    def update(self, doc_path: str, data: dict) -> None:
        self.set(doc_path, data, merge=True)

    def delete(self, doc_path: str) -> None:
        self._guard_write(doc_path)
        self.backend.delete(doc_path)

    def list(
        self,
        collection_path: str,
        *,
        where: Iterable[WhereClause] | None = None,
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[tuple[str, dict]]:
        self._guard_read(collection_path)
        return self.backend.list(
            collection_path, where=where, order_by=order_by, descending=descending, limit=limit
        )

    def exists(self, doc_path: str) -> bool:
        return self.get(doc_path) is not None

    def read_context(self) -> dict | None:
        """The one read outside the namespace: the parent's questionnaire context (read-only)."""
        return self.backend.get(self._context_path)

    @property
    def context_path(self) -> str:
        return self._context_path

    def ensure_initialized(self) -> None:
        if self.backend.get(self.paths.meta) is None:
            self.set(self.paths.meta, {"initialized": True})


def build_store(user_id: str, settings: Settings, backend: Backend | None = None) -> RadarStore:
    if backend is None:
        if settings.offline:
            backend = MemoryBackend()
        else:
            from .firestore_backend import FirestoreBackend

            backend = FirestoreBackend(settings.firebase_project_id, settings.firebase_credentials_json)
    return RadarStore(user_id, backend, settings)
