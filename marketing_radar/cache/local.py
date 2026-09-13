"""Local disk cache (spec §8). A cache, never the database.

Layout: <platformdirs user_cache_dir>/marketing_radar/<user_id>/
    context.json      ContextProfile
    brief.json        full ScanBrief
    brief_parts/      one JSON per split key
    stats.json        usage snapshot
    meta.json         { pulled_at, pulled_date, scan_id, schema_version }

A torn cache (any required file missing) is a cache miss. Files are written via tmp + os.replace
and meta.json is written last so a partial write never looks fresh.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import platformdirs

from .. import SCHEMA_VERSION
from ..clock import iso, local_date
from ..packets import ContextProfile, ScanBrief, UsageSnapshot

BRIEF_PART_KEYS = ("weekly_take", "global", "niche", "film_this", "playbook", "profile", "credits")


@dataclass
class CacheBundle:
    context: ContextProfile | None
    brief: ScanBrief | None
    stats: UsageSnapshot | None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def scan_id(self) -> str | None:
        return self.brief.scan_id if self.brief else None


class LocalCache:
    def __init__(self, user_id: str, root: Path | None = None) -> None:
        base = root if root is not None else Path(platformdirs.user_cache_dir("marketing_radar"))
        self.dir = Path(base) / user_id
        self.parts_dir = self.dir / "brief_parts"

    # ---- paths -------------------------------------------------------------------------
    @property
    def context_file(self) -> Path:
        return self.dir / "context.json"

    @property
    def brief_file(self) -> Path:
        return self.dir / "brief.json"

    @property
    def stats_file(self) -> Path:
        return self.dir / "stats.json"

    @property
    def meta_file(self) -> Path:
        return self.dir / "meta.json"

    def part_file(self, key: str) -> Path:
        return self.parts_dir / f"{key}.json"

    # ---- freshness ---------------------------------------------------------------------
    def read_meta(self) -> dict[str, Any] | None:
        return _read_json(self.meta_file)

    def files_present(self) -> bool:
        meta = self.read_meta()
        if meta is None or not self.context_file.exists():
            return False
        if meta.get("scan_id"):
            if not self.brief_file.exists():
                return False
            if not all(self.part_file(key).exists() for key in BRIEF_PART_KEYS):
                return False
        return True

    def is_fresh(self, now: dt.datetime) -> bool:
        meta = self.read_meta()
        if not meta or meta.get("schema_version") != SCHEMA_VERSION:
            return False
        return meta.get("pulled_date") == local_date(now) and self.files_present()

    # ---- read --------------------------------------------------------------------------
    def read(self) -> CacheBundle | None:
        if not self.files_present():
            return None
        meta = self.read_meta() or {}
        context_raw = _read_json(self.context_file)
        brief_raw = _read_json(self.brief_file)
        stats_raw = _read_json(self.stats_file)
        return CacheBundle(
            context=ContextProfile.model_validate(context_raw) if context_raw else None,
            brief=ScanBrief.model_validate(brief_raw) if brief_raw else None,
            stats=UsageSnapshot.model_validate(stats_raw) if stats_raw else None,
            meta=meta,
        )

    def read_part(self, key: str) -> Any:
        return _read_json(self.part_file(key))

    # ---- write -------------------------------------------------------------------------
    def write(self, *, context: ContextProfile, brief: ScanBrief | None, stats: UsageSnapshot | None,
              now: dt.datetime) -> CacheBundle:
        self.dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.context_file, context.to_doc())
        if brief is not None:
            self.write_brief(brief)
        else:
            self.brief_file.unlink(missing_ok=True)
            shutil.rmtree(self.parts_dir, ignore_errors=True)
        if stats is not None:
            _write_json(self.stats_file, stats.to_doc())
        meta = {
            "pulled_at": iso(now),
            "pulled_date": local_date(now),
            "scan_id": brief.scan_id if brief else None,
            "schema_version": SCHEMA_VERSION,
        }
        _write_json(self.meta_file, meta)
        return CacheBundle(context=context, brief=brief, stats=stats, meta=meta)

    def write_brief(self, brief: ScanBrief) -> None:
        """Write-through after our own scan: brief + split parts, and point meta at the new scan."""
        self.dir.mkdir(parents=True, exist_ok=True)
        self.parts_dir.mkdir(parents=True, exist_ok=True)
        doc = brief.to_doc()
        _write_json(self.brief_file, doc)
        for key in BRIEF_PART_KEYS:
            value = doc.get("playbook_delta") if key == "playbook" else doc.get(key)
            _write_json(self.part_file(key), value)
        meta = self.read_meta()
        if meta:
            meta["scan_id"] = brief.scan_id
            _write_json(self.meta_file, meta)

    def write_stats(self, stats: UsageSnapshot) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.stats_file, stats.to_doc())

    def write_context(self, context: ContextProfile) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.context_file, context.to_doc())

    def clear(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
