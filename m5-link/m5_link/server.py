"""HTTP and WebSocket endpoints of m5-link.

    GET  /body          WebSocket for the M5 body (?token=...)
    GET  /phone/        the phone page: the body taken outside (?token=...)
    GET  /phone/socket  its WebSocket, the M5's messages plus typed text (?token=...)
    GET  /phone/manifest.json, /phone/icon.png   for adding the page to the home screen
    GET  /              the web page (watch the body, type to talk)
    GET  /api/status    everything the page and the body MCP need to know
    GET  /api/body-diary?hours=24   what happened to the body, as the head reads it
    POST /api/chat      {"text"}: a conversation turn, as if typed to the body
    POST /api/say       {"text", "mood"?}: speak through the body the agent is in, no turn
    POST /api/face      {"mood", "ms"?}: on the body the agent is in
    POST /api/sleep     {"asleep": true|false}: the body sleeps (screen dim, sleeping face) or wakes
    POST /api/power_save {"on": true|false}: dimmer screen and fewer frames (PetitOnes' power save)
    POST /api/snapshot  take a picture with the M5; returns {"path"} under captures/ (not while on the phone)
    POST /api/heartbeat run one heartbeat now
    POST /api/diary     {"date"?}: write that day's diary page now (normally 23:50)
    POST /api/backup    zip the agent's own files and databases now (normally 04:30)

The bodies need the token. Everything else is open from this PC (loopback,
which is how the body MCP calls in) and needs the token from anywhere else.
Tailscale serve passes requests on from 127.0.0.1 with X-Forwarded-For, so
those count as from elsewhere. The manifest and the icon hold nothing private
and are open, since the phone fetches them without the page's address.
"""

from __future__ import annotations

import asyncio
import hmac
import html
import time
from datetime import date, datetime

from aiohttp import web

from .config import PACKAGE_DIR, REPO_ROOT, LinkConfig
from .conversation import CAPTURE_DIR, DIARY_READER, Conversation
from .heartbeat import Heartbeat
from .nightly import Nightly

LOOPBACK = {"127.0.0.1", "::1"}
SOCKETS = {"/body", "/phone/socket"}
OPEN_FILES = {"/phone/manifest.json", "/phone/icon.png"}


def build_app(config: LinkConfig, conversation: Conversation, heartbeat: Heartbeat,
              nightly: Nightly, version: str) -> web.Application:
    token = config.server.token
    presence = conversation.presence
    m5, phone = presence.bodies["m5"], presence.bodies["phone"]

    def has_token(request: web.Request) -> bool:
        presented = request.query.get("token") or request.headers.get("X-Link-Token", "")
        return bool(token) and hmac.compare_digest(presented, token)

    def is_on_this_pc(request: web.Request) -> bool:
        return request.remote in LOOPBACK and "X-Forwarded-For" not in request.headers

    @web.middleware
    async def guard(request: web.Request, handler):
        if request.path in OPEN_FILES:
            pass
        elif request.path in SOCKETS:
            if not has_token(request):
                raise web.HTTPUnauthorized(text="bad token")
        elif not is_on_this_pc(request) and not has_token(request):
            raise web.HTTPUnauthorized(text="open this page from the desktop, or add ?token=")
        return await handler(request)

    async def body_socket(request: web.Request) -> web.StreamResponse:
        return await m5.serve(request)

    async def phone_socket(request: web.Request) -> web.StreamResponse:
        return await phone.serve(request)

    async def page(_: web.Request) -> web.FileResponse:
        return web.FileResponse(PACKAGE_DIR / "web" / "index.html")

    async def to_phone_page(request: web.Request) -> web.Response:
        raise web.HTTPFound("/phone/" + (f"?{request.query_string}" if request.query_string else ""))

    async def phone_page(_: web.Request) -> web.Response:
        text = (PACKAGE_DIR / "web" / "phone.html").read_text(encoding="utf-8")
        return web.Response(text=text.replace("{{name}}", html.escape(config.phone.name)),
                            content_type="text/html", headers={"Cache-Control": "no-cache"})

    async def phone_manifest(_: web.Request) -> web.Response:
        # No start_url: the page's own address (with its ?token=) is where the home screen opens.
        return web.json_response({
            "name": config.phone.name, "short_name": config.phone.name, "display": "standalone",
            "background_color": "#ffffff", "theme_color": "#ffffff",
            "icons": [{"src": "icon.png", "sizes": "512x512", "type": "image/png"}],
        }, content_type="application/manifest+json")

    async def phone_icon(_: web.Request) -> web.FileResponse:
        return web.FileResponse(PACKAGE_DIR / "web" / "phone-icon.png")

    async def status(_: web.Request) -> web.Response:
        unread_notes, _ = conversation.diary.unread(DIARY_READER)
        return web.json_response({
            "now": time.time(),
            "version": version,
            "where": presence.where,
            "body": m5.summary(),
            "phone": phone.summary(),
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

    async def sleep_or_wake(request: web.Request) -> web.Response:
        asleep = bool((await request.json()).get("asleep", True))
        sent = await m5.send({"t": "sleep" if asleep else "wake"})
        return web.json_response({"sent": sent, "asleep": asleep})

    async def power_save(request: web.Request) -> web.Response:
        on = bool((await request.json()).get("on", True))
        return web.json_response({"sent": await m5.send({"t": "power_save", "on": on}), "on": on})

    async def face(request: web.Request) -> web.Response:
        payload = await request.json()
        shown = await conversation.body.show_face(payload.get("mood", "neutral"), int(payload.get("ms", 5000)))
        return web.json_response({"shown": shown})

    async def snapshot(_: web.Request) -> web.Response:
        if presence.where != "m5":  # the agent is in the phone; the M5 at home is empty
            return web.json_response({"error": "いまはスマホにいるので、家の M5 のカメラは使わない"}, status=409)
        try:
            jpeg = await m5.take_snapshot()
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

    async def back_up(_: web.Request) -> web.Response:
        path = await nightly.back_up()
        return web.json_response({"path": str(path) if path else None})

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
        web.get("/phone", to_phone_page),
        web.get("/phone/", phone_page),
        web.get("/phone/socket", phone_socket),
        web.get("/phone/manifest.json", phone_manifest),
        web.get("/phone/icon.png", phone_icon),
        web.get("/", page),
        web.get("/api/status", status),
        web.get("/api/body-diary", body_diary),
        web.post("/api/chat", chat),
        web.post("/api/say", say),
        web.post("/api/face", face),
        web.post("/api/sleep", sleep_or_wake),
        web.post("/api/power_save", power_save),
        web.post("/api/snapshot", snapshot),
        web.get("/captures/{name}", capture_file),
        web.post("/api/heartbeat", run_heartbeat),
        web.post("/api/diary", write_diary),
        web.post("/api/backup", back_up),
    ])
    return app
