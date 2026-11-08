"""HTTP and WebSocket endpoints of m5-link.

    GET  /body          WebSocket for the M5 body (?token=...)
    GET  /              the web page (watch the body, type to talk)
    GET  /api/status    everything the page and the body MCP need to know
    GET  /api/body-diary?hours=24   what happened to the body, as the head reads it
    POST /api/chat      {"text"}: a conversation turn, as if typed to the body
    POST /api/say       {"text", "mood"?}: speak through the body, no turn
    POST /api/face      {"mood", "ms"?}
    POST /api/snapshot  take a picture; returns {"path"} under captures/
    POST /api/heartbeat run one heartbeat now
    POST /api/diary     {"date"?}: write that day's diary page now (normally 23:50)

The body needs the token. Everything else is open from this PC (loopback,
which is how the body MCP calls in) and needs the token from anywhere else.
"""

from __future__ import annotations

import asyncio
import hmac
import time
from datetime import date, datetime

from aiohttp import web

from .body import Body
from .config import PACKAGE_DIR, REPO_ROOT, LinkConfig
from .conversation import CAPTURE_DIR, DIARY_READER, Conversation
from .heartbeat import Heartbeat
from .nightly import Nightly

LOOPBACK = {"127.0.0.1", "::1"}


def build_app(config: LinkConfig, body: Body, conversation: Conversation, heartbeat: Heartbeat,
              nightly: Nightly, version: str) -> web.Application:
    token = config.server.token

    def has_token(request: web.Request) -> bool:
        presented = request.query.get("token") or request.headers.get("X-Link-Token", "")
        return bool(token) and hmac.compare_digest(presented, token)

    @web.middleware
    async def guard(request: web.Request, handler):
        if request.path == "/body":
            if not has_token(request):
                raise web.HTTPUnauthorized(text="bad token")
        elif request.remote not in LOOPBACK and not has_token(request):
            raise web.HTTPUnauthorized(text="open this page from the desktop, or add ?token=")
        return await handler(request)

    async def body_socket(request: web.Request) -> web.StreamResponse:
        return await body.serve(request)

    async def page(_: web.Request) -> web.FileResponse:
        return web.FileResponse(PACKAGE_DIR / "web" / "index.html")

    async def status(_: web.Request) -> web.Response:
        unread_notes, _ = conversation.diary.unread(DIARY_READER)
        return web.json_response({
            "now": time.time(),
            "version": version,
            "body": body.summary(),
            "head": conversation.head.summary(),
            "body_notes_unread": unread_notes,
            "desires": conversation.desires.levels(),
            "ears": {"device": conversation.ears.device, "model": conversation.ears.model_name},
            "mouth": {"ready": conversation.mouth.is_ready, "voice": conversation.mouth.speaker_name},
            "heartbeat": heartbeat.summary(),
            "conversation": conversation.recent_lines(),
        })

    async def body_diary(request: web.Request) -> web.Response:
        hours = float(request.query.get("hours", "24"))
        return web.json_response({"hours": hours, "text": conversation.diary.recent(hours)})

    async def chat(request: web.Request) -> web.Response:
        text = (await request.json()).get("text", "").strip()
        if not text:
            raise web.HTTPBadRequest(text="empty text")
        reply = await conversation.respond(text, source="文字")
        return web.json_response({"text": reply.text, "mood": reply.mood, "silent": reply.is_silent})

    async def say(request: web.Request) -> web.Response:
        payload = await request.json()
        spoken = await conversation.speak_unprompted(payload.get("text", ""), payload.get("mood"))
        return web.json_response({"spoken": spoken})

    async def face(request: web.Request) -> web.Response:
        payload = await request.json()
        shown = await body.show_face(payload.get("mood", "neutral"), int(payload.get("ms", 5000)))
        return web.json_response({"shown": shown})

    async def snapshot(_: web.Request) -> web.Response:
        try:
            jpeg = await body.take_snapshot()
        except (ConnectionError, TimeoutError, RuntimeError) as error:
            return web.json_response({"error": str(error)}, status=503)
        CAPTURE_DIR.mkdir(exist_ok=True)
        path = CAPTURE_DIR / f"see_{datetime.now():%Y%m%d_%H%M%S}.jpg"
        path.write_bytes(jpeg)
        return web.json_response({"path": path.relative_to(REPO_ROOT).as_posix(), "bytes": len(jpeg)})

    async def capture_file(request: web.Request) -> web.FileResponse:
        name = request.match_info["name"]
        path = CAPTURE_DIR / name
        if path.parent != CAPTURE_DIR or not path.exists():
            raise web.HTTPNotFound()
        return web.FileResponse(path)

    running_tasks: set[asyncio.Task] = set()  # keep a reference until each finishes

    async def write_diary(request: web.Request) -> web.Response:
        payload = await request.json() if request.can_read_body else {}
        day = date.fromisoformat(payload["date"]) if payload.get("date") else None
        return web.json_response({"page": await nightly.write_diary(day)})

    async def run_heartbeat(_: web.Request) -> web.Response:
        if heartbeat.is_running:
            return web.json_response({"started": False, "reason": "already running"})
        task = asyncio.create_task(heartbeat.beat())
        running_tasks.add(task)
        task.add_done_callback(running_tasks.discard)
        return web.json_response({"started": True})

    app = web.Application(middlewares=[guard])
    app.add_routes([
        web.get("/body", body_socket),
        web.get("/", page),
        web.get("/api/status", status),
        web.get("/api/body-diary", body_diary),
        web.post("/api/chat", chat),
        web.post("/api/say", say),
        web.post("/api/face", face),
        web.post("/api/snapshot", snapshot),
        web.get("/captures/{name}", capture_file),
        web.post("/api/heartbeat", run_heartbeat),
        web.post("/api/diary", write_diary),
    ])
    return app
