"""Firestore client + path helpers for the businesses/{businessId} tree (vr_plan.md §11).

The client is created lazily from FIREBASE_SA_JSON (base64 or raw JSON) or, when
FIRESTORE_EMULATOR_HOST is set, against the emulator. Nothing here writes; writes go through
services/voice/sinks.py so the simulator's LocalJsonlSink can exist without a client at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .config import VoiceConfig, decode_sa_json, get_config


@dataclass(frozen=True)
class BusinessPaths:
    business_id: str

    @property
    def root(self) -> str:
        return f"businesses/{self.business_id}"

    @property
    def menu_items(self) -> str:
        return f"{self.root}/menuItems"

    def menu_item(self, item_id: str) -> str:
        return f"{self.menu_items}/{item_id}"

    @property
    def facts(self) -> str:
        return f"{self.root}/facts"

    def fact(self, fact_key: str) -> str:
        return f"{self.facts}/{fact_key}"

    @property
    def capacity_slots(self) -> str:
        return f"{self.root}/capacitySlots"

    def capacity_slot(self, slot_id: str) -> str:
        return f"{self.capacity_slots}/{slot_id}"

    @property
    def bookings(self) -> str:
        return f"{self.root}/bookings"

    def booking(self, idempotency_key: str) -> str:
        return f"{self.bookings}/{idempotency_key}"

    @property
    def callbacks(self) -> str:
        return f"{self.root}/callbacks"

    @property
    def calls(self) -> str:
        return f"{self.root}/calls"

    def call(self, call_id: str) -> str:
        return f"{self.calls}/{call_id}"

    def turns(self, call_id: str) -> str:
        return f"{self.call(call_id)}/turns"

    def turn(self, call_id: str, turn_index: int) -> str:
        return f"{self.turns(call_id)}/{turn_index}"

    @property
    def unanswered(self) -> str:
        return f"{self.root}/unanswered"

    @property
    def usage_events(self) -> str:
        return f"{self.root}/usageEvents"

    @property
    def emails_sent(self) -> str:
        return f"{self.root}/emailsSent"

    def email_sent(self, idempotency_key: str) -> str:
        return f"{self.emails_sent}/{idempotency_key}"

    @property
    def rollups(self) -> str:
        return f"{self.root}/rollups"

    def rollup(self, local_date: str) -> str:
        return f"{self.rollups}/{local_date}"


def make_client(config: VoiceConfig | None = None) -> Any:
    """google.cloud.firestore.Client for the configured project. Imported lazily."""
    from google.cloud import firestore

    config = config or get_config()
    if config.firestore_emulator_host:
        import os

        os.environ.setdefault("FIRESTORE_EMULATOR_HOST", config.firestore_emulator_host)
        return firestore.Client(project=config.firebase_project_id)
    from google.oauth2 import service_account

    creds = service_account.Credentials.from_service_account_info(decode_sa_json(config.firebase_sa_json))
    return firestore.Client(project=config.firebase_project_id, credentials=creds)


@lru_cache(maxsize=1)
def get_client() -> Any:
    return make_client()


def paths(business_id: str | None = None) -> BusinessPaths:
    return BusinessPaths(business_id or get_config().business_id)
