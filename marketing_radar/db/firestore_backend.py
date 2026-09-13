from __future__ import annotations

from typing import Iterable

from .backend import WhereClause, is_document_path


class FirestoreBackend:
    """google-cloud-firestore adapter. Imported lazily so offline runs never need the SDK."""

    def __init__(self, project_id: str | None = None, credentials_path: str | None = None) -> None:
        from google.cloud import firestore

        if credentials_path:
            from google.oauth2 import service_account

            creds = service_account.Credentials.from_service_account_file(credentials_path)
            self._client = firestore.Client(project=project_id or creds.project_id, credentials=creds)
        else:
            self._client = firestore.Client(project=project_id)
        self._firestore = firestore

    def get(self, doc_path: str) -> dict | None:
        # The parent's CONTEXT_PATH default (users/{uid}/context) is a collection in Firestore terms;
        # until the dashboard owner confirms the real path, read its first document.
        if not is_document_path(doc_path):
            docs = list(self._client.collection(doc_path).limit(1).stream())
            return docs[0].to_dict() if docs else None
        snap = self._client.document(doc_path).get()
        return snap.to_dict() if snap.exists else None

    def set(self, doc_path: str, data: dict, *, merge: bool = False) -> None:
        self._client.document(doc_path).set(data, merge=merge)

    def delete(self, doc_path: str) -> None:
        self._client.document(doc_path).delete()

    def list(
        self,
        collection_path: str,
        *,
        where: Iterable[WhereClause] | None = None,
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[tuple[str, dict]]:
        from google.cloud.firestore_v1.base_query import FieldFilter

        query = self._client.collection(collection_path)
        for field, op, value in where or ():
            query = query.where(filter=FieldFilter(field, op, value))
        if order_by:
            direction = self._firestore.Query.DESCENDING if descending else self._firestore.Query.ASCENDING
            query = query.order_by(order_by, direction=direction)
        if limit is not None:
            query = query.limit(limit)
        return [(snap.id, snap.to_dict() or {}) for snap in query.stream()]
