"""Framework-agnostic handlers for the routes the parent may mount (spec §4). No server here.

    api = RadarApi(deps)
    status, body = api.brief()

Optional: `fastapi_router(api)` returns an APIRouter if FastAPI is installed in the parent.
"""
from __future__ import annotations

from typing import Any

from ..agent.chat import ChatSession
from ..agent.gemini import GeminiExhausted
from ..agent.like import PostNotFound, choose_angle, like_post
from ..clock import iso
from ..jobs.scan import ScanDeps
from .brief import get_brief, get_post
from .scripts import ScriptNotFound, list_scripts, save_script
from .stats import get_stats

Response = tuple[int, Any]


class RadarApi:
    def __init__(self, deps: ScanDeps) -> None:
        self.deps = deps

    # GET /api/brief, GET /api/brief/{scan_id}
    def brief(self, scan_id: str | None = None) -> Response:
        return self._guard(lambda: get_brief(self.deps.store, self.deps.cache, self.deps.settings,
                                             scan_id=scan_id, clock=self.deps.clock),
                           not_found="no brief yet — first scan scheduled")

    # GET /api/posts/{post_id}
    def post(self, post_id: str) -> Response:
        return self._guard(lambda: get_post(self.deps.store, post_id), not_found=f"post {post_id} not found")

    # GET /api/stats
    def stats(self) -> Response:
        return self._guard(lambda: get_stats(self.deps.store, self.deps.cache, self.deps.settings,
                                             clock=self.deps.clock), not_found="no usage snapshot yet")

    # POST /api/chat  {message, thread_id?, post_id?}
    def chat(self, message: str, *, thread_id: str = "default", post_id: str | None = None,
             retry: bool = False) -> Response:
        if not (message or "").strip():
            return 400, {"error": "message is required"}

        def run():
            reply = ChatSession(self.deps, thread_id).send(message, attached_post_id=post_id, retry=retry)
            return {"reply": reply.text, "thread_id": reply.thread_id, "actions": reply.actions,
                    "model_used": reply.model_used, "quality_warning": reply.quality_warning,
                    "scan_id": reply.scan_id, "retry_after": reply.retry_after}

        return self._guard(run)

    # POST /api/posts/{post_id}/like
    def like(self, post_id: str) -> Response:
        def run():
            result = like_post(self.deps, post_id)
            return {"post_id": result.post.post_id, "liked": True, "angles": result.angles,
                    "breakdown": result.breakdown, "transcript_source": result.transcript_source,
                    "model_used": result.model_used, "quality_warning": result.quality_warning}

        return self._guard(run)

    # POST /api/posts/{post_id}/angle  {angle: int | str}
    def choose_angle(self, post_id: str, angle: int | str) -> Response:
        return self._guard(lambda: choose_angle(self.deps, post_id, angle).to_doc())

    # POST /api/scripts/{script_id}/save
    def save_script(self, script_id: str) -> Response:
        return self._guard(lambda: save_script(self.deps.store, script_id, clock=self.deps.clock).to_doc())

    # GET /api/scripts?status=saved
    def scripts(self, status: str | None = "saved") -> Response:
        return self._guard(lambda: list_scripts(self.deps.store, status=status))

    # ---- error mapping (spec §18) -------------------------------------------------
    def _guard(self, fn, *, not_found: str | None = None) -> Response:
        try:
            body = fn()
        except (PostNotFound, ScriptNotFound) as exc:
            return 404, {"error": str(exc)}
        except ValueError as exc:
            return 400, {"error": str(exc)}
        except GeminiExhausted as exc:
            return 503, {"error": "gemini_exhausted", "resets_at": iso(exc.resets_at),
                         "note": "AI is paused until midnight Pacific; the last brief and ranked posts are still available."}
        except Exception as exc:  # Firestore down with a cold cache, transport failure, ...
            return 503, {"error": "marketing data unavailable", "detail": str(exc)[:200]}
        if body is None:
            return 404, {"error": not_found or "not found"}
        return 200, body


def fastapi_router(api: RadarApi):
    """Mount on the parent's FastAPI app: `app.include_router(fastapi_router(api), prefix="/api")`."""
    from fastapi import APIRouter, Body
    from fastapi.responses import JSONResponse

    router = APIRouter()

    def respond(result: Response) -> JSONResponse:
        status, body = result
        return JSONResponse(status_code=status, content=body)

    @router.get("/brief")
    def brief():
        return respond(api.brief())

    @router.get("/brief/{scan_id}")
    def brief_by_id(scan_id: str):
        return respond(api.brief(scan_id))

    @router.get("/posts/{post_id}")
    def post(post_id: str):
        return respond(api.post(post_id))

    @router.get("/stats")
    def stats():
        return respond(api.stats())

    @router.post("/chat")
    def chat(payload: dict = Body(...)):
        return respond(api.chat(payload.get("message", ""), thread_id=payload.get("thread_id", "default"),
                                post_id=payload.get("post_id"), retry=bool(payload.get("retry"))))

    @router.post("/posts/{post_id}/like")
    def like(post_id: str):
        return respond(api.like(post_id))

    @router.post("/posts/{post_id}/angle")
    def angle(post_id: str, payload: dict = Body(...)):
        return respond(api.choose_angle(post_id, payload.get("angle", 0)))

    @router.post("/scripts/{script_id}/save")
    def save(script_id: str):
        return respond(api.save_script(script_id))

    @router.get("/scripts")
    def scripts(status: str = "saved"):
        return respond(api.scripts(status))

    return router
