"""One-a-day packet of the whole `businesses/{id}` tree, cached on disk.

Reading the tree costs one Firestore read per document, and the dashboard's own polling used to pay
that price every minute: ~275 reads per load (a call's turns are a subcollection each) against a
50,000/day free tier, which one open tab exhausts in about three hours.

So the tree is fetched **once**, as a single packet of raw documents, written to disk, and taken
apart locally on every read after that. The marketing agent already works this way
(`marketing_radar/jobs/daily_pull.py` + its `LocalCache`); this is the same idea for the voice tree.

The packet is raw provider documents, not parsed records: breaking it down reuses the same
`from_doc` parsing the live path uses, so there is one definition of what a call is, and a packet
written by an older build still reads.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
PACKET_VERSION = 1


def _json_default(value: Any) -> Any:
    """Firestore hands back native values a JSON packet cannot hold — `DatetimeWithNanoseconds` for
    any Timestamp field, bytes for a blob. Everything exotic goes in as its ISO or string form,
    which is what the read side already parses (`parse_dt` accepts ISO strings)."""
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return str(value)


def default_packet_dir() -> Path:
    """`DASHBOARD_PACKET_DIR`, else a writable temp dir — Vercel's filesystem is read-only apart
    from /tmp, and a packet that cannot be written must degrade to a live read, not crash."""
    configured = os.environ.get("DASHBOARD_PACKET_DIR")
    if configured:
        return Path(configured)
    return Path(tempfile.gettempdir()) / "uncle-tony-packets"


class PacketStore:
    """One JSON file per business. Every failure is survivable: a missing, unreadable or malformed
    packet reads as "no packet", and an unwritable directory only costs the next read a live fetch."""

    def __init__(self, business_id: str, *, directory: Path | None = None,
                 max_age_hours: float = 24.0) -> None:
        self.business_id = business_id
        self.directory = Path(directory) if directory else default_packet_dir()
        self.max_age = dt.timedelta(hours=max_age_hours)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.business_id) or "business"
        return self.directory / f"{safe}.packet.json"

    def read(self) -> dict | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            log.warning("packet unreadable (%s); will re-fetch", exc)
            return None
        if not isinstance(data, dict) or data.get("version") != PACKET_VERSION:
            return None
        return data

    def write(self, packet: dict) -> bool:
        with self._lock:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(packet, default=_json_default), encoding="utf-8")
                tmp.replace(self.path)  # atomic, so a crash mid-write cannot leave half a packet
                return True
            except (OSError, TypeError, ValueError) as exc:
                # A packet that cannot be written costs the next read a live fetch. It must never
                # take the request down with it, so nothing here is allowed to escape.
                log.warning("could not write packet to %s: %s", self.path, exc)
                return False

    def delete(self) -> None:
        try:
            self.path.unlink()
        except OSError:
            pass

    def age(self, packet: dict, *, now: dt.datetime) -> dt.timedelta | None:
        fetched = packet.get("fetchedAt")
        if not fetched:
            return None
        try:
            at = dt.datetime.fromisoformat(str(fetched))
        except ValueError:
            return None
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        return now - at

    def is_fresh(self, packet: dict, *, now: dt.datetime) -> bool:
        age = self.age(packet, now=now)
        return age is not None and dt.timedelta(0) <= age < self.max_age

    # ---- patching, so a write does not cost a whole re-fetch ------------------------------------
    def patch(self, mutate) -> dict | None:
        """Apply `mutate(packet)` to the stored packet and write it back. Keeps the day's packet
        true after a callback is closed or a setting saved, instead of throwing it away."""
        packet = self.read()
        if packet is None:
            return None
        try:
            mutate(packet)
        except Exception as exc:  # a bad patch must never poison the packet
            log.warning("packet patch failed: %s", exc)
            return packet
        self.write(packet)
        return packet


def upsert(rows: list[dict[str, Any]], doc_id: str, fields: dict) -> list[dict[str, Any]]:
    """Merge `fields` into the `{"id", "doc"}` row with this id, appending one if it is new."""
    for row in rows:
        if row.get("id") == doc_id:
            row["doc"] = {**(row.get("doc") or {}), **fields}
            return rows
    rows.append({"id": doc_id, "doc": dict(fields)})
    return rows
