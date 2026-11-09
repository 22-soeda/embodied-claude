"""Run the autonomous heartbeat (autonomous-action.sh) on a schedule.

This replaces the Task Scheduler entry from scripts/windows/register-heartbeat.ps1
when m5-link is running: one process to start at logon instead of two. The
heartbeat itself is unchanged - a separate `claude -p` with the EFPF hooks -
so it keeps the primary-session slot to itself; the conversation session in
head.py runs without hooks and never competes for it.

Beats are aligned to the clock (:00 for 60 minutes), because the script's own
schedule control only lets off-peak beats through at minute 00.

Before each beat, the desires grown strong (see desires.py) are written to
state/desire-digest.txt and passed as DESIRE_DIGEST_FILE, and the unread part
of the body diary is written to
state/body-digest.txt and passed as BODY_DIGEST_FILE. The script puts it in the
prompt and deletes the file; only then does it count as read. A beat the
schedule turns away leaves the file, and the notes wait for the next one.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from .body_diary import BodyDiary
from .config import MCP_STARTUP_TIMEOUT_MS, REPO_ROOT, STATE_DIR, HeartbeatConfig
from .desires import Desires

log = logging.getLogger(__name__)

GIT_BASH = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
BEAT_TIMEOUT_S = 30 * 60
BODY_DIGEST_PATH = STATE_DIR / "body-digest.txt"
DESIRE_DIGEST_PATH = STATE_DIR / "desire-digest.txt"
DIARY_READER = "heartbeat"  # this session's read mark in the body diary


class Heartbeat:
    def __init__(self, config: HeartbeatConfig, diary: BodyDiary, desires: Desires,
                 companion_name: str) -> None:
        self._config = config
        self._companion_name = companion_name  # the script names the companion in its rules
        self._diary = diary
        self._desires = desires
        self.last_started: datetime | None = None
        self.last_exit_code: int | None = None
        self.is_running = False

    def next_beat_after(self, now: datetime) -> datetime:
        interval = self._config.interval_minutes
        minutes_since_hour = now.minute + now.second / 60
        next_slot = (int(minutes_since_hour // interval) + 1) * interval
        return now.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=next_slot)

    async def run_forever(self) -> None:
        if not self._config.enabled:
            log.info("heartbeat disabled in m5-link.toml")
            return
        while True:
            next_beat = self.next_beat_after(datetime.now())
            log.info("next heartbeat at %s", next_beat.strftime("%H:%M"))
            await asyncio.sleep((next_beat - datetime.now()).total_seconds() + 1)
            await self.beat()

    async def beat(self) -> None:
        if self.is_running:
            log.warning("the previous heartbeat is still running; skipping this one")
            return
        script = REPO_ROOT / "autonomous-action.sh"
        if not script.exists():
            log.error("autonomous-action.sh is missing; run scripts/windows/configure_v0.py")
            return

        self.is_running = True
        self.last_started = datetime.now()
        notes_until = self._write_body_digest()
        await self._desires.update()  # fresh levels for this beat, not up to five minutes old
        self._desires.write_digest(DESIRE_DIGEST_PATH)
        try:
            process = await asyncio.create_subprocess_exec(
                str(GIT_BASH), "-l", "-c", _posix_path(script),
                cwd=REPO_ROOT,
                env=_heartbeat_environment() | {
                    "BODY_DIGEST_FILE": _posix_path(BODY_DIGEST_PATH),
                    "DESIRE_DIGEST_FILE": _posix_path(DESIRE_DIGEST_PATH),
                    "COMPANION_NAME": self._companion_name,
                },
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=0x08000000 if sys.platform == "win32" else 0,  # no console window
            )
            self.last_exit_code = await asyncio.wait_for(process.wait(), BEAT_TIMEOUT_S)
            log.info("heartbeat finished with exit code %s", self.last_exit_code)
        except TimeoutError:
            log.error("heartbeat ran over %d minutes; killing it", BEAT_TIMEOUT_S // 60)
            process.kill()
        finally:
            self.is_running = False
            if notes_until is not None and not BODY_DIGEST_PATH.exists():
                self._diary.mark_read(DIARY_READER, notes_until)  # the script took the notes into its prompt

    def _write_body_digest(self) -> float | None:
        """Write the unread body notes for the script; return the time they reach up to."""
        notes, notes_until = self._diary.unread(DIARY_READER)
        if not notes:
            BODY_DIGEST_PATH.unlink(missing_ok=True)
            return None
        STATE_DIR.mkdir(exist_ok=True)
        BODY_DIGEST_PATH.write_text(notes + "\n", encoding="utf-8")
        return notes_until

    def summary(self) -> dict:
        return {
            "enabled": self._config.enabled,
            "running": self.is_running,
            "last_started": self.last_started.isoformat() if self.last_started else None,
            "last_exit_code": self.last_exit_code,
            "next": self.next_beat_after(datetime.now()).isoformat() if self._config.enabled else None,
        }


def _posix_path(path: Path) -> str:
    """C:\\Users\\me\\x.sh -> /c/Users/me/x.sh, as Git Bash expects."""
    drive, rest = path.drive, path.as_posix()[len(path.drive):]
    return f"/{drive.rstrip(':').lower()}{rest}"


def _heartbeat_environment() -> dict[str, str]:
    """Our environment minus what would trip up `claude -p`.

    CLAUDE* variables: `claude -p` refuses to start inside another Claude Code
    session's environment. The claude.ai connectors (Notion, Calendar, Drive...)
    are switched off: the heartbeat lives on the project's own MCP servers and
    is not allowed to call the connectors anyway.
    """
    environment = {key: value for key, value in os.environ.items() if not key.startswith("CLAUDE")}
    environment["ENABLE_CLAUDEAI_MCP_SERVERS"] = "false"
    environment["MCP_TIMEOUT"] = MCP_STARTUP_TIMEOUT_MS
    return environment
