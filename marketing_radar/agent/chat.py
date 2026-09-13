"""Marketing-section chat (spec §12.4): grounded in the cached brief, tight JSON tool protocol.

There is deliberately no scrape tool — chat can never spend a ScrapeCreators credit in v1.
"""
from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field

from ..clock import iso
from ..jobs.daily_pull import ContextMissing, daily_pull
from ..jobs.scan import ScanDeps
from ..packets import ScanBrief, TrendPacket
from ..usage import refresh_snapshot
from .gemini import GeminiExhausted
from .like import PostNotFound, like_post
from .prompts import CHAT_TOOLS, chat_prompt, chat_system, packet_summary

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
MAX_TOOL_ROUNDS = 2


@dataclass
class ChatReply:
    text: str
    thread_id: str
    actions: list[str] = field(default_factory=list)
    model_used: str | None = None
    quality_warning: str | None = None
    scan_id: str | None = None


class ChatSession:
    def __init__(self, deps: ScanDeps, thread_id: str = "default", *, history_limit: int = 12) -> None:
        self.deps = deps
        self.thread_id = thread_id
        self.history_limit = history_limit

    # ---- public --------------------------------------------------------------------
    def send(self, message: str, *, attached_post_id: str | None = None) -> ChatReply:
        deps = self.deps
        self._persist("user", message, post_id=attached_post_id)
        try:
            bundle = daily_pull(deps.store, deps.cache, deps.settings, deps.clock)
        except ContextMissing:
            return self._finish("Marketing data is not available yet — the main dashboard has not stored your "
                                "questionnaire, so there is no brief to talk about.", [])
        brief = bundle.brief
        attached = self._post(attached_post_id) if attached_post_id else None
        grounding = self._grounding(brief, attached)
        history = self.history()[:-1][-self.history_limit:]
        system = chat_system(bundle.context)

        actions: list[str] = []
        tool_result: dict | None = None
        model_used = quality_warning = None
        reply = ""
        try:
            for round_index in range(MAX_TOOL_ROUNDS + 1):
                result = deps.gemini.generate("chat", system=system,
                                              prompt=chat_prompt(history, grounding, message, tool_result))
                model_used, quality_warning = result.model_used, result.quality_warning
                parsed = _parse_reply(result.text)
                tool = parsed.get("tool")
                if tool and round_index < MAX_TOOL_ROUNDS and tool.get("name") in CHAT_TOOLS:
                    name, args = tool["name"], tool.get("args") or {}
                    tool_result = self._run_tool(name, args, attached, brief)
                    actions.append(name)
                    self._persist("tool", json.dumps({"name": name, "args": args}), post_id=attached_post_id)
                    continue
                reply = parsed.get("reply") or ""
                break
        except GeminiExhausted as exc:
            reply = (f"The AI assistant is paused until {iso(exc.resets_at)} (midnight Pacific) because today's "
                     f"free Gemini quota is used up. Your latest brief"
                     f"{' (' + brief.scan_id + ')' if brief else ''} and ranked posts are still available.")
        if not reply:
            reply = "I could not produce an answer from the current brief. Try rephrasing, or ask what to film next."
        refresh_snapshot(deps.store, deps.settings, deps.clock, cache=deps.cache)
        return self._finish(reply, actions, model_used=model_used, quality_warning=quality_warning,
                            scan_id=brief.scan_id if brief else None)

    def history(self) -> list[dict]:
        rows = self.deps.store.list(self.deps.store.paths.chat_messages(self.thread_id), order_by="at")
        return [data for _, data in rows]

    # ---- tools ---------------------------------------------------------------------
    def _run_tool(self, name: str, args: dict, attached: TrendPacket | None, brief: ScanBrief | None) -> dict:
        post_id = args.get("post_id") or (attached.post_id if attached else None)
        if name == "get_brief":
            return self._brief_summary(brief)
        if name == "recommend_tomorrow":
            summary = self._brief_summary(brief)
            playbook = [d.get("text") for _, d in self.deps.store.list(
                self.deps.store.paths.playbook, order_by="created_at", descending=True, limit=6)]
            return {"brief": summary, "playbook": playbook,
                    "next_scan_at": brief.to_doc().get("next_scan_at") if brief else None,
                    "instruction": "Recommend one concrete post for tomorrow: format, hook, length, caption idea."}
        if not post_id:
            return {"error": f"{name} needs a post_id; ask the creator which card they mean."}
        post = self._post(post_id)
        if post is None:
            return {"error": f"post {post_id} is not in this brief."}
        if name == "get_post":
            return {"post": packet_summary(post), "why_it_works": post.why_it_works, "format_guess": post.format_guess}
        if name == "rewrite_hook":
            return {"post": packet_summary(post), "patterns": brief.patterns if brief else [],
                    "instruction": "Write 3 alternative hooks for this creator's niche that keep the format."}
        if name == "captions":
            return {"post": packet_summary(post), "instruction": "Write exactly 3 caption options, each with 3-5 hashtags."}
        if name == "like_trend":
            try:
                liked = like_post(self.deps, post.post_id)
            except (PostNotFound, ValueError) as exc:
                return {"error": str(exc)}
            return {"liked": True, "post_id": liked.post.post_id, "breakdown": liked.breakdown,
                    "angles": liked.angles, "transcript_source": liked.transcript_source,
                    "instruction": "Present the 3 angles and ask which one to script."}
        return {"error": f"unknown tool {name}"}

    def _brief_summary(self, brief: ScanBrief | None) -> dict:
        if brief is None:
            return {"empty": True, "note": "No scan has run yet; the first scan is scheduled."}
        return {
            "scan_id": brief.scan_id, "kind": brief.kind, "weekly_take": brief.weekly_take,
            "patterns": brief.patterns, "ignore": brief.ignore,
            "global": [self._card(r.post_id) for r in brief.global_],
            "niche": [self._card(r.post_id) for r in brief.niche],
            "film_this": ({"post_id": brief.film_this.post_id, "why": brief.film_this.why,
                           **self._card(brief.film_this.post_id)} if brief.film_this else None),
            "credits": brief.credits.to_doc(),
        }

    def _card(self, post_id: str) -> dict:
        post = self._post(post_id)
        if post is None:
            return {"post_id": post_id}
        return {"post_id": post.post_id, "platform": post.platform, "hook": post.hook, "author": post.author,
                "likes": post.likes, "comments": post.comments, "shares": post.shares, "views": post.views,
                "why_it_works": post.why_it_works, "format_guess": post.format_guess, "final": post.final}

    def _grounding(self, brief: ScanBrief | None, attached: TrendPacket | None) -> dict:
        grounding = {"brief": self._brief_summary(brief)}
        if attached is not None:
            grounding["attached_post"] = packet_summary(attached)
        return grounding

    def _post(self, post_id: str | None) -> TrendPacket | None:
        if not post_id:
            return None
        doc = self.deps.store.get(self.deps.store.paths.post(post_id))
        return TrendPacket.model_validate(doc) if doc else None

    # ---- persistence -----------------------------------------------------------------
    def _persist(self, role: str, content: str, *, post_id: str | None = None, **extra) -> None:
        now = self.deps.clock()
        message_id = f"{now.strftime('%Y%m%dT%H%M%S')}_{uuid.uuid4().hex[:8]}"
        doc = {"role": role, "content": content, "at": iso(now), "post_id": post_id, **extra}
        self.deps.store.set(self.deps.store.paths.chat_message(self.thread_id, message_id), doc)

    def _finish(self, reply: str, actions: list[str], **extra) -> ChatReply:
        self._persist("assistant", reply, actions=actions, model_used=extra.get("model_used"))
        return ChatReply(text=reply, thread_id=self.thread_id, actions=actions, **extra)


def _parse_reply(text: str) -> dict:
    cleaned = _FENCE.sub("", text or "").strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1:
        try:
            data = json.loads(cleaned[start:end + 1])
            if isinstance(data, dict) and ("reply" in data or "tool" in data):
                return data
        except json.JSONDecodeError:
            pass
    return {"reply": cleaned, "tool": None}
