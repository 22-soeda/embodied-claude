"""desktop-notify-mcp: a Windows desktop notification as the agent's voice.

This is for a body-less setup on Windows: no speaker, no camera, no Slack.
The only way to reach the person at this PC is a toast in the corner of the
screen, which stays in the Windows notification centre until it is dismissed.

Tool names matter to the individual-kernel gate (see "Adding a server" in
docs/setup.md):

- `notify` contains `__notify`, so it is an outward act. The PreToolUse hook
  lets it through only after a matching `propose_field_action`, and the
  boundary adapter treats it like a nudge.
- `list_recent_notifications` contains `__list_`, so it is read-only and can be
  called freely, e.g. to avoid saying the same thing twice.

Run from the repository root (Claude Code starts MCP servers there):

    uv run --no-sync python desktop-notify-mcp/server.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mcp.server.fastmcp import FastMCP

REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_PATH = REPO_ROOT / ".autonomous-logs" / "notify-history.jsonl"
TIMEZONE = ZoneInfo(os.environ.get("NOTIFY_TIMEZONE", "Asia/Tokyo"))
DEFAULT_TITLE = os.environ.get("NOTIFY_DEFAULT_TITLE", "embodied-claude")

# Borrow PowerShell's registered app id: a toast needs one, and registering our
# own is not worth it for this. The sender is shown as "Windows PowerShell".
POWERSHELL_APP_ID = (
    r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"
)

# Title and message travel as environment variables, not as script text, so no
# quoting of arbitrary text into PowerShell is needed.
TOAST_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$title = [System.Security.SecurityElement]::Escape($env:NOTIFY_TITLE)
$message = [System.Security.SecurityElement]::Escape($env:NOTIFY_MESSAGE)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$title</text><text>$message</text></binding></visual></toast>")
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:NOTIFY_APP_ID).Show($toast)
"""

CREATE_NO_WINDOW = 0x08000000

mcp = FastMCP("notify")


def show_toast(title: str, message: str) -> None:
    env = dict(os.environ)
    env.update(
        NOTIFY_TITLE=title,
        NOTIFY_MESSAGE=message,
        NOTIFY_APP_ID=POWERSHELL_APP_ID,
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", TOAST_SCRIPT],
        env=env,
        check=True,
        capture_output=True,
        timeout=30,
        creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def append_history(title: str, message: str) -> str:
    sent_at = datetime.now(TIMEZONE).isoformat(timespec="seconds")
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as history:
        record = {"sent_at": sent_at, "title": title, "message": message}
        history.write(json.dumps(record, ensure_ascii=False) + "\n")
    return sent_at


@mcp.tool()
def notify(message: str, title: str | None = None) -> str:
    """Speak to the person at this PC with a Windows desktop notification.

    This is the only voice this body has: there is no speaker. The toast shows
    in the corner of the screen and stays in the notification centre, so it
    also reaches someone who comes back to the PC later. They answer by opening
    Claude Code in this repository, not by clicking the toast.

    Keep it short, like a message on a phone lock screen. It is an outward act:
    propose it with propose_field_action first.

    Args:
        message: What to say. One or two sentences.
        title: Shown in bold above the message. Defaults to this agent's name.
    """
    title = title or DEFAULT_TITLE
    try:
        show_toast(title, message)
    except subprocess.CalledProcessError as error:
        stderr = error.stderr.decode("utf-8", errors="replace").strip()
        return f"notification failed: {stderr or error}"
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"notification failed: {error}"
    sent_at = append_history(title, message)
    return f"notified at {sent_at}: {title} / {message}"


@mcp.tool()
def list_recent_notifications(limit: int = 5) -> str:
    """List what this agent last said through notify, newest last.

    Read-only. Use it before notify so as not to repeat yourself, and to see
    how long ago you last spoke.
    """
    if not HISTORY_PATH.exists():
        return "no notifications sent yet"
    lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
    recent = [json.loads(line) for line in lines[-max(limit, 1) :] if line.strip()]
    return "\n".join(f"{r['sent_at']} {r['title']}: {r['message']}" for r in recent)


if __name__ == "__main__":
    mcp.run()
