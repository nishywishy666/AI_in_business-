"""Script lifecycle (spec §9.3): drafts expire after 14 days, saved scripts live until deleted."""
from __future__ import annotations

from ..clock import Clock, utc_now
from ..db import RadarStore
from ..packets import Script


class ScriptNotFound(KeyError):
    pass


def get_script(store: RadarStore, script_id: str) -> Script:
    doc = store.get(store.paths.script(script_id))
    if doc is None:
        raise ScriptNotFound(script_id)
    return Script.model_validate(doc)


def save_script(store: RadarStore, script_id: str, *, clock: Clock = utc_now) -> Script:
    script = get_script(store, script_id)
    saved = script.model_copy(update={"status": "saved", "saved_at": clock(), "expires_at": None})
    store.set(store.paths.script(script_id), saved.to_doc())
    return saved


def list_scripts(store: RadarStore, *, status: str | None = "saved") -> list[dict]:
    where = [("status", "==", status)] if status else None
    rows = store.list(store.paths.scripts, where=where)
    return sorted((data for _, data in rows), key=lambda d: d.get("created_at") or "", reverse=True)


def delete_script(store: RadarStore, script_id: str) -> None:
    script = get_script(store, script_id)
    store.delete(store.paths.script(script_id))
    post = store.get(store.paths.post(script.post_id))
    if post and post.get("script_id") == script_id:
        store.update(store.paths.post(script.post_id), {"script_id": None})
