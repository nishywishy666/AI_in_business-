"""MemoryBackend persisted to one JSON file — for the offline CLI, never for production."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .backend import MemoryBackend


class JsonFileBackend(MemoryBackend):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            try:
                self._docs = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._docs = {}

    def set(self, doc_path: str, data: dict, *, merge: bool = False) -> None:
        super().set(doc_path, data, merge=merge)
        self._flush()

    def delete(self, doc_path: str) -> None:
        super().delete(doc_path)
        self._flush()

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._docs, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
