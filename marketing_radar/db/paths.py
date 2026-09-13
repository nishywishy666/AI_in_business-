"""Single source of truth for the Firestore layout.

Concrete layout (spec §6.1). `marketingRadar` is a collection under the parent's user doc.
Top-level lifecycle docs sit directly in it; everything that needs collection queries
hangs off the `meta` marker doc, and usage counters hang off the `usage` snapshot doc:

    users/{uid}/marketingRadar/meta                       marker doc {initialized}
    users/{uid}/marketingRadar/latest                     pointer
    users/{uid}/marketingRadar/contextCache               parsed context snapshot
    users/{uid}/marketingRadar/usage                      usage snapshot
    users/{uid}/marketingRadar/usage/events/{eventId}
    users/{uid}/marketingRadar/usage/geminiDaily/{pacificDate}
    users/{uid}/marketingRadar/meta/scans/{scanId}
    users/{uid}/marketingRadar/meta/posts/{postId}
    users/{uid}/marketingRadar/meta/scripts/{scriptId}
    users/{uid}/marketingRadar/meta/scrapeCache/{requestHash}
    users/{uid}/marketingRadar/meta/playbook/{entryId}
    users/{uid}/marketingRadar/meta/notifications/{alertId}
    users/{uid}/marketingRadar/meta/chat/{threadId}/messages/{msgId}

Changing the parent schema later means changing this file only.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RadarPaths:
    user_id: str

    @property
    def user_doc(self) -> str:
        return f"users/{self.user_id}"

    @property
    def prefix(self) -> str:
        return f"{self.user_doc}/marketingRadar"

    @property
    def meta(self) -> str:
        return f"{self.prefix}/meta"

    @property
    def latest(self) -> str:
        return f"{self.prefix}/latest"

    @property
    def context_cache(self) -> str:
        return f"{self.prefix}/contextCache"

    @property
    def usage_snapshot(self) -> str:
        return f"{self.prefix}/usage"

    @property
    def usage_events(self) -> str:
        return f"{self.usage_snapshot}/events"

    def usage_event(self, event_id: str) -> str:
        return f"{self.usage_events}/{event_id}"

    @property
    def gemini_daily_collection(self) -> str:
        return f"{self.usage_snapshot}/geminiDaily"

    def gemini_daily(self, pacific_date: str) -> str:
        return f"{self.gemini_daily_collection}/{pacific_date}"

    @property
    def scans(self) -> str:
        return f"{self.meta}/scans"

    def scan(self, scan_id: str) -> str:
        return f"{self.scans}/{scan_id}"

    @property
    def posts(self) -> str:
        return f"{self.meta}/posts"

    def post(self, post_id: str) -> str:
        return f"{self.posts}/{post_id}"

    @property
    def scripts(self) -> str:
        return f"{self.meta}/scripts"

    def script(self, script_id: str) -> str:
        return f"{self.scripts}/{script_id}"

    @property
    def scrape_cache(self) -> str:
        return f"{self.meta}/scrapeCache"

    def scrape_cache_entry(self, request_hash: str) -> str:
        return f"{self.scrape_cache}/{request_hash}"

    @property
    def playbook(self) -> str:
        return f"{self.meta}/playbook"

    def playbook_entry(self, entry_id: str) -> str:
        return f"{self.playbook}/{entry_id}"

    @property
    def notifications(self) -> str:
        return f"{self.meta}/notifications"

    def notification(self, alert_id: str) -> str:
        return f"{self.notifications}/{alert_id}"

    def chat_messages(self, thread_id: str) -> str:
        return f"{self.meta}/chat/{thread_id}/messages"

    def chat_message(self, thread_id: str, message_id: str) -> str:
        return f"{self.chat_messages(thread_id)}/{message_id}"

    def packet_ref(self, post_id: str) -> str:
        """Relative reference used inside ScanBrief (`posts/{postId}`)."""
        return f"posts/{post_id}"

    def is_inside_namespace(self, path: str) -> bool:
        return path == self.prefix or path.startswith(self.prefix + "/")
