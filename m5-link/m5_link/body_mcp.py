"""MCP server "body": lets a head session use the M5 body through m5-link.

Tool names are chosen for the individual-kernel gate, which classifies the
full name mcp__body__<tool> (see docs/setup.md, "Adding a server"):

    say, show_face          outward: need a propose_field_action first
    see                     read-only (contains "__see")
    get_body_state          read-only (contains "__get_")
    list_body_events        read-only (contains "__list_")

The server name "body" contains no outward fragment, so it does not drag
every tool to the outward side the way a server named "notify" did.

Run from the repository root: uv run --project m5-link --no-sync python -m m5_link.body_mcp
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime

from mcp.server.fastmcp import FastMCP, Image

from .config import REPO_ROOT, load_config

mcp = FastMCP("body")
LINK = f"http://127.0.0.1:{load_config().server.port}"


def _call(method: str, path: str, payload: dict | None = None, timeout: float = 60) -> dict:
    data = json.dumps(payload or {}).encode() if method == "POST" else None
    request = urllib.request.Request(LINK + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        return json.loads(error.read() or b"{}") | {"error": f"HTTP {error.code}"}
    except urllib.error.URLError as error:
        return {"error": f"m5-link is not running ({error.reason})"}


@mcp.tool()
def say(text: str, mood: str | None = None) -> str:
    """Speak out loud through the M5 body's speaker; the words also show on its screen.

    Use it when someone is likely near the body (see get_body_state: near,
    recent touches). Keep it to one or two short spoken sentences. Outward
    act: propose it with propose_field_action first.

    Args:
        text: What to say, in plain spoken Japanese.
        mood: Optional face while saying it: happy, sad, surprised, thinking,
              sleepy, love.
    """
    result = _call("POST", "/api/say", {"text": text, "mood": mood})
    if result.get("error"):
        return f"could not speak: {result['error']}"
    return "spoken" if result.get("spoken") else "the body is not connected; nothing was said"


@mcp.tool()
def show_face(mood: str, seconds: int = 5) -> str:
    """Change the body's face for a while: happy, sad, surprised, thinking, sleepy, love, neutral."""
    result = _call("POST", "/api/face", {"mood": mood, "ms": seconds * 1000})
    return "shown" if result.get("shown") else f"not shown: {result.get('error', 'body not connected')}"


@mcp.tool()
def see() -> list:
    """Take a picture with the body's camera (a small 320x240 camera under its screen) and look at it.

    The picture is also saved under captures/ in the repository, so it can
    be passed to save_visual_memory.
    """
    result = _call("POST", "/api/snapshot", timeout=20)
    if result.get("error"):
        return [f"could not see: {result['error']}"]
    path = REPO_ROOT / result["path"]
    taken_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    return [Image(path=str(path)), f"taken {taken_at}, saved as {result['path']}"]


@mcp.tool()
def get_body_state() -> str:
    """Whether the body is connected, its battery, Wi-Fi, and whether something is near its screen."""
    status = _call("GET", "/api/status")
    if status.get("error"):
        return status["error"]
    body = status["body"]
    return json.dumps(
        {"connected": body["connected"], "state": body["state"], "speaking": body["speaking"]},
        ensure_ascii=False,
    )


@mcp.tool()
def list_body_events(hours: float = 24) -> str:
    """What happened to the body in the last few hours, oldest first.

    Being picked up, shaken, someone coming near, the battery running low.
    Repeats close together are one line ("持ち上げられた（3 回）"). The
    heartbeat prompt already includes the part not yet seen; use this to
    look further back.
    """
    result = _call("GET", f"/api/body-diary?hours={hours}")
    if result.get("error"):
        return result["error"]
    return result["text"] or f"nothing happened to the body in the last {hours:g} hours"


if __name__ == "__main__":
    mcp.run()
