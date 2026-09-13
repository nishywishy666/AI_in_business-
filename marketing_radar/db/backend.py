from __future__ import annotations

import copy
import operator
from typing import Any, Iterable, Protocol

WhereClause = tuple[str, str, Any]

_OPS = {
    "==": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}


def is_document_path(path: str) -> bool:
    return len(path.strip("/").split("/")) % 2 == 0


class Backend(Protocol):
    def get(self, doc_path: str) -> dict | None: ...

    def set(self, doc_path: str, data: dict, *, merge: bool = False) -> None: ...

    def delete(self, doc_path: str) -> None: ...

    def list(
        self,
        collection_path: str,
        *,
        where: Iterable[WhereClause] | None = None,
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[tuple[str, dict]]: ...


class MemoryBackend:
    """Dict-backed Firestore stand-in for tests and offline runs."""

    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}

    def get(self, doc_path: str) -> dict | None:
        data = self._docs.get(doc_path)
        return copy.deepcopy(data) if data is not None else None

    def set(self, doc_path: str, data: dict, *, merge: bool = False) -> None:
        payload = copy.deepcopy(data)
        if merge and doc_path in self._docs:
            self._docs[doc_path].update(payload)
        else:
            self._docs[doc_path] = payload

    def delete(self, doc_path: str) -> None:
        self._docs.pop(doc_path, None)

    def list(
        self,
        collection_path: str,
        *,
        where: Iterable[WhereClause] | None = None,
        order_by: str | None = None,
        descending: bool = False,
        limit: int | None = None,
    ) -> list[tuple[str, dict]]:
        prefix = collection_path.rstrip("/") + "/"
        rows: list[tuple[str, dict]] = []
        for path, data in self._docs.items():
            if not path.startswith(prefix):
                continue
            doc_id = path[len(prefix):]
            if "/" in doc_id:
                continue
            if where and not all(_matches(data, clause) for clause in where):
                continue
            rows.append((doc_id, copy.deepcopy(data)))
        if order_by:
            rows.sort(key=lambda row: _sort_key(row[1].get(order_by)), reverse=descending)
        else:
            rows.sort(key=lambda row: row[0])
        if limit is not None:
            rows = rows[:limit]
        return rows

    def all_paths(self) -> list[str]:
        return sorted(self._docs)


def _matches(data: dict, clause: WhereClause) -> bool:
    field, op, value = clause
    if op not in _OPS:
        raise ValueError(f"unsupported operator: {op}")
    current = data.get(field)
    if current is None:
        return False
    try:
        return _OPS[op](current, value)
    except TypeError:
        return False


def _sort_key(value: Any) -> tuple[int, Any]:
    if value is None:
        return (0, "")
    if isinstance(value, (int, float)):
        return (1, value)
    return (2, str(value))
